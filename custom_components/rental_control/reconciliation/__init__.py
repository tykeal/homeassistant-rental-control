# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Public compatibility boundary for Rental Control reconciliation."""

from __future__ import annotations

from .action_models import SlotAction
from .desired import DesiredPlanRequest
from .desired import compute_desired_plan
from .enums import FINGERPRINT_VERSION
from .enums import ActionKind
from .enums import ObservedSlotStatus
from .enums import SlotStatus
from .identity import extract_booking_aliases
from .identity import make_reservation_fingerprint
from .identity import normalize_slot_name_for_fingerprint
from .plan_models import DesiredPlan
from .plan_models import ManagedSlot
from .plan_models import PlannedSlot
from .plan_models import Reservation
from .rematch import find_reservation_rematch
from .rematch_models import RematchKind
from .rematch_models import RematchResult
from .stateless import StatelessPlanRequest
from .stateless import compute_stateless_plan
from .stateless_models import DesiredReservation
from .stateless_models import ObservedSlot
from .stateless_models import StatelessPlan
from .store_models import CacheOnlyStoreRecord
from .store_models import SlotMapping
from .store_models import StoredActual
from .store_models import StoredIdentity

__all__ = [
    "ActionKind",
    "CacheOnlyStoreRecord",
    "DesiredPlan",
    "DesiredPlanRequest",
    "DesiredReservation",
    "FINGERPRINT_VERSION",
    "ManagedSlot",
    "ObservedSlot",
    "ObservedSlotStatus",
    "PlannedSlot",
    "RematchKind",
    "RematchResult",
    "Reservation",
    "SlotAction",
    "SlotMapping",
    "SlotStatus",
    "StatelessPlan",
    "StatelessPlanRequest",
    "StoredActual",
    "StoredIdentity",
    "compute_desired_plan",
    "compute_stateless_plan",
    "extract_booking_aliases",
    "find_reservation_rematch",
    "make_reservation_fingerprint",
    "normalize_slot_name_for_fingerprint",
]
