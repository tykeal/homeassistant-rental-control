<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: `rental_control.force_reissue` and the allocator API delta

This contract covers the Home Assistant service surface and the internal
allocator API changes. **There is no persisted schema change.** The feature 022
registry store `rental_control.code_registry` keeps schema version 1 and every
field it has today; see
[../022-shared-code-allocator/contracts/code-registry-store.md](../022-shared-code-allocator/contracts/code-registry-store.md).

## 1. Service declaration

Registered once for the domain from `allocator/reissue_service.py`, guarded by
`hass.services.has_service(DOMAIN, SERVICE_FORCE_REISSUE)` so a second config
entry does not re-register it, with
`supports_response=SupportsResponse.OPTIONAL`.

`custom_components/rental_control/services.yaml`:

```yaml
force_reissue:
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
      required: false
      selector:
        number:
          min: 1
          mode: box
    force:
      required: false
      default: false
      selector:
        boolean:
    dry_run:
      required: false
      default: false
      selector:
        boolean:
```

Matching entries are added to `strings.json`, `translations/en.json`, and
`translations/fr.json`, following the `clear_orphaned_codes` pattern already
present in all three files.

Voluptuous schema:

```python
vol.Schema(
    {
        vol.Optional(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_LOCKNAME): cv.string,
        vol.Optional(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1)),
        vol.Optional(ATTR_FORCE, default=False): cv.boolean,
        vol.Optional(ATTR_DRY_RUN, default=False): cv.boolean,
    }
)
```

`cv.entity_id` — not `cv.entity_ids` — is what makes single-target structural:
a list, an `area_id`, a `device_id`, or `all` fails schema validation before any
handler code runs (FR-004).

## 2. Validation order

Every refusal raises `ServiceValidationError` with an operator-readable message,
changes nothing on any lock, nothing in the registry, and nothing in any sensor
state. The order is fixed so that the reported reason is the most specific one.

| # | Check | Refusal reason | FR |
|---|-------|----------------|----|
| 1 | Exactly one targeting form supplied | `ambiguous_target` / `missing_target` | FR-002, FR-003 |
| 2 | `lockname` and `slot` supplied together | `incomplete_slot_target` | FR-002 |
| 3 | Entity is a loaded Rental Control reservation sensor carrying an event | `not_a_reservation_sensor` | FR-002 |
| 4 | Slot form resolves to exactly one loaded entry whose lockname matches | `unknown_lock` / `ambiguous_lock` | FR-002 |
| 5 | Slot lies in that entry's managed range | `slot_not_managed` | FR-005 |
| 6 | Target slot is covered by a current observation and is not `SlotStatus.UNKNOWN` | `slot_unreadable` / `lock_unavailable` | FR-010 |
| 7 | Target is not checked in, or `force` is true | `checked_in_requires_force` | FR-006 |
| 8 | No pending re-issue and no outstanding hold for this target | `reissue_already_pending` | FR-009 |
| 9 | A unique replacement code is obtainable | `code_space_exhausted` | FR-008 |

Check 8 returns the original outcome rather than an error when the repeat is
identical, so a retried automation is a benign no-op (SC-007).

## 3. Response

`SupportsResponse.OPTIONAL`, so the service is usable from an automation with no
response and from the developer tools with one.

### Non-dry-run response (FR-022)

```json
{
  "status": "accepted",
  "dry_run": false,
  "entry_id": "01J...",
  "identity_key": "a1b2c3...",
  "lockname": "frontdoor",
  "slot": 12,
  "forced_checked_in_override": false,
  "replaced_code_ref": "9f2c1ab4",
  "replacement_code_ref": "3ed70c19",
  "origin": "collision_resolved",
  "replaced_disposition": "held_pending_release",
  "retention_reason": "code_still_programmed"
}
```

**No field in this response contains a raw door code**, in any encoding.
`replacement_code_ref` is `null` when the invocation is accepted but the cycle
has not yet issued a code. `retention_reason` is `null` once released, and can
never be `adoption_conflict`.

### Dry-run response (FR-023)

Identical, plus exactly one additional field, and with
`"status": "preview"` and `"dry_run": true`:

```json
  "replacement_code": "4821"
```

This is the only place in the entire feature where a raw code is emitted. It is
produced by a separate response builder from the non-dry-run one, not by a
conditional `pop()` on a shared builder, so a future edit cannot leak it by
omission.

### Refusal response

When invoked with a response requested, a refusal returns
`{"status": "refused", "reason": "<reason>", "dry_run": <bool>}` alongside the
raised `ServiceValidationError` message. Nothing else is populated.

## 4. Allocator API delta

Everything below is internal to the integration. Feature 022's public entry
points keep their signatures; the additions are backward compatible by default.

### `DoorCodeAllocator._release_guard_reason` (modified)

```python
def _release_guard_reason(
    self,
    record: AllocationRecord,
    owners: list[AllocationOwner],
    observations: list[CycleObservation],
    *,
    refresh_observed: bool = True,
    forced_release: ForcedReleaseExemption | None = None,
) -> str | None:
```

**Contract**:

- With `forced_release=None` — which is what `_sweep_unlocked`,
  `async_mark_entry_removed`, and `async_clear_orphans` all pass, because they
  pass nothing — behaviour is identical to the pre-feature implementation for
  every input. This is a tested invariant, not an intention.
- With a `forced_release` that satisfies all six match conditions in
  [../data-model.md](../data-model.md#forcedreleaseexemption), the
  `adoption_conflict` condition is skipped **for that one owner**. Nothing else
  changes: `unverifiable_lock` and `code_still_programmed` are evaluated exactly
  as before.
- With a `forced_release` that fails any match condition, behaviour is identical
  to `forced_release=None`.
- The function never releases anything; it only returns a reason or `None`.

### `DoorCodeAllocator.async_preview_reissue` (new)

```python
async def async_preview_reissue(self, request: AllocationRequest) -> ReissuePreview:
```

**Contract**: takes `_lock`; performs no registry mutation; never calls
`_store.async_save`; returns the code that `allocate_request` would return for
the same registry state with the target's current code excluded. Shares
`select_code` with the real path so the two cannot diverge.

### `issuance.resolve_cycle` (modified)

Two new steps inside the **existing** single lock acquisition. No new lock is
introduced and `_lock` is still never acquired re-entrantly.

```text
resolve_cycle(request):
    apply_forced_reissues(allocator, request)   # NEW, first
    adopt ...                                   # unchanged
    rekey ...                                   # unchanged
    allocate ...                                # unchanged
    sweep ...                                   # MOD: skips hold owners
    release_forced_holds(allocator, request)    # NEW, last
```

**Contract**:

- `apply_forced_reissues` re-homes at most one owner per directive, preserving
  `entry_id`, `origin`, `lockname`, `slot`, `lock_observed`, and timestamps, and
  never deletes a record.
- `release_forced_holds` evaluates each of this entry's hold owners under the
  guard with an exemption built from that same owner, and calls
  `AllocationRegistry.release(hold_key)` only when the guard returns `None`.
- Both steps contribute `ReissueOutcome` rows to `CycleResult.reissues`.
- The store is saved through the existing `_store.async_save` call in
  `resolve_cycle`; no additional save is introduced.

### `reissue.is_forced_release_hold` / `reissue.forced_release_hold_key` (new)

Pure helpers over the reserved `:reissued:` identity namespace. No Home
Assistant imports.

## 5. Invariants this contract asserts

1. A forced re-issue never releases more than one owner of any record.
2. A forced re-issue never releases an owner whose code may still be programmed,
   or whose lock and slot are not covered by a current observation.
3. The ordinary release paths behave identically to their pre-feature behaviour
   for every input (SC-003).
4. A replaced code is never retired or blacklisted; it returns to the pool
   through the ordinary `release` (FR-018).
5. Exactly one response field in the entire feature may contain a raw code, and
   only when `dry_run` is true (FR-023, SC-006).
6. The calendar sensor attribute surface — `slot_code`, `slot_name`,
   `last_four` — is unchanged (FR-026).
7. No new persisted state and no store version change (FR-013).
8. No new operator configuration option (FR-027).
