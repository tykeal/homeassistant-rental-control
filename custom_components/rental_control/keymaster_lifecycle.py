# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Public lifecycle registration API for verified Keymaster entities."""

from __future__ import annotations

from .keymaster_lifecycle_registry import (
    get_keymaster_lifecycle as get_keymaster_lifecycle,
)
from .keymaster_lifecycle_registry import (
    managed_slot_property_keys as managed_slot_property_keys,
)
from .keymaster_lifecycle_registry import (
    reserve_keymaster_generation as reserve_keymaster_generation,
)
from .keymaster_lifecycle_registry import (
    start_keymaster_lifecycle as start_keymaster_lifecycle,
)
from .keymaster_lifecycle_registry import (
    stop_keymaster_lifecycle as stop_keymaster_lifecycle,
)
from .keymaster_lifecycle_tracker import (
    KeymasterEntityLifecycle as KeymasterEntityLifecycle,
)
