"""Schengen Visa Appointment Slot Tracker — entry point.

Usage:
    python -m src.main                 # normal run (scheduler loop)
    python -m src.main --discover vfs  # discover VFS API endpoints (Italy as sample)
    python -m src.main --discover tls  # discover TLScontact API endpoints (France)
    python -m src.main --test-alert    # send a test Telegram message
    python -m src.main --one-shot      # scrape all countries once, write data/slots.json, exit
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import structlog

from src import store
from src.alerts.telegram import send_test_alert
from src.config import Settings, load_countries
from src.logging_config import configure_logging
from src.one_shot import run_one_shot
from src.scheduler import build_scheduler
from src.scrapers.tls import TLSScraper
from src.scrapers.vfs import VFSScraper
from src.session.tls_session import TLSSession
from src.session.vfs_session import VFSSession

logger = structlog.get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Schengen visa slot tracker")
    parser.add_argument(
        "--discover",
        choices=["vfs", "tls"],
        help="Run endpoint discovery for the given portal and exit",
    )
    parser.add_argument(
        "--discover-duration",
        type=int,
        default=30,
        help="How many seconds to intercept requests during discovery (default 30)",
    )
    parser.add_argument(
        "--test-alert",
        action="store_true",
        help="Send a test Telegram message and exit",
    )
    parser.add_argument(
        "--one-shot",
        action="store_true",
        help="Scrape all countries once, write data/slots.json, send alerts, and exit",
    )
    return parser.parse_args()


async def _run_discovery(portal: str, duration: int, settings: Settings) -> None:
    countries = load_countries()

    if portal == "vfs":
        sample = next((c for c in countries if c.portal == "vfs"), None)
        if not sample:
            print("No VFS country configured.", file=sys.stderr)
            sys.exit(1)
        session = VFSSession(settings.vfs_email, settings.vfs_password, headless=False)
        print(f"\nDiscovering VFS endpoints via {sample.url} (headless=False)...")
        intercepted = await session.discover_endpoints(sample.url, duration)
    else:
        sample = next((c for c in countries if c.portal == "tls"), None)
        if not sample:
            print("No TLS country configured.", file=sys.stderr)
            sys.exit(1)
        session = TLSSession(settings.tls_email, settings.tls_password, headless=False)
        print(f"\nDiscovering TLScontact endpoints via {sample.url} (headless=False)...")
        intercepted = await session.discover_endpoints(
            sample.tls_country or "fr", sample.url, duration
        )

    print(f"\nCaptured {len(intercepted)} XHR/fetch requests:\n")
    for req in intercepted:
        print(f"  {req['method']} {req['url']}")
        auth = req["headers"].get("authorization", "")
        if auth:
            print(f"    Authorization: {auth[:40]}...")
    print("\nFull dump (JSON):")
    print(json.dumps(intercepted, indent=2))


async def main() -> None:
    args = _parse_args()

    # Settings validation happens here — will raise if required env vars are missing
    settings = Settings()  # type: ignore[call-arg]

    configure_logging(settings.log_level)

    if args.test_alert:
        await send_test_alert(settings.telegram_bot_token, settings.telegram_chat_id)
        return

    if args.discover:
        await _run_discovery(args.discover, args.discover_duration, settings)
        return

    # ------------------------------------------------------------------ One-shot run
    countries = load_countries()
    logger.info("countries_loaded", count=len(countries))

    vfs_session = VFSSession(settings.vfs_email, settings.vfs_password, settings.headless)
    tls_session = TLSSession(settings.tls_email, settings.tls_password, settings.headless)
    vfs_scraper = VFSScraper(vfs_session)
    tls_scraper = TLSScraper(tls_session)

    if args.one_shot:
        await run_one_shot(settings, countries, vfs_scraper, tls_scraper)
        return

    # ------------------------------------------------------------------ Normal run (scheduler)
    await store.init_db()

    scheduler = build_scheduler(
        countries=countries,
        vfs_scraper=vfs_scraper,
        tls_scraper=tls_scraper,
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        poll_interval=settings.poll_interval_seconds,
        window_days=settings.slot_window_days,
        alert_cooldown_hours=settings.alert_cooldown_hours,
    )

    scheduler.start()
    logger.info(
        "tracker_started",
        countries=len(countries),
        poll_interval_seconds=settings.poll_interval_seconds,
        window_days=settings.slot_window_days,
    )

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("shutdown_requested")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
