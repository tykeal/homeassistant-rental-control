# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Internal helper package for the rental-control coordinator.

This package contains pure, side-effect-free helpers extracted from the
:mod:`custom_components.rental_control.coordinator` module. All Home
Assistant state reads, Store writes, service calls, and refresh scheduling
remain in the coordinator shell. Helpers accept data snapshots/contexts and
return data or decisions.
"""

from __future__ import annotations

from . import calendar_parsing
from . import checkin_protection
from . import codegen
from . import config_update
from . import diagnostics
from . import keymaster_bootstrap
from . import keymaster_observation
from . import models
from . import reservations
from . import slot_matching
from . import store_sync
from .models import AdoptionMappingDecision
from .models import BootstrapDecision
from .models import CalendarParseContext
from .models import CheckinProtectionSnapshot
from .models import EventOverrideUpdate
from .models import GhostReservationResult
from .models import KeymasterSlotSnapshot
from .models import ObservedSlotQuery
from .models import ReservationBuildContext
from .models import StoreSyncPlan
from .models import normalize_event_override_update

__all__ = [
    "AdoptionMappingDecision",
    "BootstrapDecision",
    "CalendarParseContext",
    "CheckinProtectionSnapshot",
    "EventOverrideUpdate",
    "GhostReservationResult",
    "KeymasterSlotSnapshot",
    "ObservedSlotQuery",
    "ReservationBuildContext",
    "StoreSyncPlan",
    "calendar_parsing",
    "checkin_protection",
    "codegen",
    "config_update",
    "diagnostics",
    "keymaster_bootstrap",
    "keymaster_observation",
    "models",
    "normalize_event_override_update",
    "reservations",
    "slot_matching",
    "store_sync",
]
