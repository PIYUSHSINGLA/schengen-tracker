from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

from src.models import Country, Slot

logger = structlog.get_logger(__name__)

# Shared browser-like headers
BASE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class BaseScraper(ABC):
    """Shared HTTP infrastructure for all portal scrapers."""

    def __init__(self) -> None:
        self._log = structlog.get_logger(self.__class__.__name__)

    @abstractmethod
    async def fetch_slots(self, country: Country) -> list[Slot]:
        """Return available appointment slots for the given country."""
        ...

    async def _get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> Any:
        """Perform an async GET, return parsed JSON, raise on non-2xx."""
        jitter = random.uniform(2.0, 4.0)
        await asyncio.sleep(jitter)

        async with httpx.AsyncClient(
            headers={**BASE_HEADERS, **headers},
            cookies=cookies or {},
            timeout=DEFAULT_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
