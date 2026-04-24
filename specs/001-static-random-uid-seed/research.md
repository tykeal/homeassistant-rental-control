# Research: Static Random Seed from iCal UID

**Feature**: 001-static-random-uid-seed
**Date**: 2026-04-24
**Status**: Complete — all unknowns resolved

## Research Tasks

### R-001: UID Availability in Existing Code

**Question**: Is the iCal UID already parsed and available in the coordinator/sensor pipeline?

**Finding**: **Yes — fully available, no parsing changes needed.**

- The coordinator (`coordinator.py:711`) already extracts the UID from raw iCal events via `event.get("UID")`.
- It passes `uid=str(raw_uid) if raw_uid is not None else None` to the HA `CalendarEvent` dataclass (`coordinator.py:718`).
- The `CalendarEvent` from `homeassistant.components.calendar` natively supports a `uid` attribute.
- The sensor already accesses `event.uid` in `_handle_coordinator_update` for slot assignment (`calsensor.py:452`).

**Decision**: No changes to the coordinator. The UID is already flowing through the pipeline; it just isn't stored in `_event_attributes` or used for code generation yet.

**Alternatives considered**: None — the UID is already parsed and propagated.

---

### R-002: Python `random.seed()` Behavior with String Seeds

**Question**: Does `random.seed(str)` produce deterministic output across Python versions and platforms?

**Finding**: **Yes — deterministic within same Python major version.**

- Python's `random.seed()` accepts any hashable object, including strings.
- For Python 3.x with the default Mersenne Twister generator, `random.seed("some_string")` produces the same PRNG state and subsequent `random.randrange()` output on every call with the same string, on any platform.
- The existing code already relies on this behavior for description-based seeding (`calsensor.py:280`).
- Switching from `random.seed(description)` to `random.seed(uid)` changes only the input value, not the mechanism.

**Decision**: Continue using `random.seed()` with a string argument. The UID string replaces the description string — same mechanism, different input.

**Alternatives considered**:
- `hashlib`-based code generation: More explicit, but would change the PRNG distribution and break parity with the existing code generation pattern. Rejected — unnecessary complexity for no benefit.

---

### R-003: UID Immutability per RFC 5545

**Question**: Is the iCal UID truly immutable per the specification?

**Finding**: **Yes — guaranteed by RFC 5545 Section 3.8.4.7.**

- The UID property is a globally unique identifier that "MUST be unique within the iCalendar object" and persists for the lifetime of the event.
- Calendar providers (Google Calendar, Apple Calendar, Outlook, Airbnb, VRBO, Guesty) all follow this convention.
- If an event is deleted and recreated (e.g., cancelled and re-booked), it will have a new UID — this is correct and expected behavior.

**Decision**: UID is a reliable immutable seed. The spec's assumption is confirmed.

**Alternatives considered**: None — RFC 5545 is authoritative.

---

### R-004: Fallback Chain Design

**Question**: What is the correct fallback priority when UID or description is unavailable?

**Finding**: The existing fallback already handles description-is-None → date_based. The new chain adds UID as the preferred seed.

**Current behavior** (`calsensor.py:254-260`):
```
if description is None → force date_based
elif static_random → seed from description
elif last_four → extract digits
else → date_based (default)
```

**New behavior**:
```
if static_random:
    if uid is not None → seed from uid
    elif description is not None → seed from description (legacy fallback)
    else → fall through to date_based
elif last_four → extract digits (unchanged)
else → date_based (unchanged)
```

**Decision**: The description-is-None guard at the top of `_generate_door_code()` must be relaxed for `static_random` when a UID is available. Specifically:
- Only force `date_based` when *both* UID and description are None (for `static_random`).
- For `last_four`, the existing description-is-None guard remains correct since last_four extracts from description text.

**Alternatives considered**:
- Always fall back to date_based when UID is missing: Rejected — would break backward compatibility for description-seeded users who don't have UIDs.
- Combine UID + description as seed: Rejected — would make the code change when either value changes, defeating the purpose.

---

### R-005: Breaking Change Impact Assessment

**Question**: What is the scope of the one-time code rotation?

**Finding**:
- **Affected**: Only users configured with `code_generation = "static_random"`.
- **Not affected**: Users with `date_based` (default) or `last_four` generators.
- **Mechanism**: On upgrade, existing events will now seed from UID instead of description, producing different PRNG output.
- **Mitigation**: Document in release notes. Users must re-program active lock codes after upgrading.
- **No data migration needed**: There is no persisted seed value. The code is generated fresh on each sensor update cycle.

**Decision**: Accept the one-time rotation as a necessary trade-off for long-term stability. Document clearly in release notes.

---

### R-006: Exposing UID as Event Attribute

**Question**: What is the pattern for adding new attributes to the sensor?

**Finding**: Attributes are stored in `self._event_attributes` dict, initialized in `__init__` (`calsensor.py:72-83`) and populated in `_handle_coordinator_update` (`calsensor.py:371-375`). The dict is exposed via `extra_state_attributes` property.

**Decision**: Add `"uid": None` to the initialization dict and set `self._event_attributes["uid"] = event.uid if hasattr(event, "uid") else None` during coordinator updates. Also clear it in the no-events reset block.

**Alternatives considered**: None — this follows the exact pattern used by all other attributes (summary, description, location, start, end).

## Summary

All research tasks are resolved. No NEEDS CLARIFICATION items remain. The implementation is straightforward:
1. The UID is already parsed and available — no coordinator changes.
2. `random.seed(str)` is deterministic and already in use.
3. RFC 5545 confirms UID immutability.
4. The fallback chain is well-defined: UID → description → date_based.
5. The breaking change is scoped and documented.
6. The attribute addition follows existing patterns exactly.
