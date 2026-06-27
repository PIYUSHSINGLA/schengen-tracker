from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Country(BaseModel):
    name: str
    code: str
    flag: str
    portal: Literal["vfs", "tls"]
    url: str
    visa_category: str | None = None
    mission_country: str | None = None
    centre: str | None = None
    tls_country: str | None = None
    tls_city: str | None = None


class Slot(BaseModel):
    country: str
    country_code: str
    portal: Literal["vfs", "tls"]
    date: date
    time: str | None = None
    slots_available: int
    scraped_at: datetime = Field(default_factory=datetime.utcnow)


class AlertRecord(BaseModel):
    country_code: str
    slot_date: date
    alerted_at: datetime = Field(default_factory=datetime.utcnow)


class VFSSessionData(BaseModel):
    bearer_token: str
    cookies: dict[str, str]
    acquired_at: datetime = Field(default_factory=datetime.utcnow)


class TLSSessionData(BaseModel):
    cookies: dict[str, str]
    acquired_at: datetime = Field(default_factory=datetime.utcnow)
