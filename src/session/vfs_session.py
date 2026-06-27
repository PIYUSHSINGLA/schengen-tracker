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

TOKEN_TTL_MINUTES = 50
_BEARER_RE = re.compile(r"Bearer\s+(\S+)", re.IGNORECASE)

# Selectors tried in order for the email field
_EMAIL_SELECTORS = [
    'input[type="email"]',
    'input[name="username"]',
    'input[name="email"]',
    'input[id*="email"]',
    'input[placeholder*="Email" i]',
    'input[placeholder*="Username" i]',
    'input[formcontrolname="username"]',
    'input[formcontrolname="email"]',
]

_STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,800",
]


class VFSSession:
    def __init__(self, email: str, password: str, headless: bool = True) -> None:
        self._email = email
        self._password = password
        self._headless = headless
        self._session: VFSSessionData | None = None
        self._lock = asyncio.Lock()

    async def get_session(self, country_url: str) -> VFSSessionData:
        async with self._lock:
            if self._is_valid():
                return self._session  # type: ignore[return-value]
            self._session = await self._login(country_url)
            return self._session

    def invalidate(self) -> None:
        self._session = None

    def _is_valid(self) -> bool:
        if self._session is None:
            return False
        age = datetime.utcnow() - self._session.acquired_at
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
        # Take screenshot to help diagnose what page is actually showing
        await page.screenshot(path=f"data/debug_{label}.png", full_page=True)
        raise RuntimeError(f"Could not find {label} field. Screenshot saved to data/debug_{label}.png")

    async def _login(self, country_url: str) -> VFSSessionData:
        login_url = country_url.rstrip("/") + "/login"
        logger.info("vfs_login_start", url=login_url)

        captured_token: list[str] = []
        captured_cookies: dict[str, str] = {}

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

            # Mask webdriver flag
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
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
                # Navigate — use domcontentloaded first, then wait for form
                await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)

                # If Cloudflare challenge, wait up to 20s for it to resolve
                await asyncio.sleep(3)
                await page.wait_for_load_state("networkidle", timeout=20_000)

                await self._find_and_fill(page, _EMAIL_SELECTORS, self._email, "email")

                password_selectors = ['input[type="password"]', 'input[name="password"]', 'input[formcontrolname="password"]']
                await self._find_and_fill(page, password_selectors, self._password, "password")

                submit_selectors = ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Sign in")', 'button:has-text("Log in")', 'button:has-text("Login")']
                for sel in submit_selectors:
                    try:
                        await page.click(sel, timeout=5_000)
                        break
                    except Exception:
                        continue

                # Wait for token capture
                for _ in range(30):
                    await asyncio.sleep(1)
                    if captured_token:
                        break

                if not captured_token:
                    logger.warning("vfs_bearer_not_captured_via_header", login_url=login_url)
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

    async def discover_endpoints(self, country_url: str, duration_seconds: int = 30) -> list[dict[str, Any]]:
        login_url = country_url.rstrip("/") + "/login"
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
            await page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
            await asyncio.sleep(3)
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await self._find_and_fill(page, _EMAIL_SELECTORS, self._email, "email")
            password_selectors = ['input[type="password"]', 'input[name="password"]']
            await self._find_and_fill(page, password_selectors, self._password, "password")
            await page.click('button[type="submit"]')

            logger.info("vfs_discover_waiting", seconds=duration_seconds)
            await asyncio.sleep(duration_seconds)
            await browser.close()

        return intercepted
