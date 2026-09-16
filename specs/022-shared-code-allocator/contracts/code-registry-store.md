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

### At-rest encoding

Door codes are not written as bare digits. Each record stores `encoded_code`,
produced by the same obfuscation Keymaster uses in
`custom_components/keymaster/serialization.py`:

```python
def encode_code(code: str, salt: str) -> str:
    return base64.b64encode(salt.encode("utf-8") + code.encode("utf-8")).decode("utf-8")

def decode_code(encoded_code: str, salt: str) -> str:
    raw = base64.b64decode(encoded_code)
    return raw[len(salt.encode("utf-8")):].decode("utf-8")
```

**Salt source**: the `entry_id` of the record's *first* owner, captured when the
record is created and recorded in `encoding_salt_source` for the record's
lifetime. `entry_id` is immutable for the life of a config entry and is already
present in the record, so decoding needs no extra lookup. A record that outlives
its first owner (an orphan, or a conflict whose first owner released) keeps the
original salt; the salt is a byte prefix, not an ownership claim.

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

- `schema_version` — must equal `1`. A higher value means the file was written
  by a newer release; the allocator refuses it, warns, and starts empty rather
  than risk misinterpreting records.
- `code_ref_salt` — random hex generated once on first save. Used only to derive
  the masked `code_ref` for logs and diagnostics. It is unrelated to the at-rest
  encoding salt and neither replaces the other. Losing it is harmless; a new one
  is generated and refs change.
- `encoded_code` — the obfuscated code, decoding to the literal code string
  including leading zeros.
- `encoding_salt_source` — which value was used as the encode salt. `entry_id`
  is the only value in schema version 1; the field exists so a future change of
  salt source can be read unambiguously.
- `code_length` — length at issue time, used to skip records that cannot belong
  to a requesting entry's space. It is recorded separately so the registry can
  filter without decoding every record.
- `owners` — exactly one entry normally. Two or more means an adoption conflict
  (FR-022): reported, retained, never auto-resolved.
- `owners[].origin` — one of `preferred`, `collision_resolved`, `adopted`.
- `owners[].lock_observed` — true when the code was seen on a managed slot at
  the most recent observation of its entry. While true, the record must not be
  released (FR-014).

### Validation and failure behaviour

Any of the following make the payload unusable: missing or non-integer
`schema_version`, `schema_version` greater than 1, `records` not a list, a record
whose `encoded_code` is missing or fails to decode to a non-empty string, a
record with no owners, an `encoding_salt_source` the release does not recognise,
or a duplicate `identity_key` across two different codes. In every such case the
allocator logs a warning, raises a persistent notification, and starts from an
empty registry (FR-018). It never partially loads.

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
    async def async_clear_orphans(
        self,
        known_entry_ids: set[str],
        observed_codes: set[str],
        dry_run: bool = False,
    ) -> OrphanCleanupReport: ...

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
- **Release safety**: `async_sweep`, `async_release_entry`, and
  `async_clear_orphans` all skip any record whose code appears in
  `observed_codes` or whose owner has `lock_observed=True`, returning it as
  retained rather than released (FR-014). They share one guard helper; the rule
  is not reimplemented per caller.
- **No codes out**: `diagnostics`, the cleanup report, and every log statement
  expose `code_ref`, never the code in plain or encoded form (FR-025).

### `AllocationResult.reason` values

| Value | Meaning | Caller behaviour |
|-------|---------|------------------|
| `None` | A code was issued or returned | Set `slot_code`, set `code_source` |
| `exhausted` | Every candidate in the space is taken | `slot_code = None`, warn once per cycle, notify operator |
| `adoption_pending` | Gate closed, new issuance deferred | `slot_code = None`, debug log, retry next cycle |
| `unaccounted_slots` | Unreadable slots the registry cannot account for | `slot_code = None`, warn, retry next cycle |

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
2. For each, apply the shared FR-014 guard. A record whose code is observed on
   any managed lock, or whose owner still has `lock_observed=True`, is refused.
3. When `dry_run` is true, report what would happen and change nothing.
4. Otherwise release the unrefused records, save the registry, and report.

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

`reason` is one of `code_still_programmed` (the FR-014 guard refused it) or
`adoption_conflict` (the code has more than one owner and must go to #735). The
same summary is logged and, when anything was retained, raised as a persistent
notification so the operator learns why without re-running the service. No raw
code appears in the response, the log, or the notification.
