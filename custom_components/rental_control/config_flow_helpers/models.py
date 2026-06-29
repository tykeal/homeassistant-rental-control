# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Typed containers for Rental Control config-flow helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant


@dataclass(slots=True)
class ConfigFormContext:
    """Values needed to render one config or options form."""

    step_id: str
    user_input: dict[str, Any] | None
    errors: dict[str, str]
    description_placeholders: dict[str, str]
    defaults: dict[str, Any] | None = None
    entry_id: str | None = None


@dataclass(slots=True)
class SchemaBuildContext:
    """Values needed to build the config-flow schema."""

    hass: HomeAssistant
    user_input: dict[str, Any]
    defaults: dict[str, Any] | None
    entry_id: str | None = None


@dataclass(slots=True)
class URLValidationResult:
    """Result details from URL validation."""

    error: str | None = None
    status: int | None = None
    reason: str | None = None
    content_type: str | None = None


@dataclass(slots=True)
class FlowValidationResult:
    """Accumulated validation state for a submitted flow step."""

    user_input: dict[str, Any]
    errors: dict[str, str]
    description_placeholders: dict[str, str]
    can_create_entry: bool


@dataclass(slots=True)
class FlowTransitionRequest:
    """Request data for driving one config or options flow step."""

    flow: Any
    step_id: str
    title: Any
    user_input: dict[str, Any] | None
    defaults: dict[str, Any] | None
    entry_id: str | None
    form_renderer: Callable[[Any, ConfigFormContext], Any]
