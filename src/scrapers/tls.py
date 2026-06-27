from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import structlog

from src.models import Country, Slot
from src.scrapers.base import BaseScraper
from src.session.tls_session import TLSSession

logger = structlog.get_logger(__name__)

# TLScontact API paths tried in order.
# Actual live paths must be confirmed with --discover.
_TLS_API_PATHS = [
    "/api/appointment/slots",
    "/api/slots/available",
    "/apiv2/appointment/availability",
    "/appointment/slots",
]


class TLSScraper(BaseScraper):
    def __init__(self, session: TLSSession) -> None:
        super().__init__()
        self._session = session

    async def fetch_slots(self, country: Country) -> list[Slot]:
        tls_country = country.tls_country
        if not tls_country:
            logger.error("tls_missing_country_config", country=country.name)
            return []

        session_data = await self._session.get_session(tls_country, country.url)
        city = country.tls_city or "LON"
        base_url = f"https://{tls_country}.tlscontact.com"

        headers = {
            "Referer": country.url,
            "Origin": base_url,
            "X-Requested-With": "XMLHttpRequest",
        }
        params = {
            "location": city,
            "service": "schengen",
        }

        data = await self._try_endpoints(
            base_url, headers, params, session_data.cookies, country, tls_country
        )
        if data is None:
            logger.warning("tls_no_data", country=country.name)
            return []

        return self._parse_slots(data, country)

    async def _try_endpoints(
        self,
        base_url: str,
        headers: dict[str, str],
        params: dict[str, str],
        cookies: dict[str, str],
        country: Country,
        tls_country: str,
    ) -> Any | None:
        tried_relogin = False

        for path in _TLS_API_PATHS:
            url = base_url + path
            try:
                return await self._get(url, headers=headers, params=params, cookies=cookies)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 401 and not tried_relogin:
                    logger.info("tls_401_relogin", country=country.name)
                    self._session.invalidate(tls_country)
                    session_data = await self._session.get_session(tls_country, country.url)
                    cookies = session_data.cookies
                    tried_relogin = True
                    try:
                        return await self._get(url, headers=headers, params=params, cookies=cookies)
                    except httpx.HTTPStatusError:
                        pass
                elif exc.response.status_code in (404, 403):
                    logger.debug("tls_endpoint_not_found", url=url, status=exc.response.status_code)
                    continue
                else:
                    logger.error(
                        "tls_http_error",
                        url=url,
                        status=exc.response.status_code,
                        country=country.name,
                    )
            except httpx.ConnectError as exc:
                logger.error("tls_connect_error", url=url, error=str(exc))
            except httpx.TimeoutException as exc:
                logger.error("tls_timeout", url=url, error=str(exc))

        return None

    def _parse_slots(self, data: Any, country: Country) -> list[Slot]:
        """Parse TLScontact slot response.

        Common shapes:

        Shape A — list:
          [{"date": "2026-08-20", "available": true, "slots": 2}, ...]

        Shape B — dict with nested list:
          {"slots": [{"appointmentDate": "2026-08-20", "timeSlots": ["10:00", "11:00"]}]}

        Shape C — dict keyed by date:
          {"2026-08-20": {"count": 3, "times": ["09:00"]}}
        """
        slots: list[Slot] = []
        now = datetime.utcnow()

        def _make(slot_date: date, time_val: str | None, count: int) -> Slot:
            return Slot(
                country=country.name,
                country_code=country.code,
                portal="tls",
                date=slot_date,
                time=time_val,
                slots_available=count,
                scraped_at=now,
            )

        records: list[Any] = []
        if isinstance(data, list):
            records = data
        elif isinstance(data, dict):
            # Shape C — date-keyed dict
            for key, value in data.items():
                try:
                    slot_date = date.fromisoformat(key[:10])
                except ValueError:
                    pass
                else:
                    if isinstance(value, dict):
                        count = int(value.get("count") or value.get("slots") or 1)
                        times: list[str] = value.get("times") or value.get("timeSlots") or []
                        if count > 0:
                            for t in times or [None]:  # type: ignore[list-item]
                                slots.append(_make(slot_date, t, count))
                    continue

            # Shape B — standard nested list
            for key in ("slots", "data", "appointmentSlots", "availableSlots"):
                candidate = data.get(key)
                if isinstance(candidate, list):
                    records = candidate
                    break

        for record in records:
            if not isinstance(record, dict):
                continue

            raw_date: str | None = (
                record.get("appointmentDate")
                or record.get("date")
                or record.get("slotDate")
            )
            if not raw_date:
                continue

            available = record.get("available", True)
            if available is False:
                continue

            try:
                slot_date = date.fromisoformat(raw_date[:10])
            except ValueError:
                logger.warning("tls_bad_date", raw=raw_date, country=country.name)
                continue

            time_slots: list[str] = record.get("timeSlots") or record.get("times") or []
            count = int(
                record.get("slots")
                or record.get("count")
                or record.get("slotCount")
                or len(time_slots)
                or 1
            )

            if count > 0:
                if time_slots:
                    for t in time_slots:
                        slots.append(_make(slot_date, t, count))
                else:
                    slots.append(_make(slot_date, record.get("time"), count))

        logger.debug("tls_slots_parsed", country=country.name, count=len(slots))
        return slots
