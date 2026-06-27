from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog
from playwright.async_api import BrowserContext, Page, Request, async_playwright

from src.models import TLSSessionData

logger = structlog.get_logger(__name__)

TOKEN_TTL_MINUTES = 50

_EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="email"]',
    'input[id*="email"]',
    'input[placeholder*="Email" i]',
    'input[formcontrolname="email"]',
]

_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,800",
]


class TLSSession:
    def __init__(self, email: str, password: str, headless: bool = True) -> None:
        self._email = email
        self._password = password
        self._headless = headless
        self._sessions: dict[str, TLSSessionData] = {}
        self._lock = asyncio.Lock()

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

    def _is_valid(self, session: TLSSessionData) -> bool:
        age = datetime.utcnow() - session.acquired_at
        return age < timedelta(minutes=TOKEN_TTL_MINUTES)

    async def _find_and_fill(self, page: Page, selectors: list[str], value: str, label: str) -> None:
        for sel in selectors:
            try:
                await page.wait_for_selector(sel, timeout=5_000, state="visible")
                await page.fill(sel, value)
                logger.debug("field_filled", label=label, selector=sel)
                return
            except Exception:
                continue
        await page.screenshot(path=f"data/debug_tls_{label}.png", full_page=True)
        raise RuntimeError(f"Could not find {label} field on TLS page. Screenshot saved.")

    async def _login(self, tls_country: str, portal_url: str) -> TLSSessionData:
        logger.info("tls_login_start", tls_country=tls_country, url=portal_url)

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self._headless,
                args=_STEALTH_ARGS,
            )
            context: BrowserContext = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-GB",
                timezone_id="Europe/London",
                extra_http_headers={
                    "Accept-Language": "en-GB,en;q=0.9",
                },
            )

            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page: Page = await context.new_page()

            try:
                await page.goto(portal_url, wait_until="domcontentloaded", timeout=60_000)
                await asyncio.sleep(3)
                await page.wait_for_load_state("networkidle", timeout=20_000)

                # Click login button if present (some TLS pages have a landing page first)
                login_btn = page.locator(
                    'a[href*="login"], button:has-text("Login"), button:has-text("Sign in")'
                )
                if await login_btn.count() > 0:
                    await login_btn.first.click()
                    await asyncio.sleep(2)
                    await page.wait_for_load_state("networkidle", timeout=15_000)

                await self._find_and_fill(page, _EMAIL_SELECTORS, self._email, "email")

                password_selectors = [
                    'input[type="password"]',
                    'input[name="password"]',
                    'input[formcontrolname="password"]',
                ]
                await self._find_and_fill(page, password_selectors, self._password, "password")

                submit_selectors = [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'button:has-text("Sign in")',
                    'button:has-text("Log in")',
                    'button:has-text("Login")',
                ]
                for sel in submit_selectors:
                    try:
                        await page.click(sel, timeout=5_000)
                        break
                    except Exception:
                        continue

                await asyncio.sleep(2)
                await page.wait_for_load_state("networkidle", timeout=30_000)

                cookies_list = await context.cookies()
                captured_cookies = {c["name"]: c["value"] for c in cookies_list}

            finally:
                await browser.close()

        logger.info("tls_login_success", tls_country=tls_country, cookie_count=len(captured_cookies))
        return TLSSessionData(cookies=captured_cookies)

    async def discover_endpoints(
        self, tls_country: str, portal_url: str, duration_seconds: int = 30
    ) -> list[dict[str, Any]]:
        intercepted: list[dict[str, Any]] = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False, args=_STEALTH_ARGS)
            context: BrowserContext = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            async def _capture(request: Request) -> None:
                if request.resource_type in ("fetch", "xhr"):
                    intercepted.append({
                        "method": request.method,
                        "url": request.url,
                        "headers": dict(request.headers),
                    })

            context.on("request", _capture)
            page: Page = await context.new_page()
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            await page.goto(portal_url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(3)
            await page.wait_for_load_state("networkidle", timeout=20_000)

            login_btn = page.locator('a[href*="login"], button:has-text("Login"), button:has-text("Sign in")')
            if await login_btn.count() > 0:
                await login_btn.first.click()
                await asyncio.sleep(2)
                await page.wait_for_load_state("networkidle", timeout=15_000)

            await self._find_and_fill(page, _EMAIL_SELECTORS, self._email, "email")
            password_selectors = ['input[type="password"]', 'input[name="password"]']
            await self._find_and_fill(page, password_selectors, self._password, "password")
            await page.click('button[type="submit"]')

            logger.info("tls_discover_waiting", tls_country=tls_country, seconds=duration_seconds)
            await asyncio.sleep(duration_seconds)
            await browser.close()

        return intercepted
