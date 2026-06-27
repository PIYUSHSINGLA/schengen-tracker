from __future__ import annotations

from datetime import datetime, timezone

import httpx
import structlog

from src.models import Country, Slot

logger = structlog.get_logger(__name__)

COUNTRY_FLAGS: dict[str, str] = {
    "ITA": "🇮🇹",
    "ESP": "🇪🇸",
    "BEL": "🇧🇪",
    "PRT": "🇵🇹",
    "GRC": "🇬🇷",
    "CZE": "🇨🇿",
    "HRV": "🇭🇷",
    "LVA": "🇱🇻",
    "LUX": "🇱🇺",
    "FRA": "🇫🇷",
    "DEU": "🇩🇪",
    "NLD": "🇳🇱",
    "CHE": "🇨🇭",
}

PORTAL_LABELS: dict[str, str] = {
    "vfs": "VFS Global London",
    "tls": "TLScontact London",
}


def _build_message(country: Country, slot: Slot) -> str:
    flag = COUNTRY_FLAGS.get(country.code, "🌍")
    portal_label = PORTAL_LABELS.get(country.portal, country.portal.upper())
    date_fmt = slot.date.strftime("%d %b %Y")
    time_str = f"\n🕐 Time: {slot.time}" if slot.time else ""
    detected_utc = datetime.now(tz=timezone.utc).strftime("%H:%M UTC")

    return (
        f"{flag} <b>{country.name.upper()} — {portal_label}</b>\n"
        f"\n"
        f"📅 Date: {date_fmt}{time_str}\n"
        f"🎫 Slots: {slot.slots_available} available\n"
        f"\n"
        f'🔗 <a href="{country.url}">Book Now</a>\n'
        f"\n"
        f"⏱ Detected: {detected_utc}"
    )


async def send_alert(
    bot_token: str,
    chat_id: str,
    country: Country,
    slot: Slot,
) -> None:
    message = _build_message(country, slot)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
            resp.raise_for_status()
            logger.info(
                "telegram_alert_sent",
                country=country.name,
                slot_date=slot.date.isoformat(),
            )
        except httpx.HTTPStatusError as exc:
            logger.error(
                "telegram_http_error",
                status=exc.response.status_code,
                body=exc.response.text,
            )
        except httpx.ConnectError as exc:
            logger.error("telegram_connect_error", error=str(exc))


async def send_test_alert(bot_token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    message = (
        "🧪 <b>Schengen Tracker — Test Alert</b>\n"
        "\n"
        "Bot is configured correctly and sending messages.\n"
        f"⏱ Sent: {datetime.now(tz=timezone.utc).strftime('%H:%M UTC')}"
    )

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                url,
                json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            )
            resp.raise_for_status()
            print("Test alert sent successfully.")
        except httpx.HTTPStatusError as exc:
            print(f"HTTP error {exc.response.status_code}: {exc.response.text}")
        except httpx.ConnectError as exc:
            print(f"Connection error: {exc}")
