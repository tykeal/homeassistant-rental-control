# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Availability checks for protected config-flow features."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN
from ..const import PROTECTED_REGISTRY_STORE


def protected_association_backend_registered(hass: HomeAssistant) -> bool:
    """Return whether the protected association store is registered."""
    domain_data: Any = hass.data.get(DOMAIN)
    return bool(
        isinstance(domain_data, dict)
        and domain_data.get(PROTECTED_REGISTRY_STORE) is not None
    )
