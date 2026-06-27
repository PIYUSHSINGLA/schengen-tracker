from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog
from playwright.async_api import BrowserContext, Page, Request, async_playwright

from src.models import TLSSessionData

logger = structlog.get_logger(__name__)

TOKEN_TTL_MINUTES = 50


class TLSSession:
    """Manages a Playwright-based TLScontact session.

    TLScontact uses cookie-based auth.  One session covers all four TLS
    countries because each subdomain shares the same auth backend.
    """

    def __init__(self, email: str, password: str, headless: bool = True) -> None:
        self._email = email
        self._password = password
        self._headless = headless
        self._sessions: dict[str, TLSSessionData] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_session(self, tls_country: str, portal_url: str) -> TLSSessionData:
        async with self._lock:
            existing = self._sessions.get(tls_country)
            if existing and self._is_valid(existing):
                return existing
            session = await self._login(tls_country, portal_url)
            self._sessions[tls_country] = session
            return session

    def invalidate(self, tls_country: str) -> None:
        self._sessions.pop(tls_country, None)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_valid(self, session: TLSSessionData) -> bool:
        age = datetime.utcnow() - session.acquired_at
        return age < timedelta(minutes=TOKEN_TTL_MINUTES)

    async def _login(self, tls_country: str, portal_url: str) -> TLSSessionData:
        logger.info("tls_login_start", tls_country=tls_country, url=portal_url)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=self._headless)
            context: BrowserContext = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
            )
            page: Page = await context.new_page()

            try:
                await page.goto(portal_url, wait_until="networkidle", timeout=60_000)

                # TLScontact login can be a modal or a separate page
                # Try clicking a login button first
                login_btn = page.locator(
                    'a[href*="login"], button:has-text("Login"), button:has-text("Sign in")'
                )
                if await login_btn.count() > 0:
                    await login_btn.first.click()
                    await page.wait_for_load_state("networkidle", timeout=15_000)

                await page.fill('input[type="email"], input[name="email"], input[id*="email"]', self._email)
                await page.fill('input[type="password"]', self._password)
                await page.click('button[type="submit"], input[type="submit"]')
                await page.wait_for_load_state("networkidle", timeout=30_000)

                cookies_list = await context.cookies()
                captured_cookies = {c["name"]: c["value"] for c in cookies_list}

            finally:
                await browser.close()

        logger.info("tls_login_success", tls_country=tls_country, cookie_count=len(captured_cookies))
        return TLSSessionData(cookies=captured_cookies)

    # ------------------------------------------------------------------
    # Discovery mode
    # ------------------------------------------------------------------

    async def discover_endpoints(
        self, tls_country: str, portal_url: str, duration_seconds: int = 30
    ) -> list[dict[str, Any]]:
        intercepted: list[dict[str, Any]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            context: BrowserContext = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            async def _capture(request: Request) -> None:
                if request.resource_type in ("fetch", "xhr"):
                    intercepted.append(
                        {
                            "method": request.method,
                            "url": request.url,
                            "headers": dict(request.headers),
                        }
                    )

            context.on("request", _capture)
            page: Page = await context.new_page()

            await page.goto(portal_url, wait_until="networkidle", timeout=60_000)

            login_btn = page.locator('a[href*="login"], button:has-text("Login"), button:has-text("Sign in")')
            if await login_btn.count() > 0:
                await login_btn.first.click()
                await page.wait_for_load_state("networkidle", timeout=15_000)

            await page.fill('input[type="email"], input[name="email"], input[id*="email"]', self._email)
            await page.fill('input[type="password"]', self._password)
            await page.click('button[type="submit"], input[type="submit"]')

            logger.info("tls_discover_waiting", tls_country=tls_country, seconds=duration_seconds)
            await asyncio.sleep(duration_seconds)
            await browser.close()

        return intercepted
