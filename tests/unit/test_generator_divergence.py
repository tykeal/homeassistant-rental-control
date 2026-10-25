# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for published codes the configured generator cannot explain."""

from __future__ import annotations

from datetime import timedelta
from types import MappingProxyType
from types import SimpleNamespace
from typing import Any

from homeassistant.util import dt as dt_util
import pytest

from custom_components.rental_control.allocator.models import ForcedReissueDirective
from custom_components.rental_control.const import CODE_GENERATOR_LAST_FOUR
from custom_components.rental_control.const import CONF_CODE_GENERATION
from custom_components.rental_control.const import CONF_CODE_LENGTH
from custom_components.rental_control.const import CONF_SHOULD_UPDATE_CODE
from custom_components.rental_control.coordinator_helpers import generator_divergence
from custom_components.rental_control.coordinator_helpers.reissue import (
    CodeResolutionRequest,
)
from custom_components.rental_control.reconciliation import Reservation

_ENTRY_ID = "entry-divergence"


@pytest.fixture(autouse=True)
def _clean_state() -> Any:
    """Keep module-level divergence state out of neighbouring tests."""
    generator_divergence.reset_reporting()
    yield
    generator_divergence.reset_reporting()


def _hass(
    *,
    generator: str = CODE_GENERATOR_LAST_FOUR,
    code_length: int = 4,
    should_update_code: bool = True,
) -> Any:
    """Return a hass stub whose entry data is a MappingProxyType."""
    entry = SimpleNamespace(
        title="Front Cottage",
        data=MappingProxyType(
            {
                CONF_CODE_GENERATION: generator,
                CONF_CODE_LENGTH: code_length,
                CONF_SHOULD_UPDATE_CODE: should_update_code,
            }
        ),
        options=MappingProxyType({}),
    )
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: entry)
    )


def _reservation(
    *, started: bool = False, phone_last_four: str | None = None
) -> Reservation:
    """Return one reservation whose stay is ahead of or behind now."""
    now = dt_util.now()
    start = now - timedelta(hours=4) if started else now + timedelta(days=3)
    end = start + timedelta(days=2)
    return Reservation(
        identity_key="identity-1",
        start=start,
        end=end,
        buffered_start=start - timedelta(hours=1),
        buffered_end=end + timedelta(hours=1),
        summary="Guest",
        slot_name="Guest",
        display_slot_name="Guest",
        slot_code=None,
        phone_last_four=phone_last_four,
    )


def _query(
    hass: Any,
    reservations: list[Reservation],
    held: str | None,
    **overrides: Any,
) -> generator_divergence.DivergenceQuery:
    """Return a query whose registry hands back one held code."""
    allocator = SimpleNamespace(
        _registry=SimpleNamespace(code_for_identity=lambda key: held)
    )
    kwargs: dict[str, Any] = {
        "hass": hass,
        "entry_id": _ENTRY_ID,
        "lockname": "frontdoor",
        "allocator": allocator,
        "reservations": reservations,
        "managed_slots": [],
        "match_slot": lambda res, slots, consumed: None,
    }
    kwargs.update(overrides)
    return generator_divergence.DivergenceQuery(**kwargs)


def test_stale_phone_code_rotates_before_the_stay() -> None:
    """A phoneless stay still ahead of now is re-issued and reported."""
    reservation = _reservation()
    directives = generator_divergence.evaluate(_query(_hass(), [reservation], "9876"))

    assert [directive.identity_key for directive in directives] == ["identity-1"]
    assert reservation.slot_code is not None
    assert reservation.slot_code != "9876"
    assert reservation.code_source == "generated"
    report = generator_divergence.diagnostics(_ENTRY_ID)
    assert report == [
        {
            "identity_key": "identity-1",
            "configured_generator": CODE_GENERATOR_LAST_FOUR,
            "effective_generator": "date_based",
            "divergence_reason": generator_divergence.PHONE_DIGITS_UNAVAILABLE,
            "rotating": True,
        }
    ]


def test_started_stay_is_reported_but_not_rotated() -> None:
    """A guest already holding the code keeps it; the divergence shows."""
    reservation = _reservation(started=True)
    directives = generator_divergence.evaluate(_query(_hass(), [reservation], "9876"))

    assert directives == ()
    assert reservation.slot_code is None
    report = generator_divergence.diagnostics(_ENTRY_ID)
    assert report is not None
    assert report[0]["rotating"] is False


def test_rotation_requires_should_update_code() -> None:
    """Without the rotation opt-in the divergence is reported only."""
    reservation = _reservation()
    directives = generator_divergence.evaluate(
        _query(_hass(should_update_code=False), [reservation], "9876")
    )

    assert directives == ()
    assert reservation.slot_code is None
    report = generator_divergence.diagnostics(_ENTRY_ID)
    assert report is not None
    assert report[0]["rotating"] is False


def test_phone_derived_code_is_not_a_divergence() -> None:
    """A code the phone digits still explain is left entirely alone."""
    reservation = _reservation(phone_last_four="9876")
    directives = generator_divergence.evaluate(_query(_hass(), [reservation], "9876"))

    assert directives == ()
    assert generator_divergence.diagnostics(_ENTRY_ID) is None


def test_date_based_entry_is_never_examined() -> None:
    """An entry that never used phone digits cannot diverge this way."""
    reservation = _reservation()
    directives = generator_divergence.evaluate(
        _query(_hass(generator="date_based", code_length=4), [reservation], "9876")
    )

    assert directives == ()
    assert generator_divergence.diagnostics(_ENTRY_ID) is None


def test_existing_forced_reissue_is_not_duplicated() -> None:
    """A cycle already re-issuing an identity is not asked to do it twice."""
    reservation = _reservation()
    directives = generator_divergence.evaluate(
        _query(
            _hass(),
            [reservation],
            "9876",
            already_reissuing=frozenset({"identity-1"}),
        )
    )

    assert directives == ()
    assert generator_divergence.diagnostics(_ENTRY_ID) is None


def test_resolved_divergence_leaves_the_diagnostics() -> None:
    """Once the held code matches again the entry drops out of the report."""
    reservation = _reservation()
    generator_divergence.evaluate(_query(_hass(), [reservation], "9876"))
    assert generator_divergence.diagnostics(_ENTRY_ID) is not None

    settled = _reservation()
    generator_divergence.evaluate(_query(_hass(), [settled], reservation.slot_code))

    assert generator_divergence.diagnostics(_ENTRY_ID) is None


def test_forget_entry_drops_recorded_divergences() -> None:
    """Unloading an entry clears what it had recorded."""
    generator_divergence.evaluate(_query(_hass(), [_reservation()], "9876"))
    generator_divergence.forget_entry(_ENTRY_ID)

    assert generator_divergence.diagnostics(_ENTRY_ID) is None


def test_directives_for_skips_identities_already_reissuing() -> None:
    """Cycle wiring hands the refresh's own directives through unchanged."""
    reservation = _reservation()
    existing = ForcedReissueDirective(_ENTRY_ID, "other-identity", "frontdoor", 3)
    request = CodeResolutionRequest(
        hass=_hass(),
        entry_id=_ENTRY_ID,
        lockname="frontdoor",
        code_length=4,
        managed_slots=[],
        reservations=[reservation],
        forced_reissues=(existing,),
    )
    allocator = SimpleNamespace(
        _registry=SimpleNamespace(code_for_identity=lambda key: "9876")
    )

    directives = generator_divergence.directives_for(
        request, allocator, lambda res, slots, consumed: None
    )

    assert [directive.identity_key for directive in directives] == ["identity-1"]
