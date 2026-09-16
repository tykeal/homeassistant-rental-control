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
  pass.
- `_gate_deadline: float` — monotonic deadline after which the adoption gate
  opens regardless.

**Operations** (all async, all taken under `_lock`):

- `async_load()` — load and validate the store, or start empty with a warning.
- `async_register_entry(entry_id)` — add to `_pending_adoption`; called during
  `async_setup_entry` before the first refresh.
- `async_adopt(request)` — record an observed code as this reservation's
  allocation, or as a conflict when another owner already holds it.
- `async_rekey(old_key, new_key)` — move an allocation to a rematched identity.
- `async_allocate(request)` — return the existing code, else the preferred code,
  else the first free candidate, else report exhaustion.
- `async_sweep(entry_id, active_keys, observed_codes)` — release allocations for
  reservations that are gone and whose codes are not observed.
- `async_mark_entry_removed(entry_id)` — abort pending adoption for a removed
  entry and mark its allocations orphaned without releasing codes.
- `async_clear_orphans(known_entry_ids, observed_codes, dry_run)` — operator
  cleanup of allocations whose owning entry no longer exists, using that same
  guard, returning an `OrphanCleanupReport`.

**Validation rules**:

- No operation may return a code recorded to a different `identity_key` (FR-005).
- Loaded codes must be decimal digits whose length equals their stored
  `code_length`; any mismatch corrupts the whole payload (FR-018).
- `async_allocate` for a known `identity_key` returns the same code and performs
  no registry mutation (FR-007).
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

- `by_identity` is rebuilt from `records` on load; a payload where the two
  disagree is treated as corrupt and discarded (FR-018).
- A code with more than one owner is a conflict and is unavailable for issuance
  until it has exactly zero or one owner again (FR-022).
- A code whose length differs from the requesting entry's configured length is
  never offered to that entry (FR-012); it still blocks that exact string.
- Changing an entry's `code_length` while it has active allocations is rejected
  by the options flow; otherwise FR-012 and FR-017 would conflict.

### AllocationRecord

One issued or observed code.

**Fields**:

- `code: str` — the literal zero-padded code, in memory only. Persisted in
  obfuscated form as `encoded_code`; see the contract.
- `code_ref: str` — masked identifier for logs and diagnostics, derived from a
  different salt than the at-rest encoding and never a substitute for it.
- `encoding_salt_source: str` / `encoding_salt_value: str` — which captured
  value salts the at-rest encoding, fixed for the record's lifetime.
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
- `lock_observed: bool` — the code was seen on a managed slot on the most recent
  observation, which forbids release (FR-014).
- `first_seen` / `last_seen` — ISO-8601 timestamps used by the sweep.

### AllocationRequest / AllocationResult

**Request fields**: `entry_id`, `identity_key`, `preferred_code`, `code_length`,
`fingerprint_history`.

**Result fields**: `code: str | None`, `origin: AllocationOrigin | None`,
`reason: str | None` where `reason` is one of `"exhausted"`,
`"adoption_pending"`, `"unaccounted_slots"`, or `None`.

A result with `code is None` is the fail-closed state. It is never an error and
never raises; the caller records it and the reservation holds.

### RegistryStore

**Owner module**: `custom_components.rental_control.allocator.store`

Wraps `homeassistant.helpers.storage.Store` at key
`rental_control.code_registry`, schema version 1. It is the only component that
converts between plain in-memory codes and their obfuscated `encoded_code` form:
encode on save, decode on load, never elsewhere. The obfuscation is Keymaster's
salted base64 and is not a security boundary; see the contract. Load failures of
any kind (absent, unreadable, wrong version, malformed, undecodable) resolve to
an empty registry plus a warning and a persistent notification. Saves use
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
records a `reason` of `code_still_programmed` or `adoption_conflict`. No field
carries a code in any form.

## Changed entities

### Reservation (`reconciliation/plan_models.py`)

- `slot_code: str` becomes `slot_code: str | None`.
  - `str` — the allocator's code for this reservation; identical to what will be
    written to the lock and shown by the sensor.
  - `None` — no code is available this cycle. The planner must hold the slot.
- `code_source` gains `"allocated"`, `"collision_resolved"`, `"adopted"`, and
  `"unallocated"`, alongside today's `"generated"` and `"manual_observed"`.
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
