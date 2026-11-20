<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Data Model: Verified Lock Identity

These are logical records and invariants, not a prescribed Python class or an
unreviewed rewrite of existing persisted structures.

## Association

| Field | Meaning and validation |
|---|---|
| RC entry ID | Existing calendar/config entry identity |
| mode | Explicitly lockless, legacy unresolved, verified, disabled, missing, or ambiguous; unresolved is never lockless |
| KM entry ID | Durable verified selected entry; absent only for lockless or unresolved legacy state |
| legacy lockname | Retained for compatibility, repair and supported selectors; not runtime authorization |
| range | Existing start/count; never widened by discovery or parent/child events |
| generation | Runtime monotonic invalidation token; not proof of persistence or live state |

Multiple RC entries may select the same KM entry with disjoint ranges. An
automatic legacy normalized match must be unique including disabled entries
and provably intended; distinguish same-named choices during repair. A verified
removed entry is not replaced by a same-named entry. Selection of another
entry invalidates observations; selection alone never transfers existing
claims.

**Positive evidence boundary**: a previously persisted, verified KM
config-entry ID associated with this RC entry is positive stable identity
evidence; it must still exist, belong to KM and pass current checks. An
explicit operator selection establishes the new intended target for future
work, but alone does not establish that old claims, exclusions or pending
clears belong to that target. Current legacy code persists only the normalized
lockname, not a KM ID; slot/booking mapping and matching PIN are not proof of
historical KM identity. A current unique match, including disabled entries,
is necessary but not sufficient for automatic migration. Where no
independent proof exists for **legacy-unknown** provenance, the maintainer
approves a **separate, explicit operator attestation** that the selected,
distinguishable current Keymaster entry is attached to the same original
physical lock. This may be true even when the original Keymaster entry was
deleted and recreated with a new entry ID. Record the assertion, its legacy
scope and selected current entry ID durably for audit. The assertion is
operator-supplied historical evidence, not machine proof of physical
continuity or fresh live slot evidence. A name, slug, ID, matching address or
entry selection alone is not attestation or proof of physical identity. The
selected current entry and its capabilities still require independent current
validation. Preserve automatic migration when genuinely independently
verifiable; do not require universal reselection by assumption.

An attestation does not transfer a hold already known to belong to entry A
onto replacement entry B, even if both entries refer to the same physical
lock. Attestation only binds legacy-unknown provenance; it neither verifies
live states, confirms a clear, nor authorizes release. Current evidence from
the attested original physical lock and exact slot remains required for
release. Uncertainty or ambiguity retains the hold and fails closed.

## Slot capability and observation

Identity is `(KM entry ID, platform, exact supported property key, slot)`;
the exact unique ID is derived by HA slugification of the property key.
Current entity ID is an address only. Retain registry entry ID, readiness
reason, generation and current live state provenance; do not persist a PIN in
new diagnostic records. Registry `disabled_by`, ownership and loaded KM entry
must be checked even when an ID resolves. A state may be valid for one
property type but not another: `unknown` is empty text only under FR-012.

Freshness transitions: `unresolved → registered/not-ready → live` after an
attributable non-restored publication. Rename, remove, disable, entry
reload/unload or reassociation invalidates the affected generation, drops
old subscriptions and returns to unresolved/not-ready. Re-registration and
fresh publication can restore live. Callbacks and confirmation must reject
stale generations, even if an old address is reassigned to another entity.

## Operation and pending clear

An operation has RC entry, originating KM entry ID, exact slot, required
capabilities, dispatch generation, progress and outstanding confirmation.
Before each side effect and confirmation, re-resolve and check identity and
current generation; preserve partial effects on invalidation. A clear has an
independently durable original `(KM entry ID, slot)` fence. Persist fence
before dispatch with an acknowledged durable write, not a Store save return;
recovery only observes that original identity and slot.
Clear is locally confirmed by both fresh, eligible empty text observations;
missing records, restored states, masks, reset notifications and other
slots/locks cannot satisfy it. Physical erasure is not established.

## Shared ledger, ownership and holds

Existing allocator state includes allocation owners, identities, lockname,
slot, observed-code aliases, adoption conflicts and forced-reissue holds.
The design augments attributable records with immutable original KM identity
and slot, retaining legacy fields/selectors as needed. Do not equate records
solely by a normalized alias; do not split accounting for RC entries sharing
one verified lock. Preserve unknown-origin legacy work and holds until
original identity is established. The existing v1 loader treats an
unsupported **payload** schema as registry lost; an unsupported higher HA
Store **envelope** version fails the load before payload parsing. Do not
rewrite alias equality before all affected RC entries are accounted for.

Slot-fence confirmation is not PIN release. Existing release guards still
account for owners, forced holds, conflicts, unreadable/missing observations
and a still-observed PIN. A redundant stale owner may be retired under the
existing same-slot rule while another owner still protects the code.
Exclusions remain separate from raw observations and cannot clear a hold.

## Authoritative registry envelope and transactions

The existing per-entry mapping store is cache-only and best-effort;
`RegistryStore.async_save` debounces for one second. Neither can prove
pre-dispatch durability. Implement this single-authority protocol:

1. Use the existing `.storage/rental_control.code_registry` key with a
   HA-compatible envelope `{version: 2, minor_version: 1, key, data}`.
   The v2 data contains the entire validated v1 ledger representation
   (including encoded codes, owners, salt and conflict accounting) plus
   versioned safety metadata: transaction ID/generation, per-RC association
   and attestation provenance, original lock/slot pending fences and holds.
   The data schema must require explicit, typed ledger, associations,
   attestations, pending fences, holds, tombstones and pending-config
   transactions sections, including explicitly empty collections where
   appropriate; missing, null or malformed sections are invalid, not empty.
   Version the data schema explicitly and reject unknown envelope/data
   versions rather than dropping fields. The existing encoding is reversible,
   not encryption; use a private file and never log or add plaintext PINs.
2. Replace the old `RegistryStore` writer for this path; do not run a Store
   delayed writer or a separate v1 mirror against it. Under the allocator's
   integration-wide transaction lock, serialize an immutable complete
   envelope and await `homeassistant.util.file.write_utf8_file_atomic` with
   `private=True` via `hass.async_add_executor_job`. Call HA
   `homeassistant.helpers.json.prepare_save_json(envelope)` **once** to get
   `(mode, json_data)`; pass both `json_data` and `mode` to the helper.
   HA's executor accepts positional arguments, so call it with
   `(write_utf8_file_atomic, path, json_data, True, mode)` or an equivalent
   bound callable.
   Hash the exact bytes that will be written (`json_data` itself for `wb`;
   UTF-8 encoding of `json_data` for `w`), not a separately serialized
   object. Read and hash exact disk bytes during recovery. Propagate
   serialization/`WriteError`/I/O failures to
   the operation as sanitized failure. Do not mix independent Store cache or
   automatic Store migration writes with direct writes to the same file.
   The HA helper uses fsync: this is an engineering choice, not a user-selected
   fsync policy or a guarantee against arbitrary hardware failure.
3. Before clear dispatch, commit original KM entry ID, exact slot, RC owner
   and relevant reservation/hold state in the same envelope. Recheck
   association and capabilities after the acknowledged write and before
   dispatch. Serialize all protective ledger, association and hold mutations
   through the same authority so a later stale snapshot cannot erase a
   fence. During shutdown, read-only conditions, failed serialization or
   write, do not dispatch. An ambiguous post-replace failure is not success:
   retain protection, stop affected work and reload/validate actual disk
   state before any retry.
4. On confirmation, evaluate fresh original-slot evidence and the existing
   separate PIN-release guards. Commit the updated ledger and resolved fence
   together before exposing a freed slot/code. Retain the prior protected
   identity/slot/owner references as recovery tombstones in the envelope:
   if a write reports failure after replacement, the old bytes cannot be
   assumed to remain. Freeze the in-memory release; on restart use those
   tombstones to reconstruct protective holds and re-evaluate original-slot
   evidence and all guards before any new work, without deleting later owners
   or undoing an already valid allocation. Only a definitely acknowledged
   transition can be exposed during the running process. An operator
   attestation binds previously unknown legacy provenance in the same
   transaction without clearing any fence.
5. At startup, classify actual on-disk bytes and activation marker before
   enabling operations. A valid v1 envelope is migrated under the same lock:
   validate the complete ledger, preserve unknown legacy provenance, durably
   record migration intent with the exact valid v1 disk digest in the marker,
   then atomically install v2
   **before** dispatch or new safety metadata. Finalize the marker only after
   the v2 write is acknowledged. Do not rely on Store's automatic migration
   write as an acknowledgement. Recover only the specific marker/envelope
   combinations described below. An interrupted migration can safely retain
   v1 only when the marker proves the old bytes are its recorded predecessor
   and the pre-dispatch ordering below holds. A restored v1 cannot always
   be distinguished from an interrupted v2. Protect against old delayed writers
   during unload/reload. Manual deletion or restoring an older backup is
   outside the downgrade guard; do not claim perfect detection.

An association change is an envelope transaction with the old identity and
all outstanding claims preserved before publishing the new association.
HA config entry updates are delayed and not atomic with the envelope:
on restart, compare both, replay the envelope's committed association only
when its transaction proves it, and treat any unmatched legacy/entry update
as unresolved rather than moving work. An operator's selection of B
authorizes B only for eligible new operations and does not make A's old
hold B's; old work remains bound to A or unknown.

This design centralizes protective ledger changes; an independently delayed
ledger could lose the owner information a fence protects. Test performance,
HA lifecycle, crash recovery and failure semantics; if implementation cannot
preserve existing policy, stop for maintainer direction.

### Load classification and protective-unknown recovery

**Protective-unknown** means the shared allocator cannot establish that its
authoritative ledger and matching protection marker are complete and
sufficiently current. It does not establish that prior claims or physical
codes are present or absent.

The direct disk reader returns a typed result, not `None` for every failure:

| Result | Handling |
|---|---|
| Valid v1, no activation evidence | Apply existing v1 validation and loss rules; migrate with marker before activating v2 |
| Truly absent, no activation/partial/corrupt evidence | Treat as the existing first-run/legacy-absent case, subject to existing adoption gates; absence is not evidence that arbitrary past history never existed |
| Finalized genesis marker and its exact recorded v1/absent predecessor | Resume the idempotent initial activation; no v2 target-dependent action has been authorized |
| Valid v2 and matching finalized marker | Restore durable ledger, fences and transaction bookkeeping; validate identities/owners/generation before observation |
| Pending marker and exact recorded target envelope | Validate target generation/schema and complete all target writes, finalize marker idempotently, then recover any existing fences before work |
| Pending marker and exact recorded prior envelope | Roll back only that pending transaction and retry from prior state **only** if all sequencing/no-dispatch conditions below are proven; retain all prior active operations and holds |
| Valid v1 or v2 with a marker not meeting either pending case, or v2 without a valid matching marker | Protective-unknown; never assume migration did not already complete |
| Absent with marker other than a valid recorded genesis-absent predecessor, unexplained partial transaction or a matching `.corrupt.*`/HA corruption issue | Protective-unknown; never treat as fresh install |
| Invalid JSON, unreadable file, invalid/unsupported marker or v2 envelope, unsupported version, missing/invalid required v2 protection section, or marker mismatch | Protective-unknown; preserve evidence and do not rebuild, overwrite, issue, release, reassign, or reset |

This distinction is mandatory: existing allocator registry-loss adoption can
reconstruct observed codes but cannot reconstruct old pending fences,
attestations, unreadable-slot holds or release tombstones. An exclusion or
new live empty slot cannot clear protective-unknown. Since the allocator
shares a code space, withhold issuance and reuse across affected shared
accounting. In the new version, lockless RC entries must still set up and
display their calendars without requiring Keymaster, while new lockless code
issuance remains withheld until allocator safety state is restored. Preserve
claims and fences already loaded and avoid hardware or ownership mutation
where information is unavailable; do not claim that codes missing from
damaged state remain visible or present, or that they are absent or reusable.
Expose persistent actionable status that directs the operator to restore
verified safety state, without PINs, raw storage contents or raw exceptions.
Keep calendar-only behavior where it does not mutate access.
HA `Store.async_load` may rename invalid JSON to `.corrupt.*` and return
`None` ([source](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/helpers/storage.py#L425-L475));
do not use that `None` as an absence proof. The new direct reader must not
silently move/delete corrupt bytes. Also detect evidence of a prior HA
quarantine when the original path is already absent, without dumping it.

**Repair**: surface a sanitized persistent actionable error, preserve the
damaged file/marker, and have an operator restore a *verified sufficiently
current* complete envelope and matching marker through a stopped,
documented backup/restore procedure, then validate current original
identities and live observations before resuming. An older backup is not
sufficient merely because it parses; it may omit later work. HA may also
present its generic storage-corruption repair issue after quarantining invalid
JSON; submitting that confirmation is not proof that the allocator envelope
and marker were restored. Any default/fresh v1 registry v4.0.3 creates or
writes after HA returns `None` for the quarantined file is not restored safety
state; with the surviving activation marker, the new reader classifies it as
protective-unknown. If no complete state or independent proof exists, keep
unknown holds and the gate; an individual operator attestation may establish
only a specific legacy-unknown physical-lock provenance, not a known-A
transfer. It cannot prove the entire lost ledger or authorize code reuse. Do
not invent a force-release or claim that all such cases are recoverable.

### Activation marker and write serialization

Keep a small private, PIN-free activation marker at the separate HA storage
key `.storage/rental_control.code_registry_protection`. Each finalized
marker records activation ID, committed generation, exact envelope digest
and committed transaction ID. Each pending marker embeds that entire prior
finalized record plus a distinct target transaction ID, target generation,
target digest and transaction kind; reject missing/invalid fields, repeated
IDs or non-successor generations. For v1 migration the validated, quiescent
v1 disk bytes serve as a **genesis predecessor** with exact digest and
explicit v1 tag; record that predecessor in the first pending marker before
installing v2. A fresh absent registry uses an explicit genesis-absent
sentinel only when there is no activation, quarantine or partial-history
evidence. Neither sentinel asserts that arbitrary deleted history cannot
exist. This marker is a correlation signal, not a ledger.

Under one serialized writer, prepare target bytes, acknowledge pending
marker, acknowledge target envelope, then acknowledge finalized marker.
**No side effect or reuse dependent on the target transaction may dispatch
until its final marker write succeeds.** Gate every operation by its own
unique committed transaction ID and generation, revalidate identity and
capabilities before dispatch, and never permit a caller to use a pending
transaction's result. Existing previously committed/in-flight operations
may already have produced partial hardware effects: their durable fences and
owners are part of the prior envelope and are not canceled or confirmed by
rolling back a newer pending intent.

Recovery after an ordinary **process crash**, with no evidence of external
restore/deletion or storage failure:

1. If marker is pending and actual validated disk bytes match the recorded
   prior digest/version/generation (or the explicitly recorded genesis
   absence), restore the prior finalized marker and discard only the target
   intent, then retry it if still eligible. This is safe only when the
   embedded    prior finalized record or validated v1 genesis is trustworthy,
   the writer's quiescence/order is proven, and target-dependent dispatch
   was impossible before final-marker acknowledgement. Do not lose prior
   pending clear A or other prior effects. If any condition is unprovable,
   enter protective-unknown instead.
2. If disk bytes match the target digest/version/generation, validate the
   complete target, finalize the marker idempotently and restore the
   committed bookkeeping and fences. Restoration is **not** dispatch of a
   hardware service: never infer a clear call from the envelope or marker.
   Observe the original identity and slot using fresh verified evidence.
   Finalization does not establish whether an earlier operation reached
   hardware; only the existing retry policy may re-dispatch after identity,
   capabilities and all applicable safety gates are revalidated. An already
   empty slot can recover from fresh evidence without replaying the reset.
3. Missing unexpected bytes, corrupt marker/envelope, invalid versions,
   non-successor generations, inconsistent IDs, or any other digest match
   failure is protective-unknown. A finalized marker must match its
   committed envelope. Never infer dispatch outcome from empty live states.

Before creating the v1 genesis record, stop/quiesce old writers and confirm
that the validated on-disk v1 snapshot covers the live allocator state; do
not race an earlier `async_delay_save`. If that cannot be established, do
not migrate. For a genesis predecessor, the restored prior marker is a
finalized marker explicitly tagged `v1` with its exact digest, or `absent`
with its sentinel. It authorizes retrying activation, not any v2 dispatch.
File helper atomic replacement/fsync and serialized writes are
the engineering assumptions for these process-crash cases, not proof against
faulty storage, external edits or restoring older files. If such rollback is
known or suspected, keep protective-unknown even when an old digest matches.
Hash is correlation, not authentication or PIN secrecy. Loss or rollback of
both files cannot always be detected.

Use the existing allocator `_lock` as the sole state/commit ordering lock
(or a documented replacement with the same coverage). Classify every
mutation: protective owner/allocation, association, attestation, fence,
release and exclusion-sensitive change must receive an acknowledged full
snapshot commit before its dependent side effect or reuse. Ordinary
non-protective metadata saves may be coalesced: a worker acquires the same
lock and snapshots the **latest** state at write time. It must not queue a
stale whole-ledger snapshot. A protective commit includes pending ordinary
changes and cannot be overtaken by an older worker. If any marker/envelope
write has an uncertain outcome, enter protective-unknown until revalidated;
do not silently retry over evidence.

Awaiting executor I/O while holding the async allocator lock is intentional
for ordering and does not block the HA event loop; do not call HA services,
dispatch callbacks, update config entries, or acquire an EventOverrides or
reissue lock inside that lock. Release an override/reissue lock before
requesting the allocator transaction; carry a generation token and recheck
after reacquiring local state. No reverse lock acquisition is allowed.
Cancellation cannot abandon an in-flight executor write and release the lock:
retain the lock until the worker completes or enter an explicit unknown
state after completion, then propagate cancellation. Coalesce only updates
that cannot weaken a fence. Benchmark and test slow I/O, cancellation,
coalescing and lock inversions; no calendar/event loop blocking or unbounded
flush queue is acceptable.

### Envelope/config correlation and shared attestations

An association transaction records a unique transaction ID and, for each
affected RC entry ID, explicit **old and new association tuples**:
`(legacy_lock_selection_raw, verified_keymaster_entry_id,
association_generation, association_transaction_id)`. Preserve the exact
legacy selection including an absent/`None` distinction; existing legacy
entries use the sentinel tuple `(raw_selection, None, 0, None)`. Each new
tuple has a monotonic per-entry generation, this transaction's ID and the
intended verified KM entry ID; an intentional lockless selection is an
explicit distinct mode, not the unresolved sentinel. The envelope also
stores attestation scope/evidence, predecessor transaction ID, affected
owner/hold/slot references and original KM identity for existing work.
Record the ordered chain of accepted tuples until config persistence is
proved, not an unspecified HA revision or a mutable lockname alone.

Commit that transaction before `async_update_entry`; persist the explicit
`verified_keymaster_entry_id`, `association_generation` and
`association_transaction_id` in RC config data while preserving the legacy
selector and unrelated options/exclusion behavior. HA config persistence
is a separate delayed write, not a second acknowledged transaction.

| On restart | Required behavior |
|---|---|
| Valid committed envelope, config matches a recorded prior tuple in its uncheckpointed chain | Replay the latest recorded tuple idempotently; retain the chain and fence affected actions until the live config matches |
| Config matches committed transaction ID, generation and intended entry | Revalidate; retain prior tuple chain even after clearing the in-memory pending flag, because HA's disk save may still lag |
| Config ahead, unknown transaction ID, unexpected entry, or third-party/option change touching the association | Protective-unknown for that association; retain old holds and require repair, never guess which wins |
| Marker pending and envelope target matches | Finalize marker first, then apply the two matching cases above |
| Marker/envelope mismatch, unreadable config or missing expected affected RC entry | Retain unresolved work and no target side effects; safe repair, not automatic transfer |

Never replace unrelated concurrent options from an old snapshot; compare
the explicit association tuple only, merge permitted unrelated changes, and
use existing options-save exclusion semantics. Recheck range and
capabilities after any concurrent options change. The **public-API-only
default** retains the tuple chain: current HA config-entry APIs expose the
live value but no verified durable-save acknowledgement. Do not equate
`async_update_entry` or a Store cache read with disk persistence. Report
chain-growth pressure and, if necessary, defer further association changes
rather than silently truncate it.

An optional read-only checkpoint adapter may prune a prior tuple only after
an exact on-disk check *and* independently proven writer quiescence. This
depends on HA's **private**, versioned `.storage/core.config_entries`
format, not a stable integration API. For a known tested HA version, in an
executor validate the full Store envelope/key/version and the expected
`data.entries` list, unique RC `domain`/`entry_id`, entry `data` shape and
the complete latest association tuple; never dump raw entries. An unknown
version/shape, missing or duplicate entry, read/parse/permission error or
inability to prove no earlier HA writer can overwrite the result means
**retain the chain and surface pressure**, not prune. A matching disk read
alone is insufficient to prove quiescence. Never write HA's private storage
directly. HA 2026.11.2 currently uses envelope version 1.5 and serializes
`data.entries` from config-entry storage fragments
([source](https://github.com/home-assistant/core/blob/2026.11.2/homeassistant/config_entries.py#L3109-L3120));
validate compatibility rather than assuming this layout for future versions.
If a future public API supplies an actual durable acknowledgement and
writer barrier, prefer it over the private adapter. Replaying a known prior tuple
cannot detect an external restore of *both* files to the same old history;
do not claim otherwise. A config update without a
committed envelope cannot authorize hardware actions. Test failure/restart
before and after marker intent, envelope commit, config update, delayed
config flush and transaction completion.

For legacy-unknown shared locks, group only entries whose original physical
lock is established by independent evidence or explicit cross-entry operator
attestation; the attestation may bind a recreated Keymaster entry, but a
matching normalized slug merely creates an *unresolved candidate*, not a
group. Physical continuity is not inferred from current entry IDs, names or
addresses. Attestations are scoped to named RC entries and specific legacy
owner/hold references. Under the same transaction lock, reject contradictory
same-group attestations A versus B; do not modify an already verified A or
implicitly propagate one entry's statement to another. Two different locks
with colliding slugs may be separately verified as A and B; keep their ledgers
distinct. Where ownership cannot be partitioned
without guessing, preserve the affected claims and holds until the operator
resolves the ambiguity. Migrate proven shared owners and aliases atomically
as a group, or not at all.

### Tombstone retention and bounded growth

Retain release tombstones while a release/config transaction is unfinalized,
any owner/forced hold/conflict references it, any older snapshot or callback
can still be applied, or recovery has not revalidated its original slot.
After a finalized checkpoint includes the current complete ledger, no pending
transaction references the tombstone, and existing release guards permit it,
prune it via the same serialized marker/envelope transaction. A failed or
ambiguous prune is recovered by the marker protocol, not assumed successful.
Do not prune by time or a hard limit alone. Coalesce eligible finalized
tombstones and run bounded batches; retain unresolved ones indefinitely,
publish count/age diagnostics without PINs and warn if growth threatens
resources. A hard cap may refuse new work rather than silently drop protection.

## Schema and rollback

The maintainer selected an HA envelope major-version-2 header as the
v4.0.3 downgrade guard. HA's v1 Store rejects a JSON-decoded object with an
integer top-level `version: 2` before inspecting its inner payload; the
`RegistryStore` wraps the version error as `ConfigEntryNotReady`. The guard is
based on the top-level version, not successful validation of the v2 payload:
even a v2 header with missing or malformed inner data can trigger it. Test
with the actual v4.0.3 loader and HA runtime, not merely a mocked schema
parser. Because the shared allocator loads before RC entry setup, downgrading
can block **all entries including lockless ones**. For an intact v2 registry,
reinstall a compatible newer RC version and retain that registry. Never
delete or overwrite it with an older backup to
force startup: that can lose later guest access and pending holds. The guard
does not promise to cover other old versions, file deletion/replacement, or
restoring an older snapshot. In particular malformed v2 JSON can be moved to
`.corrupt.*` by HA's v1 Store, which returns `None`; v4.0.3 then treats the
registry as absent and may enter its existing adoption recovery, ignoring the
new marker. It may later resume without reconstructing the new fences; this is
not a safe guard or recovery. HA's repair issue gives the operator the
quarantined path and recommends fixing manually edited JSON and restoring it,
or restoring the system from backup, then confirming the repair. Its generic
confirmation flow does not restore or validate the file. Test the actual
corrupt-downgrade path. If
encountered, stop RC/HA and preserve the quarantined bytes and marker.
Reinstalling a compatible newer version alone cannot reconstruct missing
safety state: restore a verified, sufficiently current complete envelope and
matching marker through the stopped repair process. Without that restore,
remain protective-unknown and do not claim recovery is safe.
No v1 mirror may overwrite v2 after activation.

## Event source and topology

Each KM source entry has an underlying physical lock entity and, where
applicable, a verified parent entry and per-slot inheritance/override state.
The event contains the source lock entity ID, state, slot and possibly
corroborating name. Accept only valid unique attribution, unlock, managed
range and existing stay checks; inherited child events additionally need
current verified topology and same-slot inheritance. No event grants access
to child controls or authorizes a write to a parent.

## Compatibility diagnosis

The loaded manifest version independently maps to `development`, `supported`,
`outside_support`, or `unknown`. A capability has its own identity/readiness
status and actionable remedy; none is inferred from version or vice versa.
Safe diagnostic context may include entry, slot, capability, status and
remediation but not PINs, raw state/payload dumps or exception messages.
