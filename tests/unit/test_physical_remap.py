# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guards on re-keying a stale mapping to an amended stay (issue #867)."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from typing import Any

from homeassistant.util import dt as dt_util

from custom_components.rental_control.coordinator_helpers.physical_remap import (
    remap_observed_mappings_to_physical_reservations as remap,
)
from custom_components.rental_control.reconciliation import Reservation

_OLD_START = datetime(2026, 8, 10, 16, tzinfo=dt_util.UTC)
_OLD_END = datetime(2026, 8, 14, 11, tzinfo=dt_util.UTC)
_KEY = "adopted.entry.slot6"


def _res(key: str, name: str, shift_days: int) -> Reservation:
    """Return a reservation *shift_days* away from the lock's window."""
    start = _OLD_START + timedelta(days=shift_days)
    end = _OLD_END + timedelta(days=shift_days)
    return Reservation(
        identity_key=key,
        start=start,
        end=end,
        buffered_start=start,
        buffered_end=end,
        summary=name,
        slot_name=name,
        display_slot_name=name,
        slot_code="1357",
    )


def _persisted() -> dict[str, Any]:
    """Return an adopted mapping holding slot 6's old window."""
    return {
        _KEY: {
            "slot": 6,
            "last_observed_actual": {
                "start_state": _OLD_START.isoformat(),
                "end_state": _OLD_END.isoformat(),
            },
        }
    }


def test_amended_stay_takes_over_the_stale_mapping() -> None:
    """The only reservation carrying the slot's name inherits the mapping."""
    persisted = _persisted()

    keys = remap(persisted, [_res("smith", "Smith", -1)], {6: "Smith"}, {_KEY})

    assert keys == {"smith"}
    assert list(persisted) == ["smith"]
    assert persisted["smith"]["fingerprint_history"] == [_KEY]


def test_window_owned_by_a_renamed_stay_blocks_the_rekey() -> None:
    """A stay still holding the lock's window may be the slot's real owner.

    Smith was renamed Smyth upstream while a later Smith stay exists; the
    lock's window still points at Smyth, so the mapping must not jump to
    the other Smith.
    """
    persisted = _persisted()
    current = [_res("smyth", "Smyth", 0), _res("smith", "Smith", 30)]

    keys = remap(persisted, current, {6: "Smith"}, {_KEY})

    assert keys == {_KEY}
    assert list(persisted) == [_KEY]


def test_ambiguous_name_blocks_the_rekey() -> None:
    """Two stays carrying the slot's name leave the mapping alone."""
    persisted = _persisted()
    current = [_res("smith-1", "Smith", -1), _res("smith-2", "Smith", 30)]

    keys = remap(persisted, current, {6: "Smith"}, {_KEY})

    assert keys == {_KEY}


def test_exact_match_keeps_its_target() -> None:
    """A name-only fallback never takes a stay an exact match claimed."""
    persisted = _persisted()
    persisted["adopted.entry.slot7"] = {
        "slot": 7,
        "last_observed_actual": {
            "start_state": (_OLD_START - timedelta(days=5)).isoformat(),
            "end_state": (_OLD_END - timedelta(days=5)).isoformat(),
        },
    }
    current = [_res("smith", "Smith", -5)]

    keys = remap(
        persisted,
        current,
        {6: "Smith", 7: "Smith"},
        {_KEY, "adopted.entry.slot7"},
    )

    assert keys == {_KEY, "smith"}
    assert persisted["smith"]["slot"] == 7
    assert _KEY in persisted


def test_calendar_mapping_of_a_departed_stay_is_not_moved() -> None:
    """Only adopted mappings fall back to the slot's name.

    A calendar-written mapping keeps its key through its UID when its stay
    is amended, so an unmatched one means the stay left the feed. Its slot
    must stay on the ghost path rather than pass to the guest's next
    booking.
    """
    persisted = {"smith-stay-a": _persisted()[_KEY]}
    current = [_res("smith-stay-b", "Smith", 60)]

    keys = remap(persisted, current, {6: "Smith"}, {"smith-stay-a"})

    assert keys == {"smith-stay-a"}
    assert list(persisted) == ["smith-stay-a"]
