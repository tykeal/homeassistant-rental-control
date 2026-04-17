<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Honor PMS Calendar Event Times

**Feature**: 007-honor-pms-times
**Date**: 2025-07-22

## Research Question 1: Current Time Resolution Priority in `_ical_parser`

**Context**: The core change requires reordering the time resolution priority in
`coordinator._ical_parser()` (lines 605–631). We need to understand the exact
current logic before modifying it.

### Finding

The current time resolution in `_ical_parser` follows this priority:

1. **Override exists** (`slot_name` found in `event_overrides`) → use
   `override["start_time"]` / `override["end_time"]` (converted from UTC to
   coordinator timezone)
2. **Event has explicit times** (`event["DTSTART"].dt` is `datetime`, not `date`)
   → use `event["DTSTART"].dt.time()` / `event["DTEND"].dt.time()`
3. **All-day event** (`event["DTSTART"].dt` is `date`, which raises
   `AttributeError` on `.time()`) → use `self.checkin` / `self.checkout`
   (configured defaults)

**Decision**: The new "Honor event times" option changes this priority so that
when enabled, explicit calendar times (step 2) take precedence over stored
overrides (step 1) for events with explicit times. All-day events remain
unchanged.

**Rationale**: This is the minimal change that achieves the spec's goal — PMS
time changes flow through because the CalendarEvent is built with calendar times,
and when the sensor later calls `async_reserve_or_get_slot()`, the time
difference between the CalendarEvent and the stored override triggers
`times_updated=True`, which fires `async_fire_update_times()` to push updates
to Keymaster.

**Alternatives considered**:
- Modifying the sensor layer (`calsensor.py`) to detect time differences and
  override from there — rejected because it would duplicate time resolution
  logic and break separation of concerns.
- Adding a separate "time sync" mechanism — rejected because the existing
  `async_reserve_or_get_slot()` → `async_fire_update_times()` pipeline already
  handles exactly this case when times differ.

## Research Question 2: All-Day Event Detection Mechanism

**Context**: FR-004 requires that all-day events still fall back to overrides or
defaults even when "Honor event times" is enabled. We need to understand how
all-day events are distinguished from timed events.

### Finding

All-day events in iCal have `DTSTART`/`DTEND` values that are `date` objects,
not `datetime` objects. The icalendar library parses these accordingly:

- **Timed event**: `event["DTSTART"].dt` returns `datetime` → `.time()` works
- **All-day event**: `event["DTSTART"].dt` returns `date` → `.time()` raises
  `AttributeError`

The current code uses a `try/except AttributeError` pattern to detect this
(coordinator.py lines 621–630).

**Decision**: Use `isinstance(event["DTSTART"].dt, datetime)` as a reliable
explicit check rather than relying on try/except for control flow. This is
cleaner and makes the honor_event_times branching straightforward.

**Rationale**: Using `isinstance` is more Pythonic for type branching and avoids
masking genuine `AttributeError` bugs. The `datetime` class is a subclass of
`date`, so `isinstance(val, datetime)` correctly identifies timed events, while
`isinstance(val, date)` would match both.

**Alternatives considered**:
- Keep the try/except pattern — viable but harder to read with the new
  three-way branching (honor+timed, override, fallback).
- Check `hasattr(dt_val, 'hour')` — works but less explicit than isinstance.

## Research Question 3: Config Option Persistence and Migration Pattern

**Context**: FR-008 requires the setting to persist across reloads and restarts.
We need to determine the correct pattern for adding a new config option.

### Finding

The integration stores configuration in `config_entry.data` (not
`config_entry.options`). The options flow writes to `config_entry.options`, then
`update_listener()` in `__init__.py` copies options to data and clears options.

Adding a new config key requires:

1. **`const.py`**: Add `CONF_HONOR_EVENT_TIMES` and `DEFAULT_HONOR_EVENT_TIMES`
2. **`config_flow.py`**: Add to `_get_schema()`, bump `VERSION` from 7 to 8
3. **`__init__.py`**: Add migration `7 → 8` that sets the default (`False`) for
   existing entries
4. **`coordinator.py`**: Read the config in `__init__()` and `update_config()`

The existing `CONF_SHOULD_UPDATE_CODE` option (added in migration 6→7) provides
an exact template for this pattern.

**Decision**: Follow the `CONF_SHOULD_UPDATE_CODE` pattern exactly — same
migration approach, same schema position (optional boolean after
`should_update_code`), same default behavior.

**Rationale**: Proven pattern already in the codebase. The migration sets default
to `False` for existing entries (preserving current behavior), while new entries
get `False` as the schema default.

**Alternatives considered**:
- Store in `config_entry.options` instead of `config_entry.data` — rejected
  because the integration's existing pattern always promotes options to data.
- Use HA's `async_setup_entry` to detect missing key and add default — rejected
  because the migration pattern is cleaner and only runs once per entry.

## Research Question 4: Downstream Time Update Mechanism

**Context**: FR-006 says the existing time-update mechanism must propagate
changes without new Keymaster code. We need to verify this.

### Finding

The time update pipeline:

1. `_ical_parser()` builds `CalendarEvent` objects with start/end times
2. Sensor `_reserve_or_update_slot()` (calsensor.py:439) calls
   `async_reserve_or_get_slot()` with the event's UTC times
3. `async_reserve_or_get_slot()` (event_overrides.py:202) finds the existing
   slot, compares stored times with incoming times
4. If times differ → updates the override in-place, returns
   `ReserveResult(slot, is_new=False, times_updated=True)`
5. Sensor detects `times_updated=True` (calsensor.py:502) and either:
   - Fires `async_fire_clear_code()` (for date-based code + should_update_code)
   - Fires `async_fire_update_times()` (otherwise)
6. `async_fire_update_times()` (util.py:333) pushes new start/end datetimes
   to Keymaster via `datetime.set_value` service calls

**Decision**: No changes needed to the downstream pipeline. When the coordinator
builds a CalendarEvent with calendar times (instead of override times), the
existing mechanism automatically detects the difference and propagates it.

**Rationale**: The pipeline already handles exactly this scenario — it compares
incoming times with stored override times and fires updates when they differ.
The only change is what times go *into* the CalendarEvent.

**Alternatives considered**: None — the existing mechanism is exactly what's
needed.

## Research Question 5: Impact on `async_reserve_or_get_slot()` Override Update

**Context**: When "Honor event times" is enabled and times differ, we need to
understand what happens to the stored override after the update.

### Finding

In `async_reserve_or_get_slot()` (event_overrides.py:226-233):

```python
if override is not None and (
    override["start_time"] != start_time
    or override["end_time"] != end_time
):
    override["start_time"] = start_time
    override["end_time"] = end_time
    return ReserveResult(existing, False, True)
```

When the CalendarEvent is built with calendar times (from "Honor event times"),
and those differ from the stored override, the override's stored times are
**updated in-place** to match the calendar times. After this update:

- The override now reflects the PMS times
- On the next refresh, if the PMS hasn't changed again, the calendar times will
  match the (now-updated) override times, so no update fires (FR-007 satisfied)
- If the PMS changes again, a new difference is detected and propagated

**Decision**: This is the correct and desired behavior. The override becomes a
cache of the most recently applied PMS times, enabling change detection on
subsequent refreshes.

**Rationale**: This prevents duplicate updates (FR-007) and ensures the system
converges to a consistent state after each PMS change.

## Research Question 6: Translation File Pattern

**Context**: The new option needs UI labels in strings.json and translation files.

### Finding

Translation entries exist in three places:
- `strings.json` — source of truth, duplicated for both `config.step.user.data`
  and `options.step.init.data`
- `translations/en.json` — English translations (identical content to strings.json)
- `translations/fr.json` — French translations

The existing `should_update_code` entry provides the pattern:
```json
"should_update_code": "For date based codes, update the code if future events change start or end dates"
```

**Decision**: Add `honor_event_times` key to all three files in both
`config.step.user.data` and `options.step.init.data` sections.

**Rationale**: Follows existing pattern exactly. Label wording should be clear
about what it does: "Use calendar event times instead of stored override times".
