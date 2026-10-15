<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: `rental_control.set_slot_exclusion`

**Feature**: `024-slot-exclusion` | **Plan**: [../plan.md](../plan.md) |
**Spec**: [../spec.md](../spec.md)

This contract covers the operator-facing service and the internal gate API
delta. Citations are live positions on `main` at `edb6a7f`.

## 1. Service declaration

Registered from `register_allocator_services`
(`allocator/services.py:39-57`), beside
`reissue_service.register_force_reissue_service(hass)` at line 42, guarded by
`hass.services.has_service` so repeated registration is a no-op.

```python
SERVICE_SET_SLOT_EXCLUSION = "set_slot_exclusion"
ATTR_LOCKNAME = "lockname"
ATTR_SLOT = "slot"
ATTR_EXCLUDED = "excluded"
ATTR_REASON = "reason"
ATTR_DRY_RUN = "dry_run"

SET_SLOT_EXCLUSION_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_LOCKNAME): cv.string,
        vol.Required(ATTR_SLOT): vol.All(_positive_slot, vol.Range(min=1)),
        vol.Optional(ATTR_EXCLUDED, default=True): cv.boolean,
        vol.Optional(ATTR_REASON): cv.string,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)
```

Registered with `supports_response=SupportsResponse.OPTIONAL` (FR-001),
matching `force_reissue` and `clear_orphaned_codes`.

### Asserted schema invariants

- **No `force` key, ever.** FR-009 forbids any override of the three guards. A
  test asserts `"force" not in SET_SLOT_EXCLUSION_SCHEMA.schema` by key name,
  so adding one is a test failure rather than a review question.
- **`slot` is `Required`.** FR-002: the slot is never inferred from the entity.
- **`slot` is a scalar `int`.** FR-004: no list, no range, no wildcard, no
  "all unreadable" sentinel. The schema makes bulk exclusion unrepresentable.

### `services.yaml`

```yaml
set_slot_exclusion:
  fields:
    entity_id:
      required: false
      selector:
        entity:
          integration: rental_control
          domain: sensor
    lockname:
      required: false
      selector:
        text:
    slot:
      required: true
      selector:
        number:
          min: 1
          mode: box
    excluded:
      required: false
      default: true
      selector:
        boolean:
    reason:
      required: false
      selector:
        text:
    dry_run:
      required: false
      default: false
      selector:
        boolean:
```

### Translations (FR-035)

`strings.json` and both `translations/en.json` and `translations/fr.json` gain
a `services.set_slot_exclusion` block with `name`, `description`, and a `name`
plus `description` for each of the six fields, matching the coverage
`force_reissue` already has. The service `description` must state the residual
risk (FR-030) and that an options-flow save drops exclusions.

## 2. Validation order

Evaluated in this order. The first failure refuses, and a refusal is **total**:
nothing is persisted, no notification is created or dismissed, and no cycle
behaviour changes (FR-013).

| # | Check | Refusal reason | Skipped when clearing? |
|---|-------|----------------|------------------------|
| 1 | Not both `entity_id` and `lockname` | `ambiguous_target` | no |
| 2 | At least one of them | `missing_target` | no |
| 3 | Entity resolves to one loaded entry | `unknown_entity` | no |
| 3 | Lock resolves to exactly one loaded entry | `unknown_lock` / `ambiguous_lock` | no |
| 4 | `start_slot <= slot < start_slot + max_events` | `slot_out_of_range` | **yes** (FR-012) |
| 5 | Slot is in the live observation's `unreadable_slots` | `slot_readable` | **yes** (FR-012) |
| 6 | Slot is not claimed by this entry's registry owners | `slot_claimed` | **yes** (FR-012) |

Checks 4-6 are the three guards of FR-009, FR-010, and FR-011. They have no
escape hatch. Check 4 runs before check 5 because an out-of-range slot has no
meaningful observed status.

Checks 1-3 are structural and apply to both directions: a clear call must still
address exactly one resolvable entry.

## 3. Response

`SupportsResponse.OPTIONAL`, so the response is returned only when the caller
requests one. No field can carry a door code (FR-028, SC-009).

### Success response (FR-008)

```json
{
  "status": "ok",
  "entry_id": "01J9ZQ...",
  "lockname": "frontdoor",
  "slot": 7,
  "excluded": true,
  "changed": true,
  "excluded_slots": [7],
  "dry_run": false
}
```

`changed` is `false` when the resulting set equals the stored set — a repeated
set call or a clear for a slot that was never excluded (FR-005's idempotence
and the spec's "already excluded" and US5 acceptance scenario 2 edge cases). In
that case nothing is written and no notification is touched.

### Dry-run response (FR-007, SC-007)

Identical shape with `"dry_run": true`. `excluded_slots` reports the set that
**would** result and `changed` reports whether a write would occur. The handler
returns before any persistence, any notification call, and any re-validation.
A dry run produces zero persisted changes, zero notification changes, and zero
cycle behaviour changes.

### Refusal response

```json
{
  "status": "refused",
  "reason": "slot_readable",
  "entry_id": "01J9ZQ...",
  "lockname": "frontdoor",
  "slot": 7,
  "excluded": true,
  "changed": false,
  "excluded_slots": [],
  "dry_run": false
}
```

`entry_id` and `lockname` are `null` when the target itself could not be
resolved. `excluded_slots` reports the unchanged current set. A dry run reports
the refusal that **would** apply (FR-007), so an operator can discover a guard
without tripping it.

## 4. Gate API delta

### `issuance.unaccounted_slots` (modified)

```python
def unaccounted_slots(
    allocator: DoorCodeAllocator,
    observation: CycleObservation,
    *,
    excluded: frozenset[int] = frozenset(),
) -> frozenset[int]:
```

Keyword-only with an empty default, so every existing call site is unchanged
and keeps today's behaviour. Returns
`observation.unreadable_slots - claimed - excluded`.

### `issuance.claimed_slots` (new, extracted)

```python
def claimed_slots(
    allocator: DoorCodeAllocator, entry_id: str, lockname: str
) -> frozenset[int]:
```

The claim computation lifted verbatim from `issuance.py:245-252`. The gate and
the service's FR-010 guard both call it, so "claimed" has exactly one
definition. Pure extraction, no behaviour change.

### `code_allocation._adoption_complete` (modified)

```python
def _adoption_complete(
    observation: CycleObservation,
    adoptions: list[AdoptionRequest],
    managed_slots: list[ManagedSlot],
    suppression: ReissueSuppression | None = None,
    *,
    excluded: frozenset[int] = frozenset(),
) -> bool:
```

Only the unreadability branch changes. The `readable_coded_slots <=
adopted_slots` condition is untouched, which is FR-015 and SC-015.

Both call sites must pass `excluded`: the coordinator path, and
`reissue_preview._adoption_would_complete` (`:176-178`). The defaulted
parameter means a missed call site fails silently rather than loudly.

### `reissue_preview._adoption_would_complete` (modified)

```python
def _adoption_would_complete(
    coordinator: Any,
    target: coordinator_reissue.ReissueTarget,
    observed_slots: list[Any] | None,
    observation: CycleObservation,
    *,
    excluded: frozenset[int] = frozenset(),
) -> bool:
```

Forwards `excluded` verbatim to `_adoption_complete`. It performs no exclusion
logic of its own.

### `reissue_preview.preview_request_and_result` (modified)

Loads the addressed entry's excluded set and applies it to **both** consecutive
checks at `allocator/reissue_preview.py:111-114`, and forwards the same set to
`_adoption_would_complete` at `:115`. Narrowing only the second check leaves
`force_reissue` blocked and FR-019 unmet (SC-011); so does leaving the
`_adoption_would_complete` call on its empty default, since an excluded
unreadable slot then still returns `adoption_pending`. The regression must
exercise the complete preview path rather than the narrowed checks alone.

### `slot_exclusions` (new module API)

```python
def load(hass: Any, entry_id: str) -> dict[int, str | None]: ...
def revalidate(
    exclusions: dict[int, str | None], observation: CycleObservation
) -> tuple[dict[int, str | None], dict[int, str | None]]: ...
def persist_excluded_slots(
    hass: Any, entry_id: str, exclusions: dict[int, str | None]
) -> bool: ...
def report_excluded_slots(
    hass: Any, entry_id: str, lockname: str | None,
    exclusions: dict[int, str | None],
) -> None: ...
def dismiss_excluded_slots(hass: Any, entry_id: str) -> None: ...
```

`revalidate` is pure and returns `(kept, cleared)`.
`persist_excluded_slots` returns whether it wrote, and is a no-op when the
serialised value is unchanged. `report_excluded_slots` reconciles and dismisses
in the manner of `report_unreadable_slots`
(`coordinator_helpers/code_allocation.py:160-195`).

### `exclusions` (new pure module API)

```python
def parse_excluded_slots(raw: Any) -> dict[int, str | None]: ...
def serialize_excluded_slots(
    exclusions: dict[int, str | None]
) -> list[dict[str, Any]]: ...
def refusal_for_exclusion(
    slot: int, *, start_slot: int, max_events: int,
    unreadable_slots: frozenset[int], managed_slots: frozenset[int],
    claimed_slots: frozenset[int],
) -> str | None: ...
```

No Home Assistant import appears in this module.

### What is **not** in this delta

- `build_cycle_observation` (`coordinator_helpers/code_allocation.py:262-282`)
  — unchanged signature, unchanged output. It never learns about exclusions.
- `CycleObservation` (`allocator/models.py:177-190`) — unchanged.
- `DoorCodeAllocator._owner_still_programmed`
  (`allocator/allocator.py:378-408`) and `_release_guard_reason` (`:346-376`)
  — unchanged, and they keep receiving the raw unreadable set.
- `services._collect_observations` (`allocator/services.py:98-112`) and
  `async_clear_orphans` — unchanged.
- Anything under `custom_components/rental_control/reconciliation/` —
  unchanged.
- The code registry store, its schema, and its version — unchanged.

## 5. Invariants this contract asserts

1. **Gate-only.** Exclusion reaches exactly three checks: `unaccounted_slots`,
   the unreadability branch of `_adoption_complete`, and the re-issue preview's
   raw rejection. A test greps the production tree and fails if
   `build_cycle_observation` ever references exclusions (FR-016).
2. **No code is released on the strength of an exclusion.** Release evidence
   sees the raw observation, including for owners orphaned by a removed entry
   (FR-018, SC-014).
3. **No override.** The three guards have no force flag, no configuration
   option, and no code path that skips them while setting an exclusion
   (FR-009, SC-005).
4. **Clearing is always allowed.** No guard applies when `excluded` is false,
   including for a slot that has left the managed range (FR-012).
5. **Refusals are total.** No persistence, no notification, no log at INFO, no
   cycle effect (FR-013).
6. **Dry runs are inert.** Zero writes, zero notification calls, zero
   re-validation (FR-007, SC-007).
7. **The set is never load-bearing.** It only ever subtracts from the gate's
   unreadable set; it can never cause a write, a clear, or a judgement that a
   slot is empty (FR-031).
8. **The set self-clears.** Re-validation runs every cycle, before any
   consumer, against the same observation the gate uses (FR-020, FR-024).
9. **No raw code anywhere.** Response, notification, diagnostics, and log are
   all code-free (FR-028, SC-009).
10. **No new configuration.** Nothing is added to the config or options flow
    (FR-034).
