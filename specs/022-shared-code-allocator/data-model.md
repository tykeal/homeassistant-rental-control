<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Shared Door Code Allocator with Persisted Registry

Entities introduced or changed by this feature. Persisted shapes are in
[contracts/code-registry-store.md](contracts/code-registry-store.md); this
document defines semantics, states, and validation rules.

## New entities

### DoorCodeAllocator

**Owner module**: `custom_components.rental_control.allocator.allocator`

**Lifetime**: one per Home Assistant system, held at
`hass.data[DOMAIN][ALLOCATOR]`, created by whichever config entry sets up first
and reused by all later entries.

**Fields**:

- `hass` — Home Assistant instance, used only for storage and notifications.
- `_registry: AllocationRegistry` — in-memory authority.
- `_store: RegistryStore` — persistence wrapper.
- `_lock: asyncio.Lock` — serializes every mutating operation (FR-006).
- `_pending_adoption: set[str]` — entry IDs not yet past their first allocation
  pass. Entries leave it on a completed pass, and also on setup failure,
  disable, unload-while-pending, or removal, so a broken entry cannot hold the
  gate shut.
- `_gate_deadline: float` — monotonic deadline after which a warning and
  persistent notification name the entries that never reported. It does not open
  the gate; new issuance stays fail-closed (FR-018).
- `_registry_lost: bool` — set when `async_load` had to start from an empty
  registry after a load failure. Drives `recovery_fail_closed`.

**Operations** (all async; the phase methods each take `_lock`, and
`async_resolve_cycle` takes it once for the whole cycle):

- `async_load()` — load and validate the store, or start empty with a warning.
- `async_register_entry(entry_id)` — add to `_pending_adoption`; called during
  `async_setup_entry` before the first refresh.
- `async_unregister_entry(entry_id)` — remove from `_pending_adoption` when the
  entry fails setup, is disabled, or unloads before completing a pass.
- `async_resolve_cycle(request)` — the production entrypoint: adopt, rekey,
  allocate, and sweep for one entry's refresh, atomically under one lock
  acquisition, against private non-locking helpers. It never calls the public
  phase methods; `_lock` is non-reentrant.
- `async_adopt(request)` — record an observed code as this reservation's
  allocation, or as a conflict when another owner already holds it.
- `async_rekey(old_key, new_key)` — move an allocation to a rematched identity.
- `async_allocate(request)` — return the existing code, else the preferred code,
  else the first free candidate, else report exhaustion.
- `async_sweep(observation, active_keys)` — release allocations for reservations
  that are gone and whose codes the guard proves are not programmed.
- `async_mark_entry_removed(entry_id)` — abort pending adoption for a removed
  entry and mark its allocations orphaned without releasing codes.
- `async_clear_orphans(known_entry_ids, observations, dry_run)` — operator
  cleanup of allocations whose owning entry no longer exists, using that same
  guard, returning an `OrphanCleanupReport`.

The four phase methods stay public for tests and diagnostics only. Production
code calls `async_resolve_cycle`.

**Validation rules**:

- No operation may return a code recorded to a different `identity_key` (FR-005).
- Loaded codes must be decimal digits whose length equals their stored
  `code_length`; any mismatch corrupts the whole payload (FR-018).
- `async_allocate` for a known `identity_key` returns the same code and performs
  no registry mutation (FR-007).
- A newly allocated lock-backed owner records the planned `lockname` and `slot`
  supplied on its `AllocationRequest`; it is never persisted with a missing
  physical identity that would make FR-014 undecidable.
- Issuance, and only issuance, is suppressed while `_pending_adoption` is
  non-empty; the deadline notifies but does not make unsafe issuance proceed.
- Issuance is suppressed for an entry with unreadable managed slots that the
  registry does not account for (FR-018).
- No operation emits a reconciliation action or calls a lock service.

### AllocationRegistry

**Owner module**: `custom_components.rental_control.allocator.registry`

Pure, no Home Assistant imports. Holds `records: dict[str, AllocationRecord]`
keyed by the literal code string, plus a derived `by_identity: dict[str, str]`
index from `identity_key` to code. Keys and `AllocationRecord.code` are plain
values in memory; obfuscation applies only when `RegistryStore` writes them.

**Validation rules**:

- `by_identity` is a derived in-memory index, rebuilt from `records` on every
  load; it is never persisted, so the two can never disagree in a payload.
  Corruption is the same `identity_key` appearing as an owner of two different
  records, or two persisted records decoding to the same code — both are
  detected before the registry is constructed and discard the whole payload
  (FR-018), matching the contract's validation list.
- A code with more than one owner is a conflict and is unavailable for issuance
  until it has exactly zero or one owner again (FR-022).
- A code whose length differs from the requesting entry's configured length is
  never offered to that entry (FR-012); it still blocks that exact string.
- Changing an entry's `code_length` while it holds active allocations is
  rejected by the options flow, because `async_allocate` returns a known
  identity's existing code unchanged (FR-007) and would otherwise hand back a
  four-digit code to an entry now configured for six (FR-012). The rejection is
  a validation guard in the existing options flow, not a new option; re-issuing
  at a new length is #735's job.

### AllocationRecord

One issued or observed code.

**Fields**:

- `code: str` — the literal zero-padded code, in memory only. Persisted in
  obfuscated form as `encoded_code`; see the contract.
- `code_ref: str` — masked identifier for logs and diagnostics, derived from a
  different salt than the at-rest encoding and never a substitute for it.
- `encoding_salt_value: str` — the literal salt prefixed before the code at
  rest, captured at record creation and fixed for the record's lifetime.
  `encoding_salt_source: str` — a provenance label for that value (`entry_id`).
  Decode uses the value, never the label, so an orphaned record whose first
  owner is gone still decodes.
- `owners: list[AllocationOwner]` — one owner normally, more than one only for
  an adoption conflict.
- `created_at` / `updated_at` — ISO-8601 timestamps.

**State transitions**:

```text
(absent) ──allocate──► HELD ──release──► (absent)
(absent) ──adopt────► HELD
HELD ────adopt by other identity──► CONFLICT
CONFLICT ──one side's code observed gone──► HELD
HELD ──owner entry removed, code still on lock──► ORPHANED
ORPHANED ──code observed gone──► (absent)
```

`CONFLICT` and `ORPHANED` are both reported to the operator and both keep the
code unavailable for issuance. Neither is ever resolved by rotating a code.

### AllocationOwner

**Fields**:

- `entry_id: str` — owning config entry.
- `identity_key: str` — reservation fingerprint, the allocation key.
- `origin: AllocationOrigin` — `PREFERRED`, `COLLISION_RESOLVED`, or `ADOPTED`.
- `lockname: str | None` / `slot: int | None` — the managed lock and slot number
  the code is programmed on; `None` for a lockless entry, which has no physical
  slot. This is the physical identity the FR-014 guard and the unreadable-slot
  accounting need; without it neither rule is decidable.
- `lock_observed: bool` — the code was seen on that lock and slot on the most
  recent observation, or its slot was unreadable and so may still hold it.
  Either way release is forbidden (FR-014).
- `first_seen` / `last_seen` — ISO-8601 timestamps used by the sweep.

### CycleObservation

One entry's physical slot state for one refresh cycle, built by
`coordinator_helpers/code_allocation.py` from what `keymaster_observation.py`
already produced. It exists so the two safety rules above have inputs.

**Fields**: `entry_id`, `lockname: str | None`, `managed_slots: frozenset[int]`,
`observed_codes: dict[str, int]` mapping a readable plain code to its slot, and
`unreadable_slots: frozenset[int]` — the managed slots whose observation is
genuinely indeterminate: observed `status is SlotStatus.UNKNOWN` from
`keymaster_observation.py`, including both missing or unavailable entity state
and slots with `blocked_reason="unreadable"`. `blocked_reason` is diagnostic
only; it is not the discriminator. A `SlotStatus.FREE` slot is known-empty even
though `actual_code` is `None`, so it must not be included. A lockless entry
supplies `lockname=None` and empty sets.

**Derived by the allocator, not passed in**:

- FR-014 retention — an owner is programmed if its code is in `observed_codes`,
  or its `lockname` matches the observation's `lockname` and its `slot` is in
  `unreadable_slots`.
- `unaccounted_slots` — `unreadable_slots` minus the slots claimed by registry
  owners with the same `entry_id` and `lockname`. Non-empty means the entry has
  a slot nothing accounts for, so no new code can be proven unique.

### AllocationRequest / AllocationResult

**Request fields**: `entry_id`, `identity_key`, `preferred_code`, `code_length`,
`fingerprint_history`, `previously_published: bool` (the per-entry cache has
durably recorded that the code was exposed through the sensor or captive
portal), `lockname: str | None`, `slot: int | None`, and
`issuance_allowed: bool` (default `True`, set by `async_resolve_cycle` from the
derived unaccounted-slot set). Newly allocated lock-backed owners copy the
request's physical fields; lockless owners use `None` for both.

**Result fields**: `code: str | None`, `origin: AllocationOrigin | None`,
`reason: str | None` where `reason` is one of `"exhausted"`,
`"adoption_pending"`, `"unaccounted_slots"`, `"recovery_fail_closed"`, or
`None`. `unaccounted_slots` is produced only by `async_resolve_cycle`, which is
the only caller with slot context.

### CycleRequest / CycleResult

The batch shapes `async_resolve_cycle` takes and returns: a `CycleRequest` of
one `CycleObservation`, the adoptions, rekeys, and allocation requests for that
entry's cycle, and `active_keys`; a `CycleResult` of per-identity adoption and
allocation results, the sweep's `ReleaseReport`, and the derived
`unaccounted_slots`. Full field list in the contract.

A result with `code is None` is the fail-closed state. It is never an error and
never raises; the caller records it and the reservation holds.

### RegistryStore

**Owner module**: `custom_components.rental_control.allocator.store`

Wraps `homeassistant.helpers.storage.Store` at key
`rental_control.code_registry`, schema version 1. It is the only component that
converts between plain in-memory codes and their obfuscated `encoded_code` form:
encode on save, decode on load, never elsewhere. The obfuscation is Keymaster's
salted base64 and is not a security boundary; see the contract. An absent store
or invalid payload (wrong version, malformed, undecodable) resolves to an empty
registry plus a warning and a persistent notification. Home Assistant storage
I/O failures are different: setup raises `ConfigEntryNotReady` so the old
registry is not silently discarded and codes are not reissued. Saves use
`async_delay_save` so a burst of entry refreshes produces one write.

### OrphanCleanupReport

**Owner module**: `custom_components.rental_control.allocator.models`

Result of `async_clear_orphans`, returned to the service caller and used for the
log line and the operator notification.

**Fields**:

- `dry_run: bool` — whether anything was actually released.
- `cleared: list[OrphanOutcome]` — records released.
- `retained: list[OrphanOutcome]` — records deliberately kept.

`OrphanOutcome` carries `code_ref`, `entry_id`, `identity_key`, and for retained
records a `reason` of `code_still_programmed`, `unverifiable_lock`, or
`adoption_conflict`. No field carries a code in any form.

## Changed entities

### Reservation (`reconciliation/plan_models.py`)

- `slot_code: str` becomes `slot_code: str | None`.
  - `str` — the allocator's code for this reservation; identical to what will be
    written to the lock. A lock-backed sensor shows it only after physical
    confirmation; until then it keeps the last observed code or reports no code
    if none is safe. A lockless sensor publishes it immediately.
  - `None` — no code is available this cycle. The planner must hold the slot.
- `code_source` gains `"allocated"`, `"collision_resolved"`, `"adopted"`, and
  `"unallocated"`, alongside today's `"generated"` and `"manual_observed"`.
  `AllocationOrigin.PREFERRED` maps to `"allocated"`,
  `COLLISION_RESOLVED` maps to `"collision_resolved"`, and `ADOPTED` maps to
  `"adopted"`.
- The docstring note "never written to the HA Store" is superseded; the value is
  now persisted in the shared registry, which resolves #736.

**Validation rules**:

- `slot_code is None` implies no `SET`, `OVERWRITE_MANUAL_CHANGE`, or
  `UPDATE_TIMES` action may target this reservation's slot.
- `slot_code is None` never contributes to a `CLEAR`, `RESET`, `stale`,
  `phantom`, `mis_assigned`, or `duplicate_non_canonical` classification.

### DesiredPlan (`reconciliation/plan_models.py`)

- `overflow` gains the reason `"code_unavailable"` for reservations held without
  a code.
- `validate()` gains the invariant described above, reported through the existing
  invariant-violation warning path.

### SlotReadResult / calendar sensor attributes

No shape change. `slot_code` was already `str | None` in
`sensors/calsensor_helpers/models.py`; what changes is that `None` now reaches
the published attributes instead of being backfilled by sensor-side generation.

## Relationships

```text
hass.data[DOMAIN][ALLOCATOR] ─── DoorCodeAllocator
                                      │ owns
                                      ├── AllocationRegistry ── AllocationRecord*
                                      │        (plain codes)        └── AllocationOwner*
                                      ├── RegistryStore ── HA Store
                                      │        (encodes on save, decodes on load)
                                      └── clear_orphaned_codes service

hass.data[DOMAIN][entry_id][COORDINATOR] ─── RentalControlCoordinator
                                                  │ per refresh
                                                  └── code_allocation step ──► DoorCodeAllocator
                                                             │ sets
                                                             └── Reservation.slot_code
                                                                      │ read by
                                                                      ├── compute_desired_plan
                                                                      └── coordinator.get_slot_code ──► sensor
```

One allocator, many coordinators. The arrow from the allocation step to the
allocator is the only path by which a code comes into existence.
