# Research: Lock Code Buffer Times

**Feature Branch**: `009-lock-code-buffer`
**Date**: 2025-07-17

## Research Task 1: Where to Apply Buffer Offsets

**Question**: Where in the code path should buffer offsets be applied to lock code validity windows?

**Findings**:

The lock code validity window (`date_range_start` / `date_range_end`) is sent to Keymaster in two functions in `util.py`:

1. **`async_fire_set_code`** (line ~286): Called when a new slot is reserved. Sets `date_range_start` and `date_range_end` from `event.extra_state_attributes["start"]` and `event.extra_state_attributes["end"]` (lines 359–366).

2. **`async_fire_update_times`** (line ~425): Called when times change on an existing slot. Also reads from `event.extra_state_attributes["start"]` and `event.extra_state_attributes["end"]` (lines 448–458).

Both functions already receive the `coordinator` as their first argument, making it trivial to read `coordinator.code_buffer_before` and `coordinator.code_buffer_after`.

**Decision**: Apply buffer offsets in `async_fire_set_code` and `async_fire_update_times` at the point where `date_range_start` and `date_range_end` values are prepared for the Keymaster service calls.

**Rationale**: This is the narrowest insertion point that affects only the Keymaster-bound datetime values. The `event.extra_state_attributes["start"]` and `["end"]` values remain unbuffered, preserving correct behavior for all other consumers (check-in sensor, calendar display, event overrides, auto timers).

**Alternatives considered**:
- Applying offset in `calsensor.py` when setting `_event_attributes["start"]`/`["end"]`: Rejected because it would contaminate all downstream consumers (check-in sensor timing, display, ETA calculations) violating FR-005.
- Adding separate `buffered_start`/`buffered_end` attributes to event state: Rejected as unnecessary complexity — only the Keymaster service calls need buffered values, and the buffer can be applied inline.

---

## Research Task 2: Config Flow Pattern for Lock-Conditional Fields

**Question**: How should buffer fields be conditionally shown only when a lock entry is configured?

**Findings**:

The config flow schema in `config_flow.py` (`_get_schema`, line 224) builds a single `vol.Schema`. Fields like `CONF_TRIM_NAMES` and `CONF_MAX_NAME_LENGTH` are included unconditionally in the schema but are functionally relevant only when a lock is configured. The schema does not currently have conditional visibility logic — all fields are always shown.

However, per FR-008, buffer fields should "only be visible in the options flow when a lock entry is configured." The current pattern shows all lock-related fields unconditionally. To maintain pattern consistency while honoring FR-008, the buffer fields should be added alongside `CONF_TRIM_NAMES` and `CONF_MAX_NAME_LENGTH` in the same schema section. The visibility constraint can be implemented by adding the buffer fields conditionally when the current `default_dict` has a non-None `CONF_LOCK_ENTRY`.

**Decision**: Add buffer fields to `_get_schema` as `vol.Optional` entries alongside `CONF_TRIM_NAMES`. Make them conditionally included in the schema when `CONF_LOCK_ENTRY` is present and not None/`"(none)"` in the defaults.

**Rationale**: Follows existing patterns for lock-related config fields while honoring FR-008's visibility requirement.

**Alternatives considered**:
- Always showing buffer fields (matching trim_names pattern): Simpler but violates FR-008 which explicitly requires conditional visibility.
- Multi-step config flow: Overkill for 2 additional fields; would break existing UX patterns.

---

## Research Task 3: Migration Pattern (v9→v10)

**Question**: What is the established migration pattern, and how should v9→v10 be implemented?

**Findings**:

Migration in `__init__.py` (`async_migrate_entry`, line 164) follows a sequential version-by-version upgrade pattern. Each version bump:
1. Copies `config_entry.data`
2. Adds new fields with default values
3. Calls `hass.config_entries.async_update_entry()` with the new data and incremented version
4. The config flow handler's `VERSION` class attribute must also be bumped

The most recent migration (v8→v9, line 261) adds `CONF_TRIM_NAMES = False` and `CONF_MAX_NAME_LENGTH = DEFAULT_MAX_NAME_LENGTH`.

**Decision**: Add v9→v10 migration block that adds `CONF_CODE_BUFFER_BEFORE = 0` and `CONF_CODE_BUFFER_AFTER = 0`. Bump `RentalControlFlowHandler.VERSION` from 9 to 10.

**Rationale**: Exact match to established pattern. Default of 0 preserves existing behavior per FR-003.

**Alternatives considered**: None — the pattern is well-established and mandatory.

---

## Research Task 4: Coordinator Property Pattern

**Question**: How should buffer values be stored and updated in the coordinator?

**Findings**:

The `RentalControlCoordinator.__init__` (line 92) reads all config values from `config_entry.data` into instance attributes. The `update_config` method (line 527) re-reads them when options change. Both follow the same pattern:

```python
# In __init__:
self.trim_names: bool = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))

# In update_config:
self.trim_names = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
```

**Decision**: Add `self.code_buffer_before: int` and `self.code_buffer_after: int` to both `__init__` and `update_config` following the same pattern.

**Rationale**: Consistent with all existing config property patterns. The coordinator already passes `self` to `async_fire_set_code` and `async_fire_update_times`, so no additional plumbing is needed.

**Alternatives considered**: None — pattern is mandatory for consistency.

---

## Research Task 5: Buffer Application in `async_fire_set_code` / `async_fire_update_times`

**Question**: What is the exact mechanism to apply the buffer offset?

**Findings**:

The `event.extra_state_attributes["start"]` and `["end"]` values are `datetime` objects (set in `calsensor.py` line 390–391 from `CalendarEvent.start`/`.end`). These are timezone-aware datetimes.

Buffer application is straightforward datetime arithmetic:
```python
from datetime import timedelta

buffered_start = event.extra_state_attributes["start"] - timedelta(minutes=coordinator.code_buffer_before)
buffered_end = event.extra_state_attributes["end"] + timedelta(minutes=coordinator.code_buffer_after)
```

The buffered values replace the raw values only in the Keymaster service call payloads.

**Decision**: Compute buffered datetimes inline in both `async_fire_set_code` and `async_fire_update_times`, using them only in the `date_range_start`/`date_range_end` service call data dicts.

**Rationale**: Minimal change, no side effects, preserves unbuffered values in event attributes.

**Alternatives considered**:
- Creating a helper function `apply_buffer(start, end, coordinator) -> (start, end)`: Viable but adds indirection for a two-line calculation. Could be added if more call sites emerge.

---

## Research Task 6: Event Overrides Interaction

**Question**: Should event overrides store buffered or unbuffered times?

**Findings**:

`event_overrides.py` stores override start/end times for slot ownership verification and reconciliation. The `EventOverrides.async_update` method receives `start_time` and `end_time` from `calsensor.py` (line 547–553). These are the raw calendar event times.

FR-005 explicitly states: "Buffer values MUST NOT affect ... event override matching."

The override times are used for:
- Slot ownership verification (`verify_slot_ownership`)
- Duplicate detection / reconciliation
- Time-change detection (triggering `async_fire_update_times`)

All of these comparisons must use unbuffered times to maintain correct matching.

**Decision**: Event overrides continue to store and compare unbuffered times. No changes to `event_overrides.py`.

**Rationale**: FR-005 compliance. Buffered times would break slot ownership verification since the override would no longer match the calendar event times.

**Alternatives considered**: None — FR-005 is explicit.

---

## Research Task 7: Validation Rules for Buffer Values

**Question**: What validation should buffer values have?

**Findings**:

FR-004 states: "Both buffer options MUST accept only non-negative integer values (minimum 0)."

The edge cases section confirms: "No upper-bound validation is enforced since legitimate use cases for large buffers exist."

Voluptuous provides `vol.All(vol.Coerce(int), vol.Range(min=0))` for this pattern. The config flow already uses similar patterns (e.g., `vol.Range(min=0.5, max=48.0)` for cleaning_window).

**Decision**: Use `vol.All(vol.Coerce(int), vol.Range(min=0))` for both buffer fields in the schema.

**Rationale**: Matches FR-004 exactly and follows existing voluptuous patterns in the codebase.

**Alternatives considered**:
- Adding an upper bound: Rejected per spec edge cases — "legitimate use cases for large buffers exist."
- Using `cv.positive_int`: This requires ≥1, not ≥0, so it's incorrect for this use case.

---

## Summary

All unknowns resolved. No NEEDS CLARIFICATION items remain. Key decisions:

| Area | Decision |
|------|----------|
| Buffer insertion point | `async_fire_set_code` and `async_fire_update_times` in `util.py` |
| Config flow | Conditional buffer fields alongside existing lock-related options |
| Migration | v9→v10, both buffers default to 0 |
| Coordinator | New `code_buffer_before`/`code_buffer_after` int properties |
| Event overrides | Unchanged — unbuffered times per FR-005 |
| Validation | Non-negative integer, no upper bound |
