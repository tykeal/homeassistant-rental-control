# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Internal helpers for EventOverrides decomposition.

This package is internal to rental_control. Do not import from
production callers; use custom_components.rental_control.event_overrides
instead.
"""

from .models import EvictionAction
from .models import EvictionDecision
from .models import EvictionReason
from .models import MatchCatalog
from .models import MatchPhase
from .models import MatchRequest
from .models import MatchResult
from .models import OverrideSnapshot
from .models import SlotReservationRequest
from .models import SlotUpdateRequest
from .models import TrimConfig

__all__ = [
    "EvictionAction",
    "EvictionDecision",
    "EvictionReason",
    "MatchCatalog",
    "MatchPhase",
    "MatchRequest",
    "MatchResult",
    "OverrideSnapshot",
    "SlotReservationRequest",
    "SlotUpdateRequest",
    "TrimConfig",
]
