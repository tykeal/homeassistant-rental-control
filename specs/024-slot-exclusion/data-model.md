<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Slot-Exclusion Recovery Control

**Feature**: `024-slot-exclusion` | **Plan**: [plan.md](plan.md) |
**Spec**: [spec.md](spec.md)

All citations are live positions on `main` at `edb6a7f`. Where the
specification's citations have drifted, see
[research.md](research.md#appendix-corrected-citations).

## New entities

### `SlotExclusion` (persisted record)

One operator decision that one managed slot on one config entry is left out of
the allocator's unreadable-slot accounting.

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `slot` | `int` | yes | Managed slot number. Must have been within the entry's managed range when recorded; re-validated every cycle (FR-023). |
| `reason` | `str \| None` | no | Free-text operator recall only. **Never read by any decision** (FR-006). |

It carries no timestamp, no author, and no expiry. A timestamp would invite
age-based logic, and FR-033 forbids the system from expiring the gate on its
own.

### `ExcludedSlotSet` (in-memory projection)

`dict[int, str | None]` — slot number to optional reason — for one config
entry. This is the only new state the feature introduces, and it is derived
from the config entry on every cycle rather than cached on the coordinator, so
that the gate's set and the re-validated set cannot diverge (FR-024).

### `ExclusionRequest` (service call)

Frozen, slotted dataclass in `allocator/exclusion_service.py`, mirroring
`ReissueRequest` (`allocator/reissue_service.py`).

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `entity_id` | `str \| None` | `None` | Reservation sensor identifying the **entry** only. |
| `lockname` | `str \| None` | `None` | Managed lock name; mutually exclusive with `entity_id` (FR-003). |
| `slot` | `int` | — | **Required** (FR-002). Never inferred from the entity. |
| `excluded` | `bool` | `True` | Set when true, clear when false (FR-005). |
| `reason` | `str \| None` | `None` | Recorded with the exclusion (FR-006). |
| `dry_run` | `bool` | `False` | Report without persisting (FR-007). |

There is **no `force` field**, by FR-009 and the spec's Out of Scope section.
`force_reissue` has one; this service must not.

### `ExclusionTarget` (resolved target)

Produced by `coordinator_helpers/exclusion_targets.py`.

| Field | Type | Notes |
|-------|------|-------|
| `entry_id` | `str` | Addressed config entry. |
| `lockname` | `str \| None` | The entry's lock, `None` for a lockless entry. |
| `slot` | `int` | As supplied. |
| `coordinator` | `Any` | Loaded coordinator, for `start_slot`/`max_events` and observation. |

Deliberately **not** carrying `identity_key` or `checked_in`, which
`ReissueTarget` does: an excluded slot is unreadable and therefore typically
has no identity, and check-in state is irrelevant to an exclusion.

### `ExclusionOutcome` (service response payload)

| Field | Type | Notes |
|-------|------|-------|
| `status` | `"ok" \| "refused"` | |
| `entry_id` | `str \| None` | `None` on an unresolved target. |
| `lockname` | `str \| None` | |
| `slot` | `int` | |
| `excluded` | `bool` | The requested state. |
| `changed` | `bool` | Whether anything was, or would have been, written (FR-008). |
| `excluded_slots` | `list[int]` | The resulting sorted set for the entry. |
| `reason` | `str \| None` | Refusal reason; `None` on success. |
| `dry_run` | `bool` | Echo of the request. |

No field of this payload can carry a door code (FR-028, SC-009).

## Changed entities

### `CycleRequest` (`allocator/models.py:193-203`)

```python
    excluded_slots: frozenset[int] = frozenset()
```

Appended after `forced_reissues`. Defaulted so every existing construction site
compiles unchanged and so the fail-closed value is the default.
`CycleRequest.observation` is **unchanged** and continues to carry the raw
observation into `CycleResult` and the release-evidence paths.

### `CodeResolutionRequest` (`coordinator_helpers/reissue.py:81-91`)

```python
    excluded_slots: frozenset[int] = frozenset()
```

Same shape, same defaulting rationale. `coerce_code_resolution_request`
(`coordinator_helpers/reissue.py`) continues to reject partial legacy calls
unchanged; the new field is never supplied positionally.

### `CycleObservation` (`allocator/models.py:177-190`)

**Unchanged.** This is a requirement, not an omission. `unreadable_slots`
remains the raw set of managed slots whose status is `SlotStatus.UNKNOWN`
(`coordinator_helpers/code_allocation.py:278-280`), because
`DoorCodeAllocator._owner_still_programmed`
(`allocator/allocator.py:378-408`) reads it as evidence that a code may still
be physically programmed. See
[plan.md](plan.md#why-the-filter-is-not-in-the-observation).

### `unaccounted_slots` (`allocator/issuance.py:239-253`)

Gains a keyword-only parameter and one subtraction:

```python
def unaccounted_slots(
    allocator: DoorCodeAllocator,
    observation: CycleObservation,
    *,
    excluded: frozenset[int] = frozenset(),
) -> frozenset[int]:
    """Return unreadable slots not claimed, and not operator-excluded."""
    if observation.lockname is None:
        return frozenset()
    claimed = claimed_slots(allocator, observation.entry_id, observation.lockname)
    return frozenset(observation.unreadable_slots - claimed - excluded)
```

The claim set computation at `issuance.py:245-252` is extracted verbatim into a
module-level `claimed_slots(allocator, entry_id, lockname) -> frozenset[int]`
so that the service's FR-010 guard and the gate share exactly one definition of
"claimed".

### `_adoption_complete` (`coordinator_helpers/code_allocation.py:241-259`)

Only the unreadability branch at lines 249-250 is narrowed:

```python
    if observation.unreadable_slots - excluded:
        return False
```

The `readable_coded_slots <= adopted_slots` condition below it is **untouched**,
which is FR-015: an entry with an excluded dead slot and one unaccounted
readable coded slot stays pending (SC-015).

### `reissue_preview.preview_request_and_result` (`allocator/reissue_preview.py:111-114`)

Both consecutive checks become exclusion-aware:

```python
    if observation.unreadable_slots - excluded:
        return request, ReissuePreview(reason="unaccounted_slots")
    if issuance.unaccounted_slots(allocator, observation, excluded=excluded):
        return request, ReissuePreview(reason="unaccounted_slots")
```

The first is the third gate member (FR-016, FR-019); narrowing only the second
leaves `force_reissue` blocked.

### `const.py`

```python
CONF_EXCLUDED_SLOTS = "excluded_slots"
```

Placed with the other `CONF_` names in alphabetical position.

### `allocator_diagnostics` (`allocator/diagnostics.py:18-58`)

Gains one key:

```python
        "excluded_slots": {
            entry_id: [{"slot": slot, "reason": reason} for ...]
            for entry_id in ...
        },
```

derived by walking `allocator.hass.config_entries.async_entries(DOMAIN)` and
parsing each entry's `CONF_EXCLUDED_SLOTS` through the tolerant parser. Entries
with an empty set are omitted.

## Persisted shape

Stored in the config entry's `data` mapping — **not** `options` — because
`update_listener` (`__init__.py:256-281`) rebuilds `data` from `options.copy()`
and then clears `options`.

```json
{
  "excluded_slots": [
    {"slot": 7, "reason": "lock hardware replaced 2026-10-12"},
    {"slot": 9, "reason": null}
  ]
}
```

- No store schema change and no version bump: the feature-022 registry
  (`rental_control.code_registry`, version 1) and the reconciliation `Store`
  are untouched.
- An entry that predates this feature simply has no `excluded_slots` key, which
  parses to an empty set. That is the entire migration story, and it is why no
  migration code exists.
- An options-flow save drops the key, returning the entry to fail-closed. This
  is intended and is documented in the spec's Edge Cases and in the service
  description.

### Parsing rules (FR-032, SC-010)

`exclusions.parse_excluded_slots(raw) -> dict[int, str | None]` is **total**:
it never raises, and it drops anything it cannot place.

| Input | Result |
|-------|--------|
| key absent, `None`, `""`, a string, a mapping | `{}` |
| a list member that is not a mapping | dropped |
| a member whose `slot` is missing, not an `int`, a `bool`, or `< 1` | dropped |
| a member whose `reason` is not a `str` | kept, with `reason = None` |
| duplicate `slot` values | last wins |

`exclusions.serialize_excluded_slots(mapping) -> list[dict]` emits slots in
ascending order so that an unchanged set serialises identically and the
persistence helper's equality check suppresses the write.

## State transitions

An exclusion for one slot on one entry has exactly two states, and all four
transitions are enumerated:

```text
                 service: excluded=true, all three guards pass
    NOT EXCLUDED ────────────────────────────────────────────► EXCLUDED
         ▲                                                        │
         │  service: excluded=false (no guards apply, FR-012)     │
         ├────────────────────────────────────────────────────────┤
         │  cycle: slot not in observation.unreadable_slots       │
         │         (auto-clear, FR-020)                           │
         ├────────────────────────────────────────────────────────┤
         │  cycle: slot not in observation.managed_slots          │
         │         (range moved, FR-023)                          │
         └────────────────────────────────────────────────────────┘
                 options-flow save: data rebuilt, key dropped
```

There is no third state, no pending state, and no expiry. Every transition out
of `EXCLUDED` except the options-flow save is reported through the log and the
notification (FR-022, FR-027).

## Validation rules

Applied in this order; the first match refuses and nothing is written
(FR-013).

| Order | Rule | Refusal reason | Applies when clearing? |
|-------|------|----------------|------------------------|
| 1 | Exactly one of `entity_id` / `lockname` | `ambiguous_target` / `missing_target` | yes |
| 2 | Target resolves to one loaded entry | `unknown_lock`, `ambiguous_lock`, `unknown_entity` | yes |
| 3 | `start_slot <= slot < start_slot + max_events` | `slot_out_of_range` | **no** (FR-012) |
| 4 | Slot is in the current observation's `unreadable_slots` | `slot_readable` | **no** (FR-012) |
| 5 | Slot not in `claimed_slots(allocator, entry_id, lockname)` | `slot_claimed` | **no** (FR-012) |

Rule 3 precedes rule 4 deliberately: an out-of-range slot has no meaningful
observed status, so reporting it as readable would mislead.

A repeat call that would produce the set already stored succeeds and reports
`changed: false`, writing nothing and touching no notification — for both
setting and clearing.

## Relationships

```text
ConfigEntry.data[CONF_EXCLUDED_SLOTS]
        │ parse_excluded_slots
        ▼
ExcludedSlotSet ──revalidate(observation)──► (kept, cleared)
        │                                        │
        │                                        ├─► persist (only if changed)
        │                                        ├─► log INFO per cleared slot
        │                                        └─► notification reconcile
        │
        ├─► CodeResolutionRequest.excluded_slots ─► CycleRequest.excluded_slots
        │              │                                      │
        │              │                                      ├─► G1 unaccounted_slots
        │              │                                      └─► _track_pending_adoption_slots
        │              └─► G2 _adoption_complete
        │
        ├─► G3 reissue_preview raw rejection
        │
        └─► allocator_diagnostics["excluded_slots"]

CycleObservation (raw, never filtered)
        ├─► CycleRequest.observation ─► CycleResult
        ├─► _owner_still_programmed ─► _release_guard_reason
        └─► _collect_observations ─► async_clear_orphans
```

The left column is the exclusion path; the right-hand `CycleObservation` block
is the release-evidence path. **They never merge.** That separation is FR-016
and is what SC-014 measures.
