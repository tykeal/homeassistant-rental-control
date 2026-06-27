# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Date helpers for reservation rematching."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from typing import Any

from .plan_models import Reservation
from .rematch_names import _get_nested


def _as_utc_datetime(value: Any) -> datetime | None:
    """Parse a stored datetime value and normalize it to UTC."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None

    return (
        parsed.astimezone(timezone.utc)
        if parsed.tzinfo
        else parsed.replace(tzinfo=timezone.utc)
    )


def _mapping_dates_match_reservation(
    mapping: dict[str, Any],
    reservation: Reservation,
    *,
    include_observed: bool = False,
) -> bool:
    """Return whether stored identity or observed dates match a reservation."""
    reservation_start = _as_utc_datetime(reservation.start)
    reservation_end = _as_utc_datetime(reservation.end)
    identity_start = _as_utc_datetime(_get_nested(mapping, "identity", "start"))
    identity_end = _as_utc_datetime(_get_nested(mapping, "identity", "end"))
    if (
        identity_start is not None
        and identity_end is not None
        and reservation_start == identity_start
        and reservation_end == identity_end
    ):
        return True

    if not include_observed:
        return False

    buffered_start = _as_utc_datetime(reservation.buffered_start)
    buffered_end = _as_utc_datetime(reservation.buffered_end)
    observed_start = _as_utc_datetime(
        _get_nested(mapping, "last_observed_actual", "start_state")
    )
    observed_end = _as_utc_datetime(
        _get_nested(mapping, "last_observed_actual", "end_state")
    )
    return (
        observed_start is not None
        and observed_end is not None
        and buffered_start == observed_start
        and buffered_end == observed_end
    )
