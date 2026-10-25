# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Config entry diagnostics for Rental Control.

Home Assistant calls :func:`async_get_config_entry_diagnostics` when a user
presses "Download diagnostics" on the Rental Control config entry.  The
payload bundles the allocator projection, the latest reconciliation
snapshot, the entry configuration, per-slot Keymaster observations and the
current reservation set so that a maintainer can triage a withheld-issuance
report without a round trip.

Door codes are secrets and never appear in the payload.  Only the presence
and the length of a code are reported.  Anything a code can be rebuilt
from is withheld as well: reservation dates and window durations (the
``date_based`` generator inputs) and reservation identity keys (the
``static_random`` and collision-resolution seeds).  Identities become
per-download opaque tokens, so every join inside one file still works.

Guest-identifying text (calendar summaries, slot names, Keymaster slot
name states, booking references, calendar UIDs and free-form slot
exclusion notes), operation error messages that can echo a submitted PIN
back from a failed service call, and the calendar URL are redacted because
Home Assistant diagnostics are routinely pasted into public issue
trackers.
"""

from __future__ import annotations

from collections.abc import KeysView
from datetime import datetime
import hashlib
import secrets
from typing import Any
from typing import cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_NAME
from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .allocator.exclusions import parse_excluded_slots
from .allocator.singleton import get_allocator
from .bookings_only import feed_is_bookings_only
from .const import CONF_CHECKIN
from .const import CONF_CHECKOUT
from .const import CONF_CODE_BUFFER_AFTER
from .const import CONF_CODE_BUFFER_BEFORE
from .const import CONF_CODE_GENERATION
from .const import CONF_CODE_LENGTH
from .const import CONF_DAYS
from .const import CONF_EVENT_PREFIX
from .const import CONF_EXCLUDED_SLOTS
from .const import CONF_HONOR_EVENT_TIMES
from .const import CONF_IGNORE_NON_RESERVED
from .const import CONF_MAX_EVENTS
from .const import CONF_MAX_NAME_LENGTH
from .const import CONF_REFRESH_FREQUENCY
from .const import CONF_SHOULD_UPDATE_CODE
from .const import CONF_START_SLOT
from .const import CONF_TIMEZONE
from .const import CONF_TRIM_NAMES
from .const import COORDINATOR
from .const import DOMAIN
from .const import VERSION
from .coordinator_helpers import allocation_failure
from .coordinator_helpers import generator_divergence

#: Keys whose values are secret (door codes), free-form text that can echo
#: a submitted PIN or guest name back from a failed service call, or
#: personally identifying (guest names, booking references, calendar UIDs
#: that double as the ``static_random`` code seed).  Applied to the whole
#: payload as defence in depth, on top of each projection only emitting
#: safe fields.
TO_REDACT: frozenset[str] = frozenset(
    {
        "actual_code",
        "actual_end",
        "actual_name",
        "actual_start",
        "booking_aliases",
        "buffered_end",
        "buffered_start",
        "code",
        "display_slot_name",
        "end",
        "end_state",
        "last_error",
        "last_slot_errors",
        "name_state",
        "pin",
        "preferred_code",
        "reason",
        "slot_code",
        "slot_name",
        "start",
        "start_state",
        "summary",
        "uid_aliases",
        "url",
    }
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Rental Control config entry.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry the user requested diagnostics for.

    Returns:
        A JSON-serializable diagnostics payload with door codes and guest
        identifying text removed.
    """
    coordinator = _coordinator_for(hass, entry)
    allocator = get_allocator(hass)
    exclusions = parse_excluded_slots(dict(entry.data).get(CONF_EXCLUDED_SLOTS))

    payload: dict[str, Any] = {
        "integration_version": VERSION,
        "entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "source": entry.source,
            "state": str(entry.state),
        },
        "config": _config_diagnostics(entry, coordinator),
        "excluded_slots": [
            {"slot": slot, "reason_present": exclusions[slot] is not None}
            for slot in sorted(exclusions)
        ],
        "allocator": allocator.diagnostics if allocator is not None else None,
        "reconciliation": (
            coordinator.latest_reconciliation_diagnostics
            if coordinator is not None
            else None
        ),
        "slots": _slot_diagnostics(coordinator, set(exclusions)),
        "reservations": _reservation_diagnostics(coordinator),
        "allocation_failure": allocation_failure.diagnostics(entry.entry_id),
        "generator_divergence": generator_divergence.diagnostics(entry.entry_id),
    }
    masker = _IdentityMasker()
    masker.collect(coordinator, allocator)
    redacted: dict[str, Any] = async_redact_data(masker.scrub(payload), TO_REDACT)
    return redacted


class _IdentityMasker:
    """Replace reservation identity keys with per-download opaque tokens.

    ``allocator/candidates.py::candidate_codes`` derives its deterministic
    collision-resolution walk from the identity key and the code length
    alone, so publishing an identity key next to a code length would let a
    reader reproduce the sequence that issued the code.  Tokens are salted
    per download, which keeps every join inside one diagnostics file intact
    while exporting nothing a code can be rebuilt from.
    """

    def __init__(self) -> None:
        """Create a masker with a fresh per-download salt."""
        self._salt = secrets.token_hex(16)
        self._tokens: dict[str, str] = {}

    def collect(self, coordinator: Any | None, allocator: Any | None) -> None:
        """Record every identity key reachable from this download."""
        if coordinator is not None:
            self._add_all(getattr(coordinator, "_latest_res_by_key", None))
            plan = getattr(coordinator, "latest_plan", None)
            self._add_all(getattr(plan, "selected", None))
            overrides = getattr(coordinator, "event_overrides", None)
            self._add_all(getattr(overrides, "persisted_mappings", None))
        registry = getattr(allocator, "_registry", None)
        for record in getattr(registry, "records", {}).values():
            for owner in getattr(record, "owners", ()):
                self._add(getattr(owner, "identity_key", None))

    def scrub(self, value: Any) -> Any:
        """Return *value* with every known identity key tokenized."""
        if isinstance(value, dict):
            return {self.scrub(key): self.scrub(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.scrub(item) for item in value]
        if isinstance(value, str):
            return self._mask_text(value)
        return value

    def _add_all(self, identities: Any) -> None:
        """Record identity keys from a mapping or iterable of keys."""
        if isinstance(identities, dict):
            identities = identities.keys()
        if not isinstance(identities, (list, set, frozenset, tuple, KeysView)):
            return
        for identity in identities:
            self._add(identity)

    def _add(self, identity: Any) -> None:
        """Record one identity key and mint its token."""
        if not isinstance(identity, str) or not identity:
            return
        if identity not in self._tokens:
            digest = hashlib.sha256(f"{self._salt}{identity}".encode()).hexdigest()
            self._tokens[identity] = f"identity-{digest[:12]}"

    def _mask_text(self, text: str) -> str:
        """Replace any embedded identity key inside *text* with its token."""
        for identity in sorted(self._tokens, key=len, reverse=True):
            if identity in text:
                text = text.replace(identity, self._tokens[identity])
        return text


def _coordinator_for(hass: HomeAssistant, entry: ConfigEntry) -> Any | None:
    """Return the coordinator for *entry*, or ``None`` when unavailable."""
    entry_data = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    if not isinstance(entry_data, dict):
        return None
    return entry_data.get(COORDINATOR)


def _config_diagnostics(entry: ConfigEntry, coordinator: Any | None) -> dict[str, Any]:
    """Return the entry configuration relevant to issuance decisions."""
    data = {**dict(entry.data), **dict(entry.options)}
    config: dict[str, Any] = {
        "name": data.get(CONF_NAME),
        "url": data.get(CONF_URL),
        "timezone": data.get(CONF_TIMEZONE),
        "checkin": data.get(CONF_CHECKIN),
        "checkout": data.get(CONF_CHECKOUT),
        "days": data.get(CONF_DAYS),
        "refresh_frequency": data.get(CONF_REFRESH_FREQUENCY),
        "code_length": data.get(CONF_CODE_LENGTH),
        "code_generation": data.get(CONF_CODE_GENERATION),
        "start_slot": data.get(CONF_START_SLOT),
        "max_events": data.get(CONF_MAX_EVENTS),
        "code_buffer_before": data.get(CONF_CODE_BUFFER_BEFORE),
        "code_buffer_after": data.get(CONF_CODE_BUFFER_AFTER),
        "should_update_code": data.get(CONF_SHOULD_UPDATE_CODE),
        "honor_event_times": data.get(CONF_HONOR_EVENT_TIMES),
        "ignore_non_reserved": data.get(CONF_IGNORE_NON_RESERVED),
        "bookings_only_feed": feed_is_bookings_only(data),
        "trim_names": data.get(CONF_TRIM_NAMES),
        "max_name_length": data.get(CONF_MAX_NAME_LENGTH),
        "verify_ssl": data.get(CONF_VERIFY_SSL),
        "event_prefix_set": bool(data.get(CONF_EVENT_PREFIX)),
    }
    if coordinator is not None:
        config["lockname"] = getattr(coordinator, "lockname", None)
        config["timezone"] = str(getattr(coordinator, "timezone", config["timezone"]))
    return config


def _slot_diagnostics(
    coordinator: Any | None, excluded: set[int]
) -> list[dict[str, Any]] | None:
    """Return per-slot Keymaster observations without any door code.

    ``_observe_managed_slots`` classifies fresh physical state only, so the
    identity and retry fields it leaves at their defaults are filled in
    from the latest plan, the persisted Store mappings and the
    :class:`EventOverrides` retry counters instead of reporting ``None``
    and ``0`` as if they were observed facts.
    """
    if coordinator is None:
        return None
    observe = getattr(coordinator, "_observe_managed_slots", None)
    if not callable(observe):
        return None
    desired, persisted, retries = _slot_context(coordinator)
    windows = _reservation_windows(coordinator)
    rows: list[dict[str, Any]] = []
    for slot in observe():
        code = getattr(slot, "actual_code", None)
        rows.append(
            {
                "slot": slot.slot,
                "managed": slot.managed,
                "status": slot.status.value,
                "code_present": bool(slot.actual_code_present),
                "code_length": len(code) if code else 0,
                "readable": slot.actual_code_present is not None,
                "excluded": slot.slot in excluded,
                "name_present": bool(slot.actual_name),
                "enabled": slot.enabled,
                "date_range_enabled": slot.date_range_enabled,
                "actual_window_set": slot.actual_start is not None
                and slot.actual_end is not None,
                "actual_window_matches_desired": _windows_match(
                    (slot.actual_start, slot.actual_end),
                    windows.get(desired.get(slot.slot, "")),
                ),
                "desired_identity_key": desired.get(
                    slot.slot, slot.desired_identity_key
                ),
                "persisted_identity_key": persisted.get(
                    slot.slot, slot.persisted_identity_key
                ),
                "blocked_reason": slot.blocked_reason,
                "retry_count": retries.get(slot.slot, slot.retry_count),
                "last_error_present": bool(slot.last_error),
            }
        )
    return rows


def _slot_context(
    coordinator: Any,
) -> tuple[dict[int, str], dict[int, str], dict[int, int]]:
    """Return per-slot desired, persisted and retry state by slot number."""
    desired: dict[int, str] = {}
    plan = getattr(coordinator, "latest_plan", None)
    selected = getattr(plan, "selected", None)
    if isinstance(selected, dict):
        desired = {
            slot: identity_key
            for identity_key, slot in selected.items()
            if isinstance(slot, int)
        }

    persisted: dict[int, str] = {}
    retries: dict[int, int] = {}
    overrides = getattr(coordinator, "event_overrides", None)
    if overrides is not None:
        mappings = getattr(overrides, "persisted_mappings", None)
        if isinstance(mappings, dict):
            for identity_key, mapping in mappings.items():
                if not isinstance(mapping, dict):
                    continue
                slot = mapping.get("slot")
                if isinstance(slot, int):
                    persisted[slot] = identity_key
        counts = getattr(overrides, "_retry_counts", None)
        if isinstance(counts, dict):
            retries = {
                slot: count
                for slot, count in counts.items()
                if isinstance(slot, int) and isinstance(count, int)
            }
    return desired, persisted, retries


def _reservation_diagnostics(coordinator: Any | None) -> list[dict[str, Any]] | None:
    """Return the current reservation set without codes or guest text."""
    if coordinator is None:
        return None
    reservations = getattr(coordinator, "_latest_res_by_key", None)
    if not isinstance(reservations, dict):
        return None
    rows: list[dict[str, Any]] = []
    for identity_key, res in reservations.items():
        code = getattr(res, "slot_code", None)
        rows.append(
            {
                "identity_key": identity_key,
                "slot_name_length": len(res.slot_name or ""),
                "display_slot_name_length": len(res.display_slot_name or ""),
                "window_set": isinstance(res.start, datetime)
                and isinstance(res.end, datetime),
                "buffer_applied": (res.buffered_start, res.buffered_end)
                != (res.start, res.end),
                "code_issued": code is not None,
                "code_length": len(code) if code else 0,
                "code_source": res.code_source,
                "code_provenance": res.code_provenance,
                "code_rotation_pending": res.code_rotation_pending,
                "desired_slot": res.desired_slot,
                "overflow_reason": res.overflow_reason,
                "eligible": res.eligible,
                "protected_active": res.protected_active,
                "checked_out": res.checked_out,
                "missing_count": res.missing_count,
                "published_once": res.published_once,
            }
        )
    rows.sort(key=lambda row: str(row["identity_key"]))
    return rows


def _reservation_windows(
    coordinator: Any,
) -> dict[str, tuple[datetime | None, datetime | None]]:
    """Return each reservation's buffered window, for internal comparison."""
    reservations = getattr(coordinator, "_latest_res_by_key", None)
    if not isinstance(reservations, dict):
        return {}
    return {
        identity_key: (res.buffered_start, res.buffered_end)
        for identity_key, res in reservations.items()
    }


def _windows_match(
    observed: tuple[datetime | None, datetime | None],
    desired: tuple[datetime | None, datetime | None] | None,
) -> bool | None:
    """Return whether an observed slot window equals the desired window.

    Neither window is exported.  ``codegen.generate_date_based_code``
    derives the whole code from the start date, the end date and the code
    length, and even a duration leaks those dates once joined with the
    exported check-in and check-out times and an active stay.  Comparing
    the windows here keeps the triage signal — "the lock is programmed for
    the wrong dates" — without exporting any timing material.
    """
    if desired is None:
        return None
    if not all(isinstance(value, datetime) for value in (*observed, *desired)):
        return None
    return tuple(
        dt_util.as_utc(cast("datetime", value)) for value in observed
    ) == tuple(dt_util.as_utc(cast("datetime", value)) for value in desired)
