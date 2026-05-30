# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Pydantic schemas for the QoS / traffic-shaping API."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

QosProtocol = Literal["tcp", "udp"]


# --- Rules ---

class QosRuleIn(BaseModel):
    protocol: QosProtocol | None = None
    dst_port: int | None = Field(default=None, ge=1, le=65535)
    src_address: str | None = Field(default=None, max_length=64)
    dst_address: str | None = Field(default=None, max_length=64)
    dscp: int | None = Field(default=None, ge=0, le=63)
    enabled: bool = True
    position: int = 0
    comment: str | None = Field(default=None, max_length=255)


class QosRuleOut(QosRuleIn):
    id: int
    class_id: int
    model_config = ConfigDict(from_attributes=True)


# --- Classes ---

class QosClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    priority: int = Field(default=3, ge=0, le=7)
    rate_kbit: int = Field(ge=1)
    ceil_kbit: int | None = Field(default=None, ge=1)
    is_default: bool = False
    comment: str | None = Field(default=None, max_length=255)


class QosClassOut(QosClassIn):
    id: int
    shaper_id: int
    minor: int
    rules: list[QosRuleOut] = []
    model_config = ConfigDict(from_attributes=True)


# --- Shapers ---

class QosShaperIn(BaseModel):
    interface_id: int
    enabled: bool = True
    bandwidth_kbit: int = Field(ge=8, le=100_000_000)
    comment: str | None = Field(default=None, max_length=255)


class QosShaperOut(BaseModel):
    id: int
    interface_id: int
    interface_name: str | None = None
    enabled: bool
    bandwidth_kbit: int
    comment: str | None = None
    dirty: bool
    created_at: datetime
    classes: list[QosClassOut] = []
    model_config = ConfigDict(from_attributes=True)


class QosApplyResult(BaseModel):
    applied: list[str] = []
    cleared: list[str] = []
