# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Pydantic schemas for the dynamic DNS client API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DynDnsEntryIn(BaseModel):
    enabled: bool = True
    provider: str = Field(default="noip", max_length=32)
    server: str = Field(default="", max_length=255)
    hostname: str = Field(min_length=1, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    custom_url: str | None = Field(default=None, max_length=512)


class DynDnsEntryOut(BaseModel):
    id: int
    enabled: bool
    provider: str
    server: str
    hostname: str
    username: str | None
    custom_url: str | None
    last_ip: str | None
    last_status: str | None
    last_error: str | None
    last_update_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class DynDnsUpdateResult(BaseModel):
    ip: str | None = None
    results: list[dict] = []
    reason: str | None = None
