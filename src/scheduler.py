from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src import detector, store
from src.alerts.telegram import send_alert
from src.models import Country
from src.scrapers.vfs import VFSScraper
from src.scrapers.tls import TLSScraper

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


async def scrape_country(
    country: Country,
    vfs_scraper: VFSScraper,
    tls_scraper: TLSScraper,
    bot_token: str,
    chat_id: str,
    window_days: int,
    alert_cooldown_hours: int,
) -> None:
    log = logger.bind(country=country.name, portal=country.portal)
    log.info("scrape_start")

    try:
        scraper = vfs_scraper if country.portal == "vfs" else tls_scraper
        fetched = await scraper.fetch_slots(country)
        log.info("slots_fetched", count=len(fetched))

        novel = await detector.detect_new_slots(
            country, fetched, window_days, alert_cooldown_hours
        )

        for slot in novel:
            await send_alert(bot_token, chat_id, country, slot)
            await store.record_alert(country.code, slot.date.isoformat())

    except asyncio.TimeoutError:
        log.error("scrape_timeout")
    except Exception as exc:  # noqa: BLE001
        log.error("scrape_error", error=str(exc), exc_info=True)


def build_scheduler(
    countries: list[Country],
    vfs_scraper: VFSScraper,
    tls_scraper: TLSScraper,
    bot_token: str,
    chat_id: str,
    poll_interval: int,
    window_days: int,
    alert_cooldown_hours: int,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")

    for i, country in enumerate(countries):
        start_delay = timedelta(seconds=i * 4)
        scheduler.add_job(
            scrape_country,
            trigger=IntervalTrigger(seconds=poll_interval),
            start_date=datetime.utcnow() + start_delay,
            kwargs={
                "country": country,
                "vfs_scraper": vfs_scraper,
                "tls_scraper": tls_scraper,
                "bot_token": bot_token,
                "chat_id": chat_id,
                "window_days": window_days,
                "alert_cooldown_hours": alert_cooldown_hours,
            },
            id=country.code,
            max_instances=1,
            coalesce=True,
            name=f"scrape_{country.code}",
        )
        logger.info(
            "job_registered",
            country=country.name,
            start_offset_seconds=i * 4,
            interval_seconds=poll_interval,
        )

    return scheduler
