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
retain unknown legacy provenance. The maintainer accepts an explicit
same-original-entry/association operator attestation when independent
historical proof is absent. It is distinct from merely selecting a current
entry and must be persisted before binding old holds. Do not equate present
slug uniqueness with proof of historical selection. Never use attestation
to transfer known-original A work to a replacement B. Coordinate shared
owners and aliases before changing identity equivalence.

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
lock. Await HA's atomic file helper in an executor with explicit failure
propagation. A transaction covers the pre-dispatch original entry/slot fence
and protected ledger/owner state without relying on the mapping cache or
delayed config-entry save. Failure and restart validation remain
implementation gates; this is a plan, not an already proven implementation.

**Rationale**: In [HA 2026.11.0 Store](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/helpers/storage.py)
and [2026.11.2 Store](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/storage.py),
`async_save` normally awaits the write but clears the pending payload before
the write, catches `SerializationError` and `WriteError`, logs and returns;
read-only stores do not write. During shutdown it schedules final-write and
returns before writing. `async_load` can return pending in-memory data, so
read-after-save through the same Store is not proof of disk durability.
On invalid JSON, `async_load` can move the corrupt file aside and return
`None`; this is not evidence that no v2 state ever existed. Existing RC
registry-loss adoption cannot reconstruct fences, attestations or
tombstones. The new reader classifies corruption, incompatible/missing
safety data and activation-marker mismatches as protective-unknown.
`atomic_writes=True` selects a stronger file write but does not stop Store
swallowing errors. Default writes use replace without the atomic helper's
fsync. Config entries also use delayed saves.

[HA's file helper](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/util/file.py)
`write_utf8_file_atomic` documents atomicwrites plus fsync and propagates
`WriteError`. Called with `private=True` in an executor, it supplies the
planned failure-detecting write; do not call it on the event
loop or make an unconditional claim about arbitrary disk hardware.
[HA's `prepare_save_json`](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/json.py#L199-L233)
returns `(mode, json_data)` with bytes/`wb` normally and text/`w` for a
custom encoder. Pass the mode to the file helper and hash the exact bytes
written, rather than reserializing data to calculate a digest.
Serialize snapshots and prevent concurrent writers from overwriting newer
protective state. Any error including uncertain post-replace failure means no
hardware dispatch; keep protection and reconcile on restart. The envelope
must preserve required owner/code reservations without logging or duplicating
raw PINs. A private activation marker correlates generations/digests and
detects marker/envelope mismatches; it is not a ledger. Details, config
correlation, lock ordering and migration boundaries are in the data model.
Pending markers retain both prior committed and target digest/generation:
after a process crash a valid exact prior match permits retry only when
target-dependent dispatch was impossible; an exact target match permits
finalization. Existing prior operations remain separately fenced. Restoring
those fences is bookkeeping, not a hardware call; fresh verified original
slot observation and the existing retry policy govern any later dispatch.

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
passing data to that reader. A payload-only schema bump instead becomes
registry-lost recovery, while extra fields in v1 are ignored and may be
dropped on rewrite. Test the actual old loader with the new envelope:
because [allocator initialization](https://github.com/tykeal/homeassistant-rental-control/blob/v4.0.3/custom_components/rental_control/__init__.py#L70-L86)
precedes entry setup, even lockless RC entries can be blocked. Recovery is
reinstalling compatible newer RC, not deleting/restoring the registry. This
does not promise a guard for all older versions or removed/replaced files.
For malformed JSON, HA can quarantine the v2 file and return `None` before
checking its version; v4.0.3 sees an absent registry and may run adoption,
which does not reconstruct v2 safety metadata. That path is not a guard.

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
