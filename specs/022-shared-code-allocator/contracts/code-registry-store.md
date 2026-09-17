<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Code Registry Store, Allocator API, and Cleanup Service

Three contracts are introduced: a durable on-disk schema, the internal Python
API that every code-issuing path must go through, and one Home Assistant service
for clearing orphaned allocations.

## 1. Persisted registry schema

**Store key**: `rental_control.code_registry`
**Schema version**: `1`
**Scope**: one file per Home Assistant system, shared by all config entries
**Location**: `.storage/rental_control.code_registry`

```json
{
  "version": 1,
  "key": "rental_control.code_registry",
  "data": {
    "schema_version": 1,
    "updated_at": "2026-09-16T12:00:00-07:00",
    "code_ref_salt": "b6f1c2d9e4a70351",
    "records": [
      {
        "encoded_code": "MDFKOVpRMEs3RjAwMDAwMDAwMDAwMDAwMDAwNDE3",
        "encoding_salt_source": "entry_id",
        "encoding_salt_value": "01J9ZQ0K7F0000000000000000",
        "code_length": 4,
        "created_at": "2026-09-14T08:15:00-07:00",
        "updated_at": "2026-09-16T11:59:00-07:00",
        "owners": [
          {
            "entry_id": "01J9ZQ0K7F0000000000000000",
            "identity_key": "v1:...",
            "origin": "adopted",
            "lockname": "front_door",
            "slot": 3,
            "lock_observed": true,
            "first_seen": "2026-09-14T08:15:00-07:00",
            "last_seen": "2026-09-16T11:59:00-07:00"
          }
        ]
      }
    ]
  }
}
```

### At-rest encoding

Door codes are not written as bare digits. Each record stores `encoded_code`,
produced by the same obfuscation Keymaster uses in
`custom_components/keymaster/serialization.py`:

```python
def encode_code(code: str, salt: str) -> str:
    return base64.b64encode(salt.encode("utf-8") + code.encode("utf-8")).decode("utf-8")

def decode_code(encoded_code: str, salt: str) -> str:
    salt_bytes = salt.encode("utf-8")
    raw = base64.b64decode(encoded_code, validate=True)
    if not raw.startswith(salt_bytes):
        raise ValueError("encoded code does not match stored salt")
    return raw[len(salt_bytes):].decode("utf-8")
```

**Salt source**: the `entry_id` of the record's *first* owner, captured when the
record is created and stored verbatim in `encoding_salt_value` for the record's
lifetime. `encoding_salt_source` is a provenance label naming where that value
came from (`entry_id`); it is not itself the salt. Decoding always uses
`encoding_salt_value`. A record that outlives its first owner (an orphan, or a
conflict whose first owner released) keeps the stored salt, so removing an entry
never breaks decoding; the salt is a byte prefix, not an ownership claim.

**This is obfuscation, not encryption, and not a security boundary.** The salt
is stored in the same file as the value it obfuscates, so anyone who can read
the file can recover every code with two lines of Python. Its only purpose is to
stop door codes appearing as greppable plaintext in `.storage` files, backup
archives, and casual filesystem browsing. It provides no protection whatsoever
against an attacker who has file access, and must not be described or relied on
as though it does.

**Scope**: at-rest only. In-memory registry values are plain strings, and
uniqueness comparison, candidate rejection, and everything else in
`registry.py` operate on plain values. Encoding happens on save and decoding on
load, nowhere else.

### Field rules

- `schema_version` — must equal `1`. Any other value is rejected before
  constructing the registry. A higher value means the file was written by a
  newer release; a lower or missing value is not a schema this release can
  interpret. The allocator refuses it, warns, and starts empty rather than risk
  misinterpreting records.
- `code_ref_salt` — random hex generated once on first save. Used only to derive
  the masked `code_ref` for logs and diagnostics. It is unrelated to the at-rest
  encoding salt and neither replaces the other. Losing it is harmless; a new one
  is generated and refs change.
- `encoded_code` — the obfuscated code, decoding to decimal digits whose length
  equals `code_length`, including leading zeros.
- `encoding_salt_value` — the literal salt bytes prefixed before the code, fixed
  at record creation. `encoding_salt_source` — a provenance label for that
  value; `entry_id` is the only source in schema version 1. Decode reads
  `encoding_salt_value`, never the label.
- `code_length` — length at issue time, used to skip records that cannot belong
  to a requesting entry's space. It is recorded separately so the registry can
  filter without decoding every record.
- `owners` — exactly one entry normally. Two or more means an adoption conflict
  (FR-022): reported, retained, never auto-resolved.
- `owners[].origin` — one of `preferred`, `collision_resolved`, `adopted`.
- `owners[].lockname` / `owners[].slot` — the managed lock and slot number the
  code is programmed on, or `null` for a lockless entry that has no physical
  slot. These are the physical identity the FR-014 release guard and the
  unreadable-slot accounting are evaluated against; without them neither rule is
  decidable.
- `owners[].lock_observed` — true when the code was seen on that lock and slot
  at the most recent observation of its entry. While true, the record must not
  be released (FR-014).

### Validation and failure behaviour

Any of the following make the payload unusable: missing or non-integer
`schema_version`, `schema_version` not equal to 1, `records` not a list, a
record whose `encoded_code` is missing, is not strict base64, does not decode to
bytes starting with that record's stored `encoding_salt_value`, or fails to
decode to decimal digits of exactly `code_length`, a record with no owners, an
`encoding_salt_source` the release does not recognise, two records whose
`encoded_code` values decode to the same code, or the same `identity_key` owned
by two different records.

The duplicate-decoded-code rule is not optional. The persisted form is a list
while the in-memory registry is keyed by the plain code, so without this check
one record silently overwrites the other on load and an owner — possibly one
holding a live guest code — disappears. Detect it before constructing the
registry, on the decoded values, and treat it as corruption of the whole
payload rather than dropping a record.

In every such case the allocator logs a warning naming the failing rule (with
`code_ref`, never a code), raises a persistent notification, and starts from an
empty registry (FR-018). It never partially loads, and it never repairs a
payload in place.

### Compatibility

There is no prior version to migrate, and nothing has shipped, so the schema
stays at version 1 including the at-rest encoding. A downgrade leaves the file
in place unread. The per-entry cache store
`rental_control.slot_mappings.<entry_id>` at `STORE_SCHEMA_VERSION` is untouched
by this feature and keeps its rule that raw PINs are never stored.

## 2. Allocator API

Consumers: `coordinator_helpers/code_allocation.py` (the only production caller)
and tests. Nothing else may issue a door code.

```python
async def async_get_or_create_allocator(hass: HomeAssistant) -> DoorCodeAllocator
def get_allocator(hass: HomeAssistant) -> DoorCodeAllocator | None


class DoorCodeAllocator:
    async def async_load(self) -> None: ...
    async def async_register_entry(self, entry_id: str) -> None: ...
    async def async_unregister_entry(self, entry_id: str) -> None: ...

    # Batch entrypoint: the only path production code uses.
    async def async_resolve_cycle(self, request: CycleRequest) -> CycleResult: ...

    # Phase methods: each takes the lock itself. Tests and diagnostics only.
    async def async_adopt(self, request: AdoptionRequest) -> AllocationResult: ...
    async def async_rekey(self, old_key: str, new_key: str) -> bool: ...
    async def async_allocate(self, request: AllocationRequest) -> AllocationResult: ...
    async def async_sweep(self, observation: CycleObservation,
                          active_keys: set[str]) -> ReleaseReport: ...

    async def async_mark_entry_removed(self, entry_id: str) -> ReleaseReport: ...
    async def async_clear_orphans(
        self,
        known_entry_ids: set[str],
        observations: list[CycleObservation],
        dry_run: bool = False,
    ) -> OrphanCleanupReport: ...

    @property
    def diagnostics(self) -> dict[str, Any]: ...
```

### Observation context

Both the FR-014 release guard and the FR-018 unreadable-slot rule are decided
against physical slot state, so that state has to reach the allocator. One value
carries it for an entry for one refresh cycle:

```python
@dataclass(frozen=True)
class CycleObservation:
    entry_id: str
    lockname: str | None          # None for a lockless entry
    managed_slots: frozenset[int]
    observed_codes: dict[str, int]   # plain code -> slot number, readable slots
    unreadable_slots: frozenset[int] # genuinely indeterminate managed slots
```

`code_allocation.py` builds it from what the coordinator already observed via
`keymaster_observation.py`; `unreadable_slots` is exactly the set of managed
slots whose observed `status is SlotStatus.UNKNOWN`. That includes missing or
unavailable entity state and slots with `blocked_reason="unreadable"`;
`blocked_reason` is diagnostic detail, not the discriminator. A
`SlotStatus.FREE` slot is known-empty even though `actual_code` is `None`, so it
must not be included. A lockless entry supplies empty sets.

From it the allocator derives, without any further input:

- **Release safety (FR-014)**: a record's owner is refreshed to
  `lock_observed=True` when its code is in `observed_codes`, or when the
  owner's `lockname` matches the observation's `lockname` and its `slot` is in
  `unreadable_slots` — an unreadable slot may still hold that code, so it
  counts as programmed.
- **Unaccounted slots (FR-018)**: `unreadable_slots` minus the slots claimed by
  registry owners with the same `entry_id` and `lockname`. A non-empty remainder
  means the entry has a slot whose contents nothing can account for, so a new
  code cannot be proven unique.

### Batch entrypoint

```python
@dataclass(frozen=True)
class CycleRequest:
    observation: CycleObservation
    adoptions: list[AdoptionRequest]
    rekeys: list[tuple[str, str]]          # (old_key, new_key)
    allocations: list[AllocationRequest]
    active_keys: set[str]


@dataclass(frozen=True)
class CycleResult:
    adopted: dict[str, AllocationResult]     # identity_key -> result
    allocated: dict[str, AllocationResult]   # identity_key -> result
    released: ReleaseReport
    unaccounted_slots: frozenset[int]
```

`async_resolve_cycle` acquires `_lock` once and runs adopt → rekey → allocate →
sweep against private, non-locking helpers, so one entry's whole cycle is atomic
against another's (FR-006). It **must not** call the public phase methods: the
lock is a plain non-reentrant `asyncio.Lock` and doing so would deadlock. The
public phase methods exist so tests can exercise one behaviour at a time; they
are not a supported way to compose a cycle.

`AllocationRequest` fields: `entry_id`, `identity_key`, `preferred_code`,
`code_length`, `fingerprint_history`, `previously_published: bool` (the
per-entry cache has durably recorded that this reservation's code was exposed
through the sensor or captive portal), plus `lockname` and `slot` for
lock-backed reservations. Lockless requests set both physical fields to
`None`. `issuance_allowed: bool` defaults to `True` and is set by
`async_resolve_cycle` from the derived unaccounted-slot set; a standalone
`async_allocate` call therefore never returns `unaccounted_slots`.

For a newly issued lock-backed code, the request's `lockname` and `slot` become
the owner's physical identity before the result is returned. The caller derives
them from the planned slot assignment before allocation, so a later unreadable
or missing observation can still be guarded by FR-014 instead of releasing an
unbound record.

`AdoptionRequest` carries `entry_id`, `identity_key`, the observed `code`,
`code_length`, and the `lockname` and `slot` it was observed on, which become
the owner's physical identity. If the same code is already owned by another
identity, adoption records a conflict owner, reports it, and never returns that
code for new issuance until the conflict is cleared.

`ReleaseReport` contains `code_ref`, `entry_id`, `identity_key`, and a
`released`/`retained` status with a retention reason. It never includes the raw
or encoded code.

### Behavioural guarantees

- **Serialization**: every method above mutates only under a single
  non-reentrant `asyncio.Lock`, so concurrent entries cannot both claim a code
  (FR-006). `async_resolve_cycle` holds it for the whole cycle.
- **Idempotency**: `async_allocate` with a known `identity_key` returns the same
  `code` with the recorded `origin` and mutates nothing (FR-007).
- **Uniqueness**: allocation never returns a code whose record has an owner with
  a different `identity_key`; adoption may record an observed conflict but does
  not make that code available for new issuance (FR-005, FR-022).
- **Non-destructive**: no method returns, emits, or schedules a reconciliation
  action, and none calls a lock service. The allocator can never clear a slot.
- **Fail-closed, not fail-loud**: exhaustion, a closed adoption gate, unaccounted
  unreadable slots, and post-loss recovery all return
  `AllocationResult(code=None, reason=...)`. They do not raise.
- **Release safety**: `async_sweep`, `async_mark_entry_removed`, and
  `async_clear_orphans` all evaluate one shared guard helper, whose only inputs
  are the record's owners and the supplied `CycleObservation` values. A record
  is retained when any owner has `lock_observed=True` after refresh, when an
  observation for the same `lockname` reports that owner's `slot` as
  unreadable, or when no supplied observation covers its `lockname` at all. The
  rule is defined once and not reimplemented per caller
  (FR-014).
- **Entry lifecycle**: `async_register_entry` adds to the adoption pending set;
  `async_unregister_entry` removes an entry that failed setup, was disabled, or
  was unloaded before completing a pass, so a broken entry cannot hold the gate
  shut indefinitely. Both are idempotent.
- **No codes out**: `diagnostics`, the cleanup report, and every log statement
  expose `code_ref`, never the code in plain or encoded form (FR-025).

### `AllocationResult.reason` values

| Value | Produced by | Meaning | Caller behaviour |
|-------|-------------|---------|------------------|
| `None` | both | A code was issued or returned | Set `slot_code`, set `code_source` |
| `exhausted` | both | Every candidate in the space is taken | `slot_code = None`, warn once per cycle, notify operator |
| `adoption_pending` | both | Gate closed, new issuance deferred | `slot_code = None`, debug log, retry next cycle |
| `unaccounted_slots` | `async_resolve_cycle` only | Unreadable slots the registry cannot account for | `slot_code = None`, warn, retry next cycle |
| `recovery_fail_closed` | both | Registry was lost; this reservation may already have been published and has no recovered code | `slot_code = None`, warn, never replace |

`unaccounted_slots` is derivable only inside `async_resolve_cycle`, which has the
`CycleObservation`; a standalone `async_allocate` has no slot context and never
produces it. `recovery_fail_closed` applies when `async_load` started from an
empty registry after a load failure and the request has
`previously_published=True` with no adopted or recovered code — see FR-018 and
decision 7 in the plan.

In every `code is None` case the caller sets `code_source = "unallocated"` and
the planner holds the slot without writing or clearing it.

## 3. Service: `rental_control.clear_orphaned_codes`

Resolves FR-004's operator recovery path. A service is not operator-supplied
configuration under FR-024, whose target is values that must be kept consistent
across config entries; this is a manual, on-demand operator action with no
persisted setting.

**Scope**: orphaned allocations only — records whose owning `entry_id` is no
longer a loaded or configured Rental Control config entry. It does not touch
allocations of live entries, does not resolve adoption conflicts, and does not
re-issue anything. Force re-issue remains #735.

### Registration

Registered once alongside the allocator singleton, in `allocator/services.py`
called from `async_get_or_create_allocator`, guarded by
`hass.services.has_service(DOMAIN, "clear_orphaned_codes")` so a second config
entry does not re-register it. It is a domain service rather than an entity
service because it operates on system-wide state with no entity target; the
existing `checkout` and `set_state` entity services on the sensor platform are
unchanged.

Declared in `services.yaml`, `strings.json` under `services`, and both
`translations/en.json` and `translations/fr.json`, matching the existing pattern
for `checkout` and `set_state`.

```yaml
clear_orphaned_codes:
  fields:
    dry_run:
      required: false
      default: false
      selector:
        boolean:
```

`supports_response=SupportsResponse.OPTIONAL`, so an automation may call it
without consuming a response while a developer-tools caller sees the full report.

### Behaviour

1. Compute the orphan set: records with at least one owner whose `entry_id` is
   not in `hass.config_entries.async_entries(DOMAIN)`.
2. Collect a fresh `CycleObservation` from every currently loaded entry and
   apply the shared FR-014 guard helper — the same function `async_sweep` uses,
   not a parallel implementation. An orphan owner's stale `lock_observed=True`
   is cleared only when a supplied observation covers its `lockname` and shows
   the code absent from a readable slot.
3. An orphan whose `lockname` is `null` (a lockless entry) has nothing
   programmed anywhere and is releasable immediately.
4. An orphan whose `lockname` is covered by no loaded entry cannot be verified
   either way. It is retained as `unverifiable_lock`, not released — the
   operator's remedy is to clear the slot physically or re-add the entry. This
   is deliberately conservative: a retained orphan costs one unusable code, a
   wrongly released one can hand a live guest's code to somebody else.
5. When `dry_run` is true, report what would happen and change nothing.
6. Otherwise release the unrefused records, save the registry, and report.

The service is idempotent: a second call with nothing left to clear releases
nothing and reports an empty `cleared` list. It is safe to run at any time,
including mid-refresh, because it takes the same allocator lock as every other
mutating operation.

### Response

```json
{
  "dry_run": false,
  "cleared": [
    {"code_ref": "3f9a1c04", "entry_id": "01J9...", "identity_key": "v1:..."}
  ],
  "retained": [
    {
      "code_ref": "a71b0e52",
      "entry_id": "01J8...",
      "identity_key": "v1:...",
      "reason": "code_still_programmed"
    }
  ]
}
```

`reason` is one of `code_still_programmed` (the FR-014 guard saw the code, or its
slot was unreadable), `unverifiable_lock` (no loaded entry observes that lock, so
its state is unknown), or `adoption_conflict` (the code has more than one owner
and must go to #735). The
same summary is logged and, when anything was retained, raised as a persistent
notification so the operator learns why without re-running the service. No raw
code appears in the response, the log, or the notification.
