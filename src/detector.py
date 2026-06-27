from __future__ import annotations

from datetime import date, datetime, timedelta

import structlog

from src import store
from src.models import Country, Slot

logger = structlog.get_logger(__name__)


def _within_window(slot_date: date, window_days: int) -> bool:
    today = date.today()
    cutoff = today + timedelta(days=window_days)
    return today <= slot_date <= cutoff


async def detect_new_slots(
    country: Country,
    fetched_slots: list[Slot],
    window_days: int = 90,
    alert_cooldown_hours: int = 6,
) -> list[Slot]:
    """Return slots that are new (not previously seen) and within the booking window."""
    novel: list[Slot] = []

    known_dates = await store.get_known_slot_dates(country.code, country.portal)

    for slot in fetched_slots:
        if not _within_window(slot.date, window_days):
            continue

        date_str = slot.date.isoformat()
        is_new = date_str not in known_dates
        already_alerted = await store.was_alerted_recently(
            country.code, date_str, alert_cooldown_hours
        )

        # Persist / update the slot regardless
        await store.upsert_slot(slot)

        if is_new and not already_alerted:
            novel.append(slot)
            logger.info(
                "new_slot_detected",
                country=country.name,
                slot_date=date_str,
                slots_available=slot.slots_available,
            )

    return novel
