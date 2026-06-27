from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import structlog

from src.models import Slot

logger = structlog.get_logger(__name__)

DB_PATH = Path("data/tracker.db")


async def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country TEXT NOT NULL,
                country_code TEXT NOT NULL,
                portal TEXT NOT NULL,
                slot_date TEXT NOT NULL,
                slot_time TEXT,
                slots_available INTEGER NOT NULL,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_slots_country_date
                ON slots (country_code, slot_date);

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                country_code TEXT NOT NULL,
                slot_date TEXT NOT NULL,
                alerted_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_country_date
                ON alerts (country_code, slot_date);
            """
        )
        await db.commit()
    logger.info("database_initialised", path=str(DB_PATH))


async def upsert_slot(slot: Slot) -> None:
    now_iso = datetime.utcnow().isoformat()
    date_str = slot.date.isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT id FROM slots WHERE country_code = ? AND slot_date = ? AND portal = ?",
                (slot.country_code, date_str, slot.portal),
            )
        ).fetchone()

        if row:
            await db.execute(
                "UPDATE slots SET slots_available = ?, slot_time = ?, last_seen = ? WHERE id = ?",
                (slot.slots_available, slot.time, now_iso, row[0]),
            )
        else:
            await db.execute(
                """INSERT INTO slots
                   (country, country_code, portal, slot_date, slot_time, slots_available, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    slot.country,
                    slot.country_code,
                    slot.portal,
                    date_str,
                    slot.time,
                    slot.slots_available,
                    now_iso,
                    now_iso,
                ),
            )
        await db.commit()


async def get_known_slot_dates(country_code: str, portal: str) -> set[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT slot_date FROM slots WHERE country_code = ? AND portal = ?",
                (country_code, portal),
            )
        ).fetchall()
    return {row[0] for row in rows}


async def was_alerted_recently(
    country_code: str,
    slot_date: str,
    cooldown_hours: int = 6,
) -> bool:
    cutoff = (datetime.utcnow() - timedelta(hours=cooldown_hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                """SELECT id FROM alerts
                   WHERE country_code = ? AND slot_date = ? AND alerted_at > ?""",
                (country_code, slot_date, cutoff),
            )
        ).fetchone()
    return row is not None


async def record_alert(country_code: str, slot_date: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alerts (country_code, slot_date, alerted_at) VALUES (?, ?, ?)",
            (country_code, slot_date, datetime.utcnow().isoformat()),
        )
        await db.commit()
