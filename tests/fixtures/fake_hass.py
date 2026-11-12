# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Shared builders for lightweight fake Home Assistant objects."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING
from typing import cast

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


def fake_config(config_dir: str = ".") -> SimpleNamespace:
    """Return a fake ``hass.config`` usable by ``homeassistant`` storage.

    Home Assistant's ``Store`` resolves its file through
    ``hass.config.path(...)`` (2026.11+), so fakes must provide ``path``
    with the same semantics as ``homeassistant.core.Config.path``.
    """

    def path(*parts: str) -> str:
        """Join ``parts`` onto the fake configuration directory."""
        return os.path.join(config_dir, *parts)

    return SimpleNamespace(config_dir=config_dir, path=path)


def as_hass(fake: object) -> HomeAssistant:
    """Return a duck-typed fake typed as ``HomeAssistant`` for code under test."""
    return cast("HomeAssistant", fake)


def as_config_entry(fake: object) -> ConfigEntry:
    """Return a duck-typed fake typed as ``ConfigEntry`` for code under test."""
    return cast("ConfigEntry", fake)
