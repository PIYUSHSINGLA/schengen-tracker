from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

import structlog
from playwright.async_api import (
    BrowserContext,
    Page,
    Request,
    async_playwright,
)

from src.models import VFSSessionData

logger = structlog.get_logger(__name__)

# Token considered stale after 50 minutes (VFS tokens expire ~1 hour)
TOKEN_TTL_MINUTES = 50
# Regex to extract Bearer token from Authorization header
_BEARER_RE = re.compile(r"Bearer\s+(\S+)", re.IGNORECASE)


class VFSSession:
    """Manages a single Playwright-based VFS Global session.

    One instance is shared across all VFS countries.  The bearer token is
    obtained by logging in once and then intercepting the Authorization header
    on the first authenticated XHR request after login.
    """

    def __init__(self, email: str, password: str, headless: bool = True) -> None:
        self._email = email
        self._password = password
        self._headless = headless
        self._session: VFSSessionData | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_session(self, country_url: str) -> VFSSessionData:
        async with self._lock:
            if self._is_valid():
                return self._session  # type: ignore[return-value]
            self._session = await self._login(country_url)
            return self._session

    def invalidate(self) -> None:
        self._session = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_valid(self) -> bool:
        if self._session is None:
            return False
        age = datetime.utcnow() - self._session.acquired_at
        return age < timedelta(minutes=TOKEN_TTL_MINUTES)

    async def _login(self, country_url: str) -> VFSSessionData:
        login_url = country_url.rstrip("/") + "/login"
        logger.info("vfs_login_start", url=login_url)

        captured_token: list[str] = []
        captured_cookies: dict[str, str] = {}

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

            async def _on_request(request: Request) -> None:
                auth_header = request.headers.get("authorization", "")
                m = _BEARER_RE.search(auth_header)
                if m and not captured_token:
                    captured_token.append(m.group(1))
                    logger.debug("vfs_bearer_captured", url=request.url)

            context.on("request", _on_request)
            page: Page = await context.new_page()

            try:
                await page.goto(login_url, wait_until="networkidle", timeout=60_000)

                # Fill credentials
                await page.fill('input[type="email"], input[name="username"], input[id*="email"]', self._email)
                await page.fill('input[type="password"]', self._password)
                await page.click('button[type="submit"], input[type="submit"]')

                # Wait for authenticated state — network idle or token capture
                for _ in range(30):
                    await asyncio.sleep(1)
                    if captured_token:
                        break

                if not captured_token:
                    logger.warning("vfs_bearer_not_captured_via_header", login_url=login_url)
                    # Fallback: try to extract from localStorage / sessionStorage
                    token_js: str | None = await page.evaluate(
                        """() => {
                            const keys = Object.keys(localStorage);
                            for (const k of keys) {
                                const v = localStorage.getItem(k);
                                if (v && v.length > 50 && !v.startsWith('{')) return v;
                            }
                            return null;
                        }"""
                    )
                    if token_js:
                        captured_token.append(token_js)

                cookies_list = await context.cookies()
                captured_cookies = {c["name"]: c["value"] for c in cookies_list}

            finally:
                await browser.close()

        if not captured_token:
            raise RuntimeError("VFS login failed: could not capture Bearer token")

        logger.info("vfs_login_success", token_prefix=captured_token[0][:12] + "...")
        return VFSSessionData(
            bearer_token=captured_token[0],
            cookies=captured_cookies,
        )

    # ------------------------------------------------------------------
    # Discovery mode
    # ------------------------------------------------------------------

    async def discover_endpoints(self, country_url: str, duration_seconds: int = 30) -> list[dict[str, Any]]:
        """Run login, then intercept all XHR/fetch requests for `duration_seconds`."""
        login_url = country_url.rstrip("/") + "/login"
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

            await page.goto(login_url, wait_until="networkidle", timeout=60_000)
            await page.fill('input[type="email"], input[name="username"], input[id*="email"]', self._email)
            await page.fill('input[type="password"]', self._password)
            await page.click('button[type="submit"], input[type="submit"]')

            logger.info("vfs_discover_waiting", seconds=duration_seconds)
            await asyncio.sleep(duration_seconds)
            await browser.close()

        return intercepted
