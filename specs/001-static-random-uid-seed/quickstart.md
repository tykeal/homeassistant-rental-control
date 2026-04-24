# Quickstart: Static Random Seed from iCal UID

**Feature**: 001-static-random-uid-seed
**Date**: 2026-04-24

## What This Feature Does

Changes the `static_random` door code generator to seed from the iCal UID instead of the event description. This makes generated codes stable across description edits. The UID is also exposed as a new sensor attribute.

## Files to Modify

| File | Change | Scope |
|------|--------|-------|
| `custom_components/rental_control/sensors/calsensor.py` | Add `uid` to `_event_attributes`; change `_generate_door_code()` seed logic | ~20 lines |
| `tests/unit/test_sensors.py` | Update existing `static_random` tests; add UID-based tests | ~80 lines |

## Key Code Locations

### 1. Event Attributes Initialization (`calsensor.py:72-83`)

Add `"uid": None` to the `_event_attributes` dict in `__init__`:

```python
self._event_attributes: dict[str, Any] = {
    "summary": summary,
    "description": None,
    "location": None,
    "start": None,
    "end": None,
    "uid": None,           # ← ADD THIS
    "eta_days": None,
    # ... rest unchanged
}
```

### 2. Coordinator Update Handler (`calsensor.py:371-375`)

Add UID population after description is set:

```python
self._event_attributes["description"] = event.description
self._event_attributes["uid"] = event.uid if hasattr(event, "uid") else None  # ← ADD THIS
```

### 3. Door Code Generation (`calsensor.py:248-308`)

Change the `_generate_door_code()` method:

**Before** (lines 254-260, 278-282):
```python
# Force date_based when description is None
if self._event_attributes["description"] is None:
    generator = "date_based"
# ...
elif generator == "static_random":
    random.seed(self._event_attributes["description"])
    # ...
```

**After**:
```python
# For static_random: only force date_based when BOTH uid and description are None
# For other generators: keep existing description-is-None guard
if self._event_attributes["description"] is None:
    if generator != "static_random" or self._event_attributes.get("uid") is None:
        generator = "date_based"

# ...
elif generator == "static_random":
    # Prefer UID (immutable) over description (mutable) as seed
    seed = self._event_attributes.get("uid") or self._event_attributes["description"]
    random.seed(seed)
    # ...
```

### 4. No-Events Reset (`calsensor.py:476-487`)

Add `"uid": None` to the reset dict:

```python
self._event_attributes = {
    "summary": summary,
    "description": None,
    "location": None,
    "start": None,
    "end": None,
    "uid": None,           # ← ADD THIS
    # ... rest unchanged
}
```

## Testing Strategy

### Tests to Update

- `TestGenerateDoorCodeStaticRandom` class — existing tests that seed from description need to be updated to also set a UID, or explicitly test the description-fallback path.

### New Tests Needed

| Test | Validates |
|------|-----------|
| UID-seeded code is deterministic | FR-005: Same UID → same code every time |
| UID-seeded code stable across description changes | SC-001: Description change doesn't affect code |
| UID=None falls back to description seed | FR-003: Legacy fallback works |
| Both UID and description are None → date_based | FR-004: Final fallback preserved |
| UID exposed as sensor attribute | FR-001: UID visible in attributes |
| Different UIDs produce different codes | FR-006: Statistical distinctness |
| Code length respected with UID seed | FR-007: Code length unchanged |

## Commit Strategy

Following Atomic Commit Discipline (Constitution Principle II):

1. **Commit 1** (`Feat: expose iCal UID as sensor event attribute`):
   - Add `uid` to `_event_attributes` init and reset dicts
   - Populate `uid` from `event.uid` in coordinator update handler
   - Add test for UID attribute exposure

2. **Commit 2** (`Feat: seed static_random door code from iCal UID`):
   - Change `_generate_door_code()` to prefer UID over description
   - Relax description-is-None guard for static_random when UID available
   - Update and add static_random tests

3. **Commit 3** (`Docs: document breaking change for static_random seed`):
   - Release notes / README update documenting the one-time code rotation

## Pre-Commit Hooks Checklist

All commits must pass:
- [ ] ruff (linting)
- [ ] ruff-format (formatting)
- [ ] mypy (type checking)
- [ ] interrogate (100% docstring coverage)
- [ ] reuse-tool (SPDX headers)
- [ ] gitlint (conventional commit format)
- [ ] yamllint (YAML files if modified)
