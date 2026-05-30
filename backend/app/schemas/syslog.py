# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Pydantic schemas for the remote syslog forwarding API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Protocol = Literal["udp", "tcp"]
SyslogFormat = Literal["rfc5424", "rfc3164"]


class SyslogConfigIn(BaseModel):
    enabled: bool = False
    host: str = Field(default="", max_length=255)
    port: int = Field(default=514, ge=1, le=65535)
    protocol: Protocol = "udp"
    format: SyslogFormat = "rfc5424"
    comment: str | None = Field(default=None, max_length=255)


class SyslogConfigOut(SyslogConfigIn):
    id: int
    model_config = ConfigDict(from_attributes=True)


class SyslogStatus(BaseModel):
    installed: bool
    service_active: bool
    service_state: str = "unknown"
    version: str | None = None


class SyslogInstallResult(BaseModel):
    installed: bool
    already_present: list[str]
    newly_installed: list[str]
    output_tail: str


class SyslogApplyResult(BaseModel):
    message: str
    service: str | None = None
    conf_preview: str | None = None
