"""One-shot mode: scrape all countries once, write data/slots.json, send alerts."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from src import detector, store
from src.alerts.telegram import send_alert
from src.models import Country, Slot
from src.scrapers.tls import TLSScraper
from src.scrapers.vfs import VFSScraper

logger = structlog.get_logger(__name__)

OUTPUT_PATH = Path("data/slots.json")

# Portal booking URL base — used when a country's url is the canonical booking link
_PORTAL_LABELS: dict[str, str] = {"vfs": "VFS", "tls": "TLS"}


def _build_country_entry(
    country: Country,
    slots: list[Slot],
    error: str | None,
    now_iso: str,
    window_days: int,
) -> dict[str, Any]:
    if error is not None:
        status = "error"
    elif slots:
        status = "available"
    else:
        status = "none"

    slot_entries = [
        {
            "date": s.date.isoformat(),
            "time": s.time or "",
            "count": s.slots_available,
        }
        for s in sorted(slots, key=lambda s: s.date)
    ]

    return {
        "name": country.name,
        "code": country.code,
        "flag": country.flag,
        "portal": country.portal,
        "booking_url": country.url,
        "status": status,
        "slots": slot_entries,
        "last_checked": now_iso,
        "error": error,
    }


async def _scrape_one(
    country: Country,
    vfs_scraper: VFSScraper,
    tls_scraper: TLSScraper,
    bot_token: str,
    chat_id: str,
    window_days: int,
    alert_cooldown_hours: int,
) -> tuple[list[Slot], str | None]:
    log = logger.bind(country=country.name, portal=country.portal)
    try:
        scraper = vfs_scraper if country.portal == "vfs" else tls_scraper
        fetched: list[Slot] = await scraper.fetch_slots(country)
        log.info("slots_fetched", count=len(fetched))

        novel = await detector.detect_new_slots(
            country, fetched, window_days, alert_cooldown_hours
        )
        for slot in novel:
            await send_alert(bot_token, chat_id, country, slot)
            await store.record_alert(country.code, slot.date.isoformat())

        return fetched, None
    except asyncio.TimeoutError:
        log.error("scrape_timeout")
        return [], "Timeout"
    except Exception as exc:  # noqa: BLE001
        log.error("scrape_error", error=str(exc))
        return [], str(exc)


async def run_one_shot(
    settings: Any,
    countries: list[Country],
    vfs_scraper: VFSScraper,
    tls_scraper: TLSScraper,
) -> dict[str, Any]:
    """Scrape all countries once, persist results, send alerts for new slots.

    Returns the dict that was written to data/slots.json.
    """
    await store.init_db()

    now_utc = datetime.now(tz=timezone.utc)
    now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    tasks = [
        _scrape_one(
            country=country,
            vfs_scraper=vfs_scraper,
            tls_scraper=tls_scraper,
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            window_days=settings.slot_window_days,
            alert_cooldown_hours=settings.alert_cooldown_hours,
        )
        for country in countries
    ]

    results = await asyncio.gather(*tasks)

    country_entries: list[dict[str, Any]] = []
    for country, (slots, error) in zip(countries, results):
        entry = _build_country_entry(
            country=country,
            slots=slots,
            error=error,
            now_iso=now_iso,
            window_days=settings.slot_window_days,
        )
        country_entries.append(entry)

    payload: dict[str, Any] = {
        "generated_at": now_iso,
        "window_days": settings.slot_window_days,
        "countries": country_entries,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("slots_json_written", path=str(OUTPUT_PATH))

    available = sum(1 for c in country_entries if c["status"] == "available")
    logger.info("one_shot_complete", total=len(countries), available=available)

    return payload
