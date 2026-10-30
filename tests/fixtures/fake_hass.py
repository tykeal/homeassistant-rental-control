# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Shared builders for lightweight fake Home Assistant objects."""

from __future__ import annotations

import os
from types import SimpleNamespace


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
