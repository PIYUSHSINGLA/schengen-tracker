from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import structlog

from src.models import Country, Slot
from src.scrapers.base import BaseScraper
from src.session.vfs_session import VFSSession

logger = structlog.get_logger(__name__)

# Primary slot-check endpoint discovered via network interception.
# VFS moved their API to a separate subdomain (lift-api).  The path may vary
# per deployment — the --discover flag should be used to confirm the live URL.
VFS_SLOT_API = "https://lift-api.vfsglobal.com/slot/checkslots"

# Fallback paths tried in order if the primary returns 404/403
_FALLBACK_PATHS = [
    "/slot/checkslots",
    "/api/slot/checkslots",
    "/api/appointment/slots",
    "/appointment/availableSlots",
]
_VFS_HOSTS = [
    "https://lift-api.vfsglobal.com",
    "https://api.vfsglobal.com",
]


class VFSScraper(BaseScraper):
    def __init__(self, session: VFSSession) -> None:
        super().__init__()
        self._session = session

    async def fetch_slots(self, country: Country) -> list[Slot]:
        session_data = await self._session.get_session(country.url)

        headers = {
            "Authorization": f"Bearer {session_data.bearer_token}",
            "Referer": country.url,
            "Origin": "https://visa.vfsglobal.com",
        }
        params = {
            "countryCode": "GBR",
            "missionCountry": country.mission_country or country.code,
            "visaCategory": country.visa_category or "Schengen Visa",
            "applicants": "1",
        }

        data = await self._try_endpoints(headers, params, session_data.cookies, country)
        if data is None:
            logger.warning("vfs_no_data", country=country.name)
            return []

        return self._parse_slots(data, country)

    async def _try_endpoints(
        self,
        headers: dict[str, str],
        params: dict[str, str],
        cookies: dict[str, str],
        country: Country,
    ) -> Any | None:
        """Try known endpoints; on 401 re-login once; on 404 try fallbacks."""
        tried_relogin = False

        for host in _VFS_HOSTS:
            for path in _FALLBACK_PATHS:
                url = host + path
                try:
                    return await self._get(url, headers=headers, params=params, cookies=cookies)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 401 and not tried_relogin:
                        logger.info("vfs_401_relogin", country=country.name)
                        self._session.invalidate()
                        session_data = await self._session.get_session(country.url)
                        headers["Authorization"] = f"Bearer {session_data.bearer_token}"
                        cookies = session_data.cookies
                        tried_relogin = True
                        # Retry the same endpoint once
                        try:
                            return await self._get(url, headers=headers, params=params, cookies=cookies)
                        except httpx.HTTPStatusError:
                            pass
                    elif exc.response.status_code in (404, 403):
                        logger.debug("vfs_endpoint_not_found", url=url, status=exc.response.status_code)
                        continue
                    else:
                        logger.error(
                            "vfs_http_error",
                            url=url,
                            status=exc.response.status_code,
                            country=country.name,
                        )
                except httpx.ConnectError as exc:
                    logger.error("vfs_connect_error", url=url, error=str(exc))
                except httpx.TimeoutException as exc:
                    logger.error("vfs_timeout", url=url, error=str(exc))

        return None

    def _parse_slots(self, data: Any, country: Country) -> list[Slot]:
        """Convert VFS API response into Slot objects.

        VFS responses vary by country/deployment.  We handle the two most
        common shapes:

        Shape A — list of objects:
          [{"appointmentDate": "2026-08-15", "slotCount": 3, "timeSlot": "10:30"}, ...]

        Shape B — nested dict:
          {"data": {"slots": [{"date": "2026-08-15", "count": 2}, ...]}}
        """
        slots: list[Slot] = []
        now = datetime.utcnow()

        def _make_slot(slot_date: date, time_val: str | None, count: int) -> Slot:
            return Slot(
                country=country.name,
                country_code=country.code,
                portal="vfs",
                date=slot_date,
                time=time_val or None,
                slots_available=count,
                scraped_at=now,
            )

        # Normalise: extract list from nested responses
        records: list[Any] = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            for key in ("slots", "data", "appointmentSlots", "availableSlots"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    records = candidate
                    break
                if isinstance(candidate, dict):
                    for inner_key in ("slots", "appointmentSlots"):
                        inner = candidate.get(inner_key)
                        if isinstance(inner, list):
                            records = inner
                            break
                if records:
                    break

        for record in records:
            if not isinstance(record, dict):
                continue

            # Attempt to find date field
            raw_date: str | None = (
                record.get("appointmentDate")
                or record.get("date")
                or record.get("slotDate")
                or record.get("availableDate")
            )
            if not raw_date:
                continue

            try:
                slot_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                logger.warning("vfs_bad_date", raw=raw_date, country=country.name)
                continue

            count: int = int(
                record.get("slotCount")
                or record.get("count")
                or record.get("availableSlots")
                or record.get("noOfSlots")
                or 1
            )
            time_val: str | None = record.get("timeSlot") or record.get("time")

            if count > 0:
                slots.append(_make_slot(slot_date, time_val, count))

        logger.debug("vfs_slots_parsed", country=country.name, count=len(slots))
        return slots
