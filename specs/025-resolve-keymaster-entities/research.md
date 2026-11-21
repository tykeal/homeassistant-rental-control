<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Verified Keymaster Identity

Evidence checked 2026-11-20 against live upstream tags and HA 2026.11.2
source. A source tree is not the installed release archive: tagged KM
manifests contain `v0.0.0`, while the release workflow substitutes the tag in
the packaged artifact. The spec records archive verification. Runtime must use
loaded HA integration metadata, not tags, filenames, reset buttons or RC's
own version.

## Stable identity and operations

**Decision**: For supported property keys, compute
`<config_entry.entry_id>_<ha_slugify(property_key)>`, lookup the exact
`(domain, platform=keymaster, unique_id)` and independently verify registry
entry ownership by the selected config entry. Require an enabled entity on a
loaded entry and operation-specific live readiness. Treat missing, wrong
owner/platform/property, disabled and conflicting candidates as distinct
unusable conditions. Test actual HA slugification rather than hardcoding a
guessed resulting string.

**Rationale**: Both [v0.6.0 entity source](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/entity.py)
and [v0.6.5 entity source](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/entity.py)
derive unique IDs from the entry and slugified property; current entity IDs and
device areas are mutable. The registry indexes domain/platform/unique ID.
See the explicit property table in [contract](contracts/integration.md).

**Alternatives considered**: Name-shaped entity IDs, device labels, and first
match are rejected as unsafe. A device identifier alone does not establish
slot/property identity. A whole-lock reset cannot replace per-slot reset.
KM's [v0.6.0 services](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/services.py)
and [v0.6.5 services](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/services.py)
register Lovelace regeneration; PIN service declarations are commented out.
Continue existing HA entity-service operations, not invented KM PIN services.

## Events, registry and startup

**Decision**: Track HA registry and config-entry lifecycle separately, with a
generation fence, explicit rebind/unsubscribe and fresh live observations.
For lock events require the source's physical lock entity and use lockname
only as corroboration/disambiguation, never as sole identity. Verify
parent/child linkage and inheritance before attributing child check-in.

**Rationale**: [HA bus](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/core.py)
`async_listen` returns an unsubscribe callable; events can be queued and
callbacks can outlive an old mapping. In the
[entity registry](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/entity_registry.py),
create/remove events contain action and entity ID; update also includes
old values in `changes`, and rename supplies `old_entity_id` with the new
`entity_id`. Disabled entries remain registered; deletion emits a remove and
may later restore the identity. The
[registry tracker](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/event.py)
keys callbacks by entity ID and dispatches a rename by its old ID; inference:
it does not automatically re-key subsequent events. Test with actual registry
renames and the real tracker, not just invented event payloads.
[Config-entry changes](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/config_entries.py)
use a dispatcher signal, not the HA event bus; entry state hooks, entity
registry events and live state transitions must be coordinated without
relying on a fictitious config-entry bus event. Verify chosen public APIs on
the minimum supported HA runtime.

KM [v0.6.0](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/coordinator.py)
and [v0.6.5](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/bus_events.py)
emit `keymaster_lock_state_changed` with physical `entity_id`, `lockname`,
state and slot, but no KM entry ID; reset event is separate. Shared physical
addresses may therefore be unattributable and must be refused unless uniquely
disambiguated with verified nonconflicting fields. Event payload metadata and
slot names are not authentication. See spec for both release payloads.

**Alternatives considered**: Startup-only resolution, polling alone, name
parsing, trusting old IDs on a state callback, and assuming a reset event
confirms a clear all fail races or identity safety.

## Clear evidence and physical limits

**Decision**: Require a present current live name and PIN record on verified,
enabled controls after the relevant generation, with no restored marker,
unavailable value or synthetic mask. For these text controls only, preserve
trimmed case-insensitive empty string, `unknown`, and `none` as empty. A live
initial `unknown` is valid for an available native `None`; no transition is
required. Separate local clear, pending-slot fence, and allocator release.

**Rationale**: Baseline release entities publish native values after
availability; HA turns native `None` into `unknown`. A missing state record
is not `None`. Provider clear failure can still yield empty KM text; this is
not proof of physical erasure. Restored placeholders and stale cached
observations cannot prove a current slot. Synthetic whitespace, `none`, masks
and restored `unknown` are compatibility/negative fixtures, not claims about
native baseline output.

**Alternatives considered**: Require every empty value to transition
(`unknown` may already be live), count a reset service/event, or free PINs
directly on empty text: each violates existing recovery or release safeguards.

## Migration and shared accounting

**Decision**: Separate durable selected identity from legacy selectors;
retain unknown legacy provenance. For legacy-unknown ownership only, the
maintainer accepts an explicit operator attestation that a selected,
distinguishable current KM entry is attached to the same original physical
lock, even if the original KM entry was deleted and recreated. Record the
assertion and selected current entry durably for audit. This is operator
historical evidence, not machine proof of physical continuity; current entry,
capability and live slot evidence still require independent validation. A
present slug, name, entry ID or matching address does not prove that the
physical lock is the same. Never use attestation to transfer work already
known to belong to entry A onto B, even if both entries refer to the same
physical lock. Coordinate shared owners and aliases before changing identity
equivalence.

The v0.6.5 [entity mapping](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/entity.py)
confirms what can be checked now: entity unique IDs and device identifiers
derive from the current KM config-entry ID, while [entry setup](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/entry_setup.py)
supplies the current `lock_entity_id`. [Lock events](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/bus_events.py)
carry that physical entity address but no KM entry ID. These values establish
the current entry/entity/source relationship, not historical continuity from
a deleted KM entry to a recreated one. The explicit operator assertion
supplies that missing legacy history; the software must not claim to verify
the physical identity independently.

**Rationale**: RC `allocator/store.py` schema v1 records owners' RC entry,
booking identity, lockname and slot, but not KM config-entry ID. It uses
`async_delay_save(..., 1)`. EventOverrides `_pending_fences` and
`_pending_clear_slots` are in memory. The separate per-entry slot-mapping
store is explicitly a cache: it can be lost, and save failures are caught.
Pending-clear status there is not a durable original KM identity/fence.
Legacy booking aliases or a current observed PIN may associate a reservation
with a slot but cannot prove which historical KM entry was selected. A name
collision or reselection cannot justify moving a hold. Existing exclusion only
narrows the issuance gate; raw unreadable observations still protect release.

**Alternatives considered**: Rewrite all locknames to entry IDs immediately,
transfer work to the selected replacement, discard unattributable records,
or make each slot independently unblock by default. These lose continuity or
change approved safety policy.

## Awaited durability is not HA Store success

**Decision**: Store the authoritative complete ledger and protection
transactions together in a private HA-compatible version-2 envelope at the
existing registry key, serialized under a single integration-wide async
lock. Rental Control owns bounded raw-byte reads and writes for both the
registry and activation marker; do not route either path through HA
`Store.async_load`, `load_json`, `write_utf8_file_atomic`, or Store save /
quarantine behavior. A transaction covers the pre-dispatch original
entry/slot fence and protected ledger/owner state without relying on the
mapping cache or delayed config-entry save. Failure and restart validation
remain implementation gates; this is a plan, not an already proven
implementation.

**Rationale**: In [HA 2026.11.0 Store](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/helpers/storage.py)
and [2026.11.2 Store](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/storage.py),
`async_save` normally awaits the write but clears the pending payload before
the write, catches `SerializationError` and `WriteError`, logs and returns;
read-only stores do not write. During shutdown it schedules final-write and
returns before writing. `async_load` can return pending in-memory data, so
read-after-save through the same Store is not proof of disk durability.
On invalid JSON, `async_load` can move the corrupt file aside and return
`None`; this is not evidence that no v2 state ever existed. HA 2026.11.0
also logs the storage key, original and quarantine paths, and exception
text, then publishes those paths and error text in its repair issue. Using
it for the new registry or marker path would both destroy the distinction
between absence and corruption and bypass RC's privacy boundary. The RC
reader must instead open each fixed path directly in the executor, reject
symlinks/non-regular files, read at most 32 MiB of envelope or 64 KiB of
marker plus one byte, and decode/validate only the bounded bytes. Accept
exactly-at-limit input; limit+1 is protective-unknown. Bound each
`.corrupt.*` evidence or temporary-file directory scan to 4,096 entries; if
an absence scan cannot complete, classify unknown rather than absent, while
cleanup leaves all candidates untouched. Count every directory entry,
including unrelated files, examine at most the 4,097th to detect overflow,
and require end-of-directory before declaring a scan complete or deleting
any candidate. Where available use `O_NOFOLLOW` with descriptor `fstat`;
on Windows reject reparse traversal of every component with verified
handle-based no-follow traversal, or fail closed without disk access.
ACLs or checking only the final component are not sufficient. Test the
actual platform behavior; do not assume safe-open equivalence.
Only a verified `ENOENT` for both paths with no activation, partial-write,
or quarantine evidence can mean true absence. If the bounded quarantine
evidence scan cannot complete, absence is unproven and the result is
protective-unknown. Permission/I/O errors, oversize input, invalid UTF-8/JSON,
malformed data, and unsupported versions are distinct sanitized
protective-unknown outcomes; never move, delete, or silently replace the
evidence. Existing RC registry-loss adoption cannot reconstruct fences,
attestations or tombstones.
The inspected HA 2026.11.0 `Store.__init__` defaults
`max_readable_version` to its configured major version. In
`Store._async_load_data`, a decoded empty object returns `None` on the disk
load path; otherwise a
missing `minor_version` defaults to 1, an exact version/minor match returns
the `data` field, and a different major version is compared against
`max_readable_version` before the migration callback receives `data`.
For a version/minor mismatch, HA takes its migration path (with its
same-major `NotImplementedError` fallback) and saves the result; it is not
equivalent to exact-match loading.
The new bounded reader distinguishes a present `{}` or invalid wrapper from
absence and never writes while reading. Preserve v1 missing-minor default
and valid same-major minor compatibility, without invoking HA's write-on-read
path. HA's loader does not require the wrapper `key`; new legacy validation
must not introduce that requirement. Validate v1 payload and v2 safety
schema independently rather than adopting malformed or unknown data.
Therefore the actual v4.0.3 `Store(version=1)` rejects a decoded integer
top-level `version: 2` before inspecting its inner payload, even when that
payload is missing or malformed. The loader is not a general schema/type
validator: malformed types can fail during the version comparison/indexing.
`Store._FileStoreIO._load` calls
[`homeassistant.util.json.load_json`](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/util/json.py),
which reads the entire file before `orjson.loads`; missing-file `FileNotFoundError`
is debug-logged and returned as `{}`, while JSON and other `OSError`
failures are exception-logged with the filename and wrapped as
`HomeAssistantError`. Only when HA's file IO wraps a `JSONDecodeError` does
this Store path rename the file to `.corrupt.*`, log the storage
key/original/quarantine paths and error, publish the same error/path values
in a fixable issue, and return `None`. These exact old-wrapper behaviors are
characterized against HA 2026.11.0 source and apply only to the v4.0.3
downgrade path; the new reader
must bypass it.

The [HA storage-corruption translation](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/components/homeassistant/strings.json)
describes the quarantined path and recommends correcting manually edited JSON
and restoring the file/restarting, or restoring the system from backup, then
submitting the repair. HA's generic
[repair confirmation flow](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/components/repairs/issue_handler.py)
confirms and removes the issue; it does not restore or validate the file.
Rental Control v4.0.3 has no custom repair flow. Therefore, issue confirmation
alone is not evidence that a complete allocator envelope and matching marker
were recovered. Stop RC/HA and preserve evidence; reinstalling newer code
does not reconstruct missing state, and an external backup cannot be used to
resume without the independently verifiable evidence described below.
`atomic_writes=True` selects a stronger file write but does not stop Store
swallowing errors. Default writes use replace without the atomic helper's
fsync. Config entries also use delayed saves.

[HA's file helper](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/util/file.py)
in the inspected HA 2026.11.0 and
[2026.11.2](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/util/file.py)
calls `AtomicWriter`, then catches `OSError`, logs it with
`_LOGGER.exception("Saving file failed: %s", filename)`, and raises
`WriteError(error) from error`. The original exception and traceback are
already logged before Rental Control can sanitize the propagated error.
`homeassistant.helpers.json.save_json` likewise logs `SerializationError`;
do not use either logging wrapper for this registry.

The [HA serializer](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/helpers/json.py#L199-L233)
returns `(mode, json_data)` as bytes/`wb` normally and text/`w` for a custom
encoder. `prepare_save_json` itself does not log, but its `SerializationError`
can contain paths and values from the serialized object:
[`format_unserializable_data`](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/util/json.py)
formats entries as `path=value(type)`. Call it once on an immutable snapshot
in the executor, catch and sanitize serialization failures before they leave
that boundary, normalize `wb` bytes or UTF-8-encoded `w` text to one exact byte
string, and hash that string. Do not call `save_json` or pass raw exceptions
to another logger.

**Decision**: Use a small RC-owned adapter around
`atomicwrites.AtomicWriter`, provided by HA's runtime dependency; add no
package dependency. The project minimum is HA 2026.11.0, whose locked test
environment currently resolves `atomicwrites-homeassistant` 1.4.1. This is
not a Home Assistant public API: test the actual required behavior on the
minimum supported HA runtime and supported CI HA lines, rather than pinning
an exact Python signature. The adapter must fail closed if the import or
required behavior is unavailable; never fall back to a logging wrapper. The
[HA's AtomicWriter source](https://github.com/home-assistant-libs/python-atomicwrites-homeassistant/blob/1.4.1/atomicwrites/__init__.py)
creates its temporary file with `mkstemp` (private mode `0600` on POSIX),
flushes and fsyncs it, then atomically renames it over the target and fsyncs
the containing directory on POSIX; its Windows implementation uses
`MoveFileExW` with replace-existing and write-through flags. Write the
pre-hashed bytes in binary mode, with overwrite enabled, in the same
`.storage` directory. The lower-level writer has no logging wrapper.
On the minimum supported local HA 2026.11.0 environment, a functional probe
against `atomicwrites-homeassistant` 1.4.1 verified an adapter-provided
prefix, private POSIX temporary mode, exact-byte output and successful
same-directory overwrite; compatibility acceptance should assert these
behaviors rather than the observed method signature.

Use the fixed HA storage key, require `.storage` to be a real directory and
the target to be absent or a regular file, and fail closed on symlinked
parents or targets. Rename replaces rather than follows a destination
symlink; the HA-owned configuration directory remains the trusted path
boundary, not a defense against a same-user process able to replace that
directory concurrently. Coordinate direct registry/marker readers, writers
and cleanup through `.storage/rental_control.code_registry.io_lock`, a fixed
private, non-authoritative lock file using standard-library process-shared
locks (POSIX `flock`, Windows `LockFileEx` fixed-byte-range lock); never
rename/unlink/recreate the lock file while participants can run. All
participating RC writers hold the lock over disk read/compare/write and
compare the planned predecessor digest/generation under lock to reject stale
snapshots. Acquire the allocator async lock before the OS lock for every
in-process reader, writer and cleanup operation; release in reverse.
Use nonblocking `flock`/`LOCK_NB` or Windows `LockFileEx` with exclusive
and fail-immediately flags on the same one-byte range. The
[Windows API documentation](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex)
allows locking past EOF, so a newly created empty file requires no content
initialization and remains usable after a creator crash. Lock release after
process exit may be delayed. Confirm actual platform behavior with a second
process. Microsoft documents raw
[`ERROR_LOCK_VIOLATION` as 33 and `ERROR_ACCESS_DENIED` as 5](https://learn.microsoft.com/en-us/windows/win32/debug/system-error-codes--0-499-).
Python documents [`use_last_error=True` and `ctypes.get_last_error()`](https://docs.python.org/3/library/ctypes.html)
for the ctypes private Windows error copy: capture it immediately after a
failed `LockFileEx` through a correctly declared `WinDLL` binding. Classify
33 before Python errno conversion, and do not treat access-denied 5 as
contention. Native two-process and permission-failure tests are required;
they have not been run here. Unknown/unsupported errors are sanitized
unavailable. For unsafe or unowned lock paths, never repair by chmod/ACL
change; stop all HA/RC processes before operator repair and never replace the
path with participants active. Start one five-second monotonic deadline
before asynchronously acquiring the allocator lock; OS-lock retries use the
remaining time and never block the event loop. Contention yields
a static retryable busy result, unsupported/unsafe locking a sanitized
unavailable result; neither alone proves ledger corruption or causes
persistent protective-unknown. A cancelled waiter stops and drains; started
filesystem operations can still stall and must not be abandoned. This coordinates
participating RC disk operations and cleanup only, not hardware/allocator
work across HA processes; one HA process owns its config directory, and
non-participating legacy/external writers are outside the guarantee.
Verified Windows protected-operation support must not be claimed until native
lock and no-follow path tests pass; otherwise gate protected operations
while preserving calendar-only functionality, not removing platform support.

The current setup in `__init__.py` awaits shared allocator creation before
coordinator startup. Calendar `async_get_events` delegates to the coordinator,
whose `_async_get_events_impl` reads cached coordinator data; first refresh
nonetheless calls `_run_lockless_allocation`, which awaits
`code_allocation.async_resolve_codes` and `allocator.async_resolve_cycle`.
Calendar-only check-in has its own transition path in
`sensors/checkin/runtime.py`. Therefore the new busy **and** unsupported,
unsafe or unavailable lock paths must explicitly allow lockless setup,
first refresh, display and calendar-only check-in without raising, while
withholding shared issuance, reuse, release and new dependent hardware or
ownership dispatch. Cached display alone does not prove first-refresh
availability. Test held local/process locks and unavailable primitives or
unsafe paths with deterministic calendar input and an event-loop heartbeat.
The lock deadline excludes calendar network fetches
and filesystem calls already in flight; it is not a claim that all other
nonmutating reads avoid locks.
Have the adapter provide an exact, RC-owned
temporary-file prefix containing only a fixed integration stem, file kind
and opaque random operation ID; the lower-level writer supplies the unique
random filename. Validate this behavior with an actual write/replace test,
not an assumed method signature. After a successful, validated startup
recovery and while holding the shared writer lock with all executor workers
quiescent, perform bounded stale-temp cleanup: scan at most 4,096 directory
entries, and unlink only regular non-symlink files matching
the exact RC prefix and naming form with verified private ownership/ACL
(same owner and mode on POSIX; validated HA-directory ACL on Windows). Never
match registry/marker names or HA `.corrupt.*` files. If the scan limit,
path check, ownership or lock checks fail, leave every candidate in place
and surface only a static maintenance status. Use the same OS lock as every
participating writer; do not rely on process discovery or timing. Verify
with a second process holding the lock that cleanup cannot run concurrently.
Do not clean while state is protective-unknown, a transaction is pending,
or a writer is active; stale temps are never authoritative.

Preflight the exact serialized target envelope and both pending/final marker
bytes against the corresponding read caps before any transaction mutation;
oversize output cannot be acknowledged and must not dispatch dependent work.
For a valid v1 beside quarantined evidence, the approved policy is
verified-backup-only, not an operator override; no genuine verifier or
external recovery path is supported in this release. The
[data model](data-model.md#load-classification-and-protective-unknown-recovery)
requires a complete matching pair and independent authoritative evidence of
completeness through the incident; neither a markerless v1 backup nor a
digest or attestation proves it. The current allocator serializes a v1
snapshot via delayed `RegistryStore.async_save`; its payload has records,
owners and conflict bookkeeping, not an independent transaction/hardware-effect
journal. No rollback-resistant external witness or historical-backup
verification mechanism is established in this design. Snapshot consistency
and re-acknowledgement do not prove recency. A separate authoritative,
independently verifiable source covering the full interval and quiescence is
necessary; without it recovery is unavailable, rather than automatically
accepting a candidate or guessing from logs, attestations or live slots.
The maintainer chose to proceed without a staged release and keep affected
installations blocked potentially indefinitely. This release has no genuine
historical-backup verifier or supported external restore/override when
history cannot be verified; this does not assert verification is
mathematically impossible. Intact v1 upgrades and fresh installs with verified
absence remain supported under their existing validation and adoption gates.
An ordinary crash with validated predecessor/target bytes under the
single-writer/no-external-restore assumptions is a separate protocol; an
external rollback of both files can make the histories indistinguishable.
With no surviving contrary evidence, ordinary crash recovery may accept that
stale pair and omit later safety state, potentially permitting unsafe PIN
reuse; neither detection nor safe recovery is guaranteed. The maintainer
explicitly accepted this residual risk (`accept_residual`). Preventing
undetected rollback requires an independent outside record protected from
the same rollback, which this design does not provide. Known or suspected
rollback remains blocked. This can block legacy installations that never
activated v2 indefinitely; no external restore is supported in this release
and a different recovery policy requires separate approval.

Catch `Exception` failures from serialization, encoding, hashing, raw
reads/decoding, writer/replace/fsync, cleanup, executor submission and
worker result handling at their owning boundary, including `ValueError`,
`TypeError`, Unicode/JSON errors and errors carrying sensitive causes or
contexts. Return only typed failure results with fixed allowlisted stage
codes; callers map these to static sanitized status without retaining or
logging the exception. Never raise a sanitized replacement from inside an
exception handler, chain errors, expose a filename/traceback, or pass raw
exceptions to HA. Cancellation drains the shielded worker through repeated
cancellation requests; retain locks until its result is known, apply success
or protective-unknown state, suppress target-dependent dispatch after
cancellation, then propagate cancellation without exposing worker
exceptions. The executor acknowledgement is not success until the atomic writer exits,
including fsync steps. Any reported failure after possible rename, including
directory-fsync failure, is ambiguous: do not dispatch or treat visible
bytes alone as success. Keep protection and enter protective-unknown. On
restart, validate bounded raw bytes and marker/digest relationships, then
re-acknowledge the exact validated bytes with a successful atomic write
before finalizing or resuming; if any validation or re-acknowledgement fails,
remain protective-unknown. Hash the exact serialized bytes once, store that
digest in the marker, and compare it to exact bytes read from disk; never
re-serialize a decoded object to establish digest agreement.

A scoped in-memory probe against the locked HA 2026.11.0 package injected an
`OSError` containing only a synthetic PIN token into `AtomicWriter.open`;
without writing a file, the token appeared in the captured HA logger output,
the `WriteError`, and its cause. This confirms caller-side sanitization is too
late. The inspected 2026.11.0 and 2026.11.2 file-helper source has the same
logging and chaining behavior. This is source/probe evidence, not a permanent
acceptance condition on future HA behavior; runtime acceptance verifies the
RC path does not invoke those wrappers and does not leak synthetic data.
A second in-memory check against HA 2026.11.0 made
`prepare_save_json` fail on a synthetic unserializable object and confirmed
that the resulting `SerializationError` text contains its synthetic value.

Serialize immutable snapshots and prevent concurrent writers from
overwriting newer protective state. The envelope must preserve required
owner/code reservations without logging or duplicating raw PINs. A private
activation marker correlates generations/digests and detects
marker/envelope mismatches; it is not a ledger. Details, config correlation,
lock ordering and migration boundaries are in the data model. Pending markers
retain both prior committed and target digest/generation: after a process
crash a valid exact prior match permits retry only when target-dependent
dispatch was impossible; an exact target match permits finalization. Existing
prior operations remain separately fenced. Restoring those fences is
bookkeeping, not a hardware call; fresh verified original-slot observation
and the existing retry policy govern any later dispatch.

HA's public config-entry update exposes live state, not an acknowledged
durable config-save barrier. The default therefore retains association
history. Optional pruning based on a direct disk check is a dependency on
HA's private, versioned `core.config_entries` layout; a known-format check
and independent writer quiescence proof are both required. Unsupported or
unreadable layouts retain the chain and surface growth pressure.

**Alternatives considered**: Awaiting `Store.async_save`, enabling atomic
writes on that Store, loading it immediately after save, and using the
mapping cache fail the acknowledgement or authority requirements. Direct
atomic writing is not by itself a cross-file transaction; a recovery protocol
and explicit downgrade limitations are still necessary. The helper uses
fsync as an engineering choice, not a user-selected policy or a guarantee
against all storage hardware failures.

The maintainer chose a v4.0.3 downgrade guard. Its
[registry loader](https://github.com/tykeal/homeassistant-rental-control/blob/v4.0.3/custom_components/rental_control/allocator/store.py#L70-L101)
constructs HA Store version 1 and wraps its load errors as
`ConfigEntryNotReady`. HA rejects a higher envelope major version before
passing data to that reader. The guard checks the decoded top-level version
before the inner payload: any object with integer `version: 2` is rejected,
including one with missing or malformed payload data. A payload-only schema
bump instead becomes registry-lost recovery, while extra fields in v1 are
ignored and may be dropped on rewrite. Test the actual old loader with the new
envelope:
because [allocator initialization](https://github.com/tykeal/homeassistant-rental-control/blob/v4.0.3/custom_components/rental_control/__init__.py#L70-L86)
precedes entry setup, even lockless RC entries can be blocked. Recovery is
reinstalling compatible newer RC while retaining the intact v2 registry, not
deleting or restoring it. This does not promise a guard for all older versions
or removed/replaced files.
For malformed JSON, HA can quarantine the v2 file and return `None` before
checking its version; v4.0.3 sees an absent registry and may run adoption,
which does not reconstruct v2 safety metadata. Any default/fresh v1 registry
that v4.0.3 creates or writes after HA returns `None` for the quarantined file
is not restored safety state: the new reader classifies it alongside the
surviving activation marker as protective-unknown. That path is not a guard.
Reinstalling a compatible newer RC version alone cannot reconstruct missing
safety state: preserve the quarantined bytes and marker and remain
protective-unknown; this release has no supported external restore or
historical-backup verifier. In the new version, protective-unknown must not prevent
lockless entry setup or calendar displays, but the shared allocator withholds
new lockless code issuance until safety state is restored. This differs from
v4.0.3's top-level version-2-header downgrade block, which may prevent all RC
entries, including lockless entries, from loading.

## Child availability and event source

**Decision**: Verify inheritance for each property, not for the child as a
single availability flag. Attribute check-in using `lock_entity_id`,
`parent_entry_id`, actual current inheritance and nonconflicting event fields.

**Rationale**: Both baseline [button](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/button.py)
and [v0.6.5 button](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/button.py)
availability depends on connection and slot existence, not
`override_parent`: a child reset can be available while inherited text,
datetime and most switch controls are unavailable. The override and
notifications switches are exceptions to the normal child switch gate.
Never infer permission to perform or confirm reset from the button alone.
KM stores the physical `lock_entity_id` and `parent_entry_id` in config
entry data; in both baselines the parent entry ID can be backfilled by a
name-based match. Verify the resulting current topology instead of treating
that backfill as unquestionable historical proof. See
[v0.6.0 setup](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/__init__.py)
and [v0.6.5 setup](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/entry_setup.py).
KM may emit a later corrected slot event after an initial slot-zero event;
do not equate event count with one physical action. Lockname and slot
constants map to `lockname` and `code_slot_num` in both versions.

**Alternatives considered**: Assuming all child controls are unavailable or
using an available reset button as proof of the required inherited text
controls would each misrepresent the upstream contract.

## Version and diagnostics

**Decision**: Allow only unsuffixed `v?0.0.0` as development first; validate
three-number release shape and comparator-recognized SemVer/PEP 440 suffix
before comparing using HA's AwesomeVersion. If `< floor` true report outside
support, else if `>= floor` true report supported, otherwise unknown. A
comparison error is unknown. All statuses still require the same operation
identity, state and safety checks.

**Rationale**: HA's loaded integration metadata and comparator are the
authoritative installed evidence. The spec's probes show `v0.6.0+local` is
incomparable with stable and must be unknown, not automatically supported.
Release-shape validation rejects comparator-recognized calendar/bare versions.
Release notes: [v0.6.0](https://github.com/FutureTense/keymaster/releases/tag/v0.6.0),
[v0.6.5](https://github.com/FutureTense/keymaster/releases/tag/v0.6.5);
[release workflow](https://github.com/FutureTense/keymaster/blob/v0.6.5/.github/workflows/release.yaml).

**Alternatives considered**: Python tuple comparison, presence of reset
button, version-only blocking, or default-supported on failed comparison.
None implements FR-007. Restrict diagnostics to safe allowlisted fields and
avoid exception strings that may echo PINs.

HA 2026.11.2 [pins awesomeversion 25.8.0](https://github.com/home-assistant/core/blob/2026.11.2/requirements.txt)
and [loader.py](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/loader.py)
imports it directly. RC does not declare it in its own manifest. The plan
uses the HA-provided comparator and must verify the loaded integration
metadata API at implementation time; it does not add a speculative new
dependency or assert a separate RC version.
