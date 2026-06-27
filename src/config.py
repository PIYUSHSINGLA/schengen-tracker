from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

from src.models import Country


class Settings(BaseSettings):
    vfs_email: str = Field(..., env="VFS_EMAIL")
    vfs_password: str = Field(..., env="VFS_PASSWORD")
    tls_email: str = Field(..., env="TLS_EMAIL")
    tls_password: str = Field(..., env="TLS_PASSWORD")
    telegram_bot_token: str = Field(..., env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(..., env="TELEGRAM_CHAT_ID")
    log_level: str = Field("INFO", env="LOG_LEVEL")
    poll_interval_seconds: int = Field(90, env="POLL_INTERVAL_SECONDS")
    slot_window_days: int = Field(90, env="SLOT_WINDOW_DAYS")
    alert_cooldown_hours: int = Field(6, env="ALERT_COOLDOWN_HOURS")
    headless: bool = Field(True, env="HEADLESS")

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def load_countries(yaml_path: str = "config/countries.yaml") -> list[Country]:
    with open(yaml_path, encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)
    return [Country(**c) for c in raw["countries"]]
