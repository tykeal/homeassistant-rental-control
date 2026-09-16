<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Code Registry Store and Allocator API

Two contracts are introduced. Neither is an HTTP, WebSocket, or Home Assistant
service API. The first is a durable on-disk schema; the second is the internal
Python API that every code-issuing path must go through.

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
        "code": "0417",
        "code_length": 4,
        "created_at": "2026-09-14T08:15:00-07:00",
        "updated_at": "2026-09-16T11:59:00-07:00",
        "owners": [
          {
            "entry_id": "01J9ZQ0K7F0000000000000000",
            "identity_key": "v1:...",
            "origin": "adopted",
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

### Field rules

- `schema_version` — must equal `1`. A higher value means the file was written
  by a newer release; the allocator refuses it, warns, and starts empty rather
  than risk misinterpreting records.
- `code_ref_salt` — random hex generated once on first save. Used only to derive
  the masked `code_ref` for logs and diagnostics. Losing it is harmless; a new
  one is generated and refs change.
- `records[].code` — the literal code string including leading zeros. This is
  the only place in the integration where a raw code is persisted, and the
  reason is stated in `plan.md`, design decision 4.
- `records[].code_length` — length at issue time, used to skip records that
  cannot belong to a requesting entry's space.
- `owners` — exactly one entry normally. Two or more means an adoption conflict
  (FR-022): reported, retained, never auto-resolved.
- `owners[].origin` — one of `preferred`, `collision_resolved`, `adopted`.
- `owners[].lock_observed` — true when the code was seen on a managed slot at
  the most recent observation of its entry. While true, the record must not be
  released (FR-014).

### Validation and failure behaviour

Any of the following make the payload unusable: missing or non-integer
`schema_version`, `schema_version` greater than 1, `records` not a list, a record
without a non-empty string `code`, a record with no owners, or a duplicate
`identity_key` across two different codes. In every such case the allocator logs
a warning, raises a persistent notification, and starts from an empty registry
(FR-018). It never partially loads.

### Compatibility

There is no prior version to migrate. A downgrade leaves the file in place
unread. The per-entry cache store
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
    def register_entry(self, entry_id: str) -> None: ...

    async def async_adopt(self, request: AdoptionRequest) -> AllocationResult: ...
    async def async_rekey(self, old_key: str, new_key: str) -> bool: ...
    async def async_allocate(self, request: AllocationRequest) -> AllocationResult: ...
    async def async_sweep(
        self,
        entry_id: str,
        active_keys: set[str],
        observed_codes: set[str],
    ) -> list[str]: ...
    async def async_release_entry(self, entry_id: str) -> list[str]: ...

    @property
    def diagnostics(self) -> dict[str, Any]: ...
```

### Behavioural guarantees

- **Serialization**: every method above mutates only under a single
  `asyncio.Lock`, so concurrent entries cannot both claim a code (FR-006).
- **Idempotency**: `async_allocate` with a known `identity_key` returns the same
  `code` with the recorded `origin` and mutates nothing (FR-007).
- **Uniqueness**: no method ever returns a code whose record has an owner with a
  different `identity_key` (FR-005).
- **Non-destructive**: no method returns, emits, or schedules a reconciliation
  action, and none calls a lock service. The allocator can never clear a slot.
- **Fail-closed, not fail-loud**: exhaustion, a closed adoption gate, and
  unaccounted unreadable slots all return `AllocationResult(code=None,
  reason=...)`. They do not raise.
- **Release safety**: `async_sweep` and `async_release_entry` skip any record
  whose code appears in `observed_codes` or whose owner has
  `lock_observed=True`, returning it as retained rather than released (FR-014).
- **No raw codes out**: `diagnostics` and every log statement expose `code_ref`,
  never `code` (FR-025).

### `AllocationResult.reason` values

| Value | Meaning | Caller behaviour |
|-------|---------|------------------|
| `None` | A code was issued or returned | Set `slot_code`, set `code_source` |
| `exhausted` | Every candidate in the space is taken | `slot_code = None`, warn once per cycle, notify operator |
| `adoption_pending` | Gate closed, new issuance deferred | `slot_code = None`, debug log, retry next cycle |
| `unaccounted_slots` | Unreadable slots the registry cannot account for | `slot_code = None`, warn, retry next cycle |

In every `code is None` case the caller sets `code_source = "unallocated"` and
the planner holds the slot without writing or clearing it.
