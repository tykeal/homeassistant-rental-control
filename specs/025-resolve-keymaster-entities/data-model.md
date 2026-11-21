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
   envelope and perform serialization, encoding, hashing and file I/O in the
   executor. Keep the transaction lock until each submitted worker finishes,
   including caller cancellation; never abandon an in-flight write or claim
   acknowledgement before its result. Call HA
   `homeassistant.helpers.json.prepare_save_json(envelope)` **once** for that
   snapshot. Validate `(mode, json_data)`, keep bytes for `wb`, UTF-8 encode
   text for `w`, and hash the resulting exact byte string; use those same bytes
   for disk write and recovery comparison. Never reserialize to calculate a
   digest.

   Do **not** use HA `Store.async_load`, `load_json`, `write_utf8_file_atomic`,
   `save_json`, or Store save/quarantine behavior for registry or marker I/O.
   In particular, `Store.async_load` can move corrupt JSON aside, log paths
   and exception text, publish those values in a repair issue, and return
   `None`; that is neither private nor a reliable absence result. Use an
   RC-owned raw-byte reader in the executor for both fixed paths. Reject
   symlinked/non-regular paths, distinguish only verified `ENOENT` from
   unreadable/invalid paths, read at most 32 MiB for the registry or 64 KiB
   for the marker plus one byte, then decode and validate the captured bytes.
   Accept files exactly at the limit; classify the limit plus one byte as
   oversize/protective-unknown.
   Where supported, open with `O_NOFOLLOW` and verify the opened descriptor
   with `fstat` before reading. On Windows, reject reparse points in every
   traversed component, including the final file, using verified handle-based
   no-follow traversal; an ACL check or final-component check alone is not
   sufficient. Test actual behavior on supported Windows. If the required
   safe traversal cannot be implemented and verified, return sanitized
   unavailable and do not open, read, write or clean these paths. The
   trusted-config-directory boundary still excludes concurrent hostile
   same-user replacement; never claim path checks defend against it.
   A present wrapper must decode to a nonempty object with a valid integer
   major version (not a boolean) and `data` of the validated expected shape;
   malformed or empty wrappers and missing required or unsupported fields
   are protective-unknown, not `ENOENT`.
   For valid v1 wrappers, missing `minor_version` defaults to 1; valid
   integer same-major minor mismatches retain HA's v1 payload compatibility
   without its write-on-read migration (reject boolean/non-integer minors).
   Validate the existing v1 payload;
   HA does not require the wrapper `key` on load, so do not require it for
   legacy acceptance. For v2 validate supported minor/version and required
   protection sections explicitly; never rely on HA's permissive same-major
   fallback to accept an unknown safety schema. Raw classification itself
   performs no write; protective migration uses the marker protocol.
   Invalid UTF-8/JSON, oversize data, permission/I/O errors, and malformed or
   unsupported content return distinct typed sanitized outcomes; never move,
   delete, or replace damaged evidence. Only registry **and** marker absence
   with no activation, partial-write, or quarantine evidence is a true
   first-run/legacy absence. Keep the v4.0.3 downgrade case separate: its old
   HA Store still rejects a decoded integer top-level `version: 2` before
   inspecting the payload, but quarantines malformed JSON and returns absent;
   the new reader must never invoke that old path.

   Use a small RC-owned adapter around `atomicwrites.AtomicWriter` from the
   existing HA runtime distribution, directly in binary overwrite mode; add
   no package dependency. The project minimum is HA 2026.11.0, whose current
   locked test environment provides `atomicwrites-homeassistant` 1.4.1.
   This is not a Home Assistant public API: test the import and required
   behavior on the minimum supported HA runtime and supported CI HA lines,
   without pinning a brittle exact Python signature. Fail closed if the
   dependency or required behavior is unavailable; never fall back to HA's
   logging wrapper. Verify private temporary-file permissions (POSIX mode
   `0600`; on Windows verify the private HA configuration directory ACL),
   file fsync, same-directory atomic replacement and the platform's
   directory-fsync/write-through acknowledgement by behavior.
   Before creating a temp file or replacing either authoritative path,
   enforce the same 32 MiB/64 KiB caps on the exact serialized registry/marker
   bytes. Oversize output is a sanitized failed transaction, not an
   acknowledgement; retain prior state and fences and perform no dependent
   dispatch. Preflight both target envelope and all pending/final marker
   bytes before starting a transaction. A re-ack likewise checks its exact
   bytes before replacement.

   Use the fixed HA `.storage` key, reject a symlinked/non-directory `.storage`
   parent and a symlinked/non-regular existing target, and keep the path under
   the trusted HA configuration directory. The replacement operation replaces
   rather than follows a target symlink; path-based parent validation is not a
   defense against a same-user process that can concurrently replace the
   trusted directory. Never claim stronger hostile-local-user protection.
   Coordinate direct registry/marker readers, writers and cleanup with a
   process-shared exclusive OS lock at
   `.storage/rental_control.code_registry.io_lock`, a fixed private,
   non-authoritative lock-file path, in addition to the allocator's async lock.
   Acquire it in the executor using nonblocking supported standard-library
   primitives (POSIX `flock` with `LOCK_NB`, Windows `LockFileEx` through
   standard-library `ctypes` with `LOCKFILE_EXCLUSIVE_LOCK` and
   `LOCKFILE_FAIL_IMMEDIATELY` over the same fixed one-byte range); create
   with private permissions/ACL and reject a symlink/non-regular lock path.
   On Windows the range may extend past end of file; the lock file needs no
   content or initialization step. Create/open one stable private regular
   file without truncation, and lock the same range on its validated handle.
   An empty file after a crash is usable; the OS releases abandoned locks
   after handle closure, potentially with delay. Never rename, unlink or replace the lock file while any
   participant can run. If it is unsafe or unsupported, stop all HA/RC
   processes before an operator repairs the path or environment, then
   restart and verify the same lock protocol; do not mutate the ledger.
   Every in-process participant, including reads and cleanup, first acquires
   the same allocator async lock, then the process lock, then releases in
   reverse order; never acquire the allocator lock under the process lock.
   This is required even if OS lock semantics allow same-process reentry.
   Start one monotonic five-second deadline before waiting for the allocator
   async lock. Await that lock asynchronously with the remaining deadline;
   never block the HA event loop. After acquiring it, retry only OS-lock
   contention with bounded backoff and cancellation checks using the same
   remaining deadline. Queue expiry returns busy without starting that
   request's registry/marker read, write, cleanup or dependent hardware
   action; the separate lock-file open/lock attempts and other already-active
   work are not claimed to be absent.
   Classify only verified platform-specific contention errors as busy: test
   the real errno/Windows error produced by a second process on each
   supported runtime, including expected `EAGAIN`/`EWOULDBLOCK` on POSIX and
   `ERROR_LOCK_VIOLATION` on Windows. Load `kernel32` via
   `ctypes.WinDLL(..., use_last_error=True)`, declare the correct Win32
   prototypes and capture `ctypes.get_last_error()` immediately when
   `LockFileEx` fails. Classify raw Windows error 33 as contention before
   any Python errno conversion; raw `ERROR_ACCESS_DENIED` (5) is unavailable,
   not busy. Test both outcomes on native Windows with an actual competing
   process and a genuine permission failure. Do not guess other mappings;
   unknown/unsupported/permission errors are sanitized unavailable, not
   endlessly retried as contention. Handle interrupted calls within the
   same deadline.
   A deadline while waiting returns a static retryable busy result without
   ledger mutation; cancellation stops and drains any waiter before
   propagating. Unsupported primitives or unsafe lock
   paths fail closed with sanitized unavailable status, without declaring
   persistent protective-unknown unless independent ledger evidence requires
   it. Unsafe or unowned existing lock files are never repaired by `chmod`
   or ACL mutation; require offline operator repair. Never start an I/O
   transaction without the lock. A cancelled waiter
   stops retrying and is drained; no blocking lock call can hang the drain on
   contention. Once actual I/O begins, drain until its result is known; this
   is not a promise to bound arbitrary stalled filesystem I/O.
   Every participating writer must hold it for
   disk read/compare/write, and compare the planned predecessor digest and
   generation with actual bytes under that lock before replacement; a stale
   process must fail closed rather than overwrite newer state. If lock
   acquisition or its safety checks fail, do not read/write/cleanup.
   This protocol coordinates participating RC disk
   readers/writers and cleanup only; it is not cross-process hardware or
   allocator coordination. One HA process owns a config directory;
   non-participating legacy or external writers remain outside the guarantee.
   Convert `Exception` failures from serialization, encoding, hashing, raw
   read/decode, writing, replace/fsync, executor submission/result handling
   and cleanup into typed results with fixed allowlisted stage codes,
   including non-`OSError` failures and sensitive exception causes/contexts.
   Do not retain, log, chain or expose raw exception objects, filenames or
   tracebacks; never raise a sanitized replacement from inside the handler
   or pass raw errors to HA. Cancellation uses the shield/drain protocol
   below, including repeated cancellation; sanitize again at the caller
   boundary.
   Hash the exact bytes produced once by serialization and compare that
   digest only with exact raw disk bytes during recovery; never reserialize a
   decoded object to establish agreement.

   Give each writer's temporary file an exact RC-owned name prefix containing
   a fixed integration stem, file kind and opaque random operation ID. Keep
   the random suffix, private permissions and same-directory atomicity. After
   startup has validated/recovered state, while holding the shared writer
   lock and after all workers are quiescent, cleanup may scan a directory
   containing at most 4,096 entries in total, including unrelated entries.
   Inspect at most the 4,097th entry to detect overflow; exactly 4,096 is
   complete only after end-of-directory is observed. Gather candidates before
   unlinking; on overflow or incomplete enumeration unlink none. Evidence
   scans that cannot finish enter protective-unknown with persistent
   sanitized status explaining that a directory scan exceeded its limit or
   could not complete, directing the operator to investigate the storage
   directory with HA stopped and preserve registry/marker/quarantine evidence;
   cleanup reports sanitized maintenance status.
   Do not assume a typical directory is below the bound. Unlink only regular,
   non-symlink files with the exact RC prefix/form and verified private
   ownership/ACL (same owner and mode on POSIX; validated HA-directory ACL
   on Windows). Never match registry, marker or HA `.corrupt.*` files. If the
   scan is over bound or any ownership, path or lock check fails, leave
   candidates untouched and return sanitized maintenance status. Hold the
   same process-shared OS lock used by every participating RC writer; do not
   attempt process discovery or rely on timing. Tests must use a second
   process holding the lock to prove cleanup cannot run concurrently. Do not
   clean while pending or protective-unknown; leftover temporary files are
   never authoritative. Native Windows behavior and safe traversal are not
   established by this design or Linux-only probes; do not claim verified
   Windows protected-operation support until the required native tests pass.
   Until then gate protected operations, not calendar-only availability.

   A failed writer result after a possible rename, including a directory
   fsync failure, is ambiguous even if the new bytes are visible: do not
   acknowledge the transaction, dispatch dependent work, or infer success.
   Enter protective-unknown. At restart, validate bounded raw bytes and
   marker/digest relationships, then re-acknowledge the exact validated
   bytes with a successful atomic write before finalizing or resuming; if
   validation or re-acknowledgement fails, remain protective-unknown. Do
   not mix independent Store cache or automatic Store migration writes with
   direct writes to the same file.
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
| Valid v1, no marker, activation, partial-write, or quarantine evidence (complete bounded evidence scan) | Apply existing v1 validation and loss rules; migrate with marker before activating v2 |
| Both registry and marker paths return verified `ENOENT`, no symlinks, partial-write, activation, or boundedly discovered quarantine evidence (the scan completed within its cap) | Support fresh installs and the existing legacy-absent case, subject to existing adoption gates; absence is not evidence that arbitrary past history never existed |
| Valid v2 and matching finalized marker, without evidence of external restore/deletion, corruption or other suspected rollback | Re-acknowledge exact validated envelope and marker bytes before availability; restore ledger, fences and transaction bookkeeping; validate identities/owners/generation before observation |
| Pending marker and exact recorded target envelope | Validate target generation/schema, re-acknowledge exact pending-marker and target bytes, finalize marker idempotently, then recover any existing fences before work |
| Pending marker and exact recorded prior envelope | Re-acknowledge exact prior bytes; roll back only that pending transaction and retry from prior state **only** if all sequencing/no-dispatch conditions below are proven; retain all prior active operations and holds |
| Pending genesis marker with explicit absent predecessor, registry path verified `ENOENT`, and no activation/partial/quarantine evidence | Remove only the exact pending marker after confirming its genesis transaction and no possible target dispatch; fsync the `.storage` directory, leave the registry path absent (do not create or delete an envelope), then return to first-run activation. If marker removal/fsync is uncertain, remain protective-unknown |
| Valid v1 with a valid finalized v1-genesis marker matching its exact bytes after rollback, with trustworthy predecessor and no evidence of external restore/deletion, corruption or other suspected rollback | Re-acknowledge exact v1 and marker bytes; retry activation only after the existing quiescence and live-state agreement checks, without authorizing v2 dispatch |
| Valid v1 with a present marker not matching a valid finalized v1-genesis or pending case, or v2 without a valid matching marker | Protective-unknown; never assume migration did not already complete |
| Valid v1 with a matching `.corrupt.*` sibling or other prior-corruption evidence but no valid marker authenticating those exact v1 bytes, or with an incomplete bounded evidence scan and no such marker | Protective-unknown; preserve evidence, do not migrate/adopt from it or allow attestation to override missing history; this release has no supported external recovery or genuine verifier |
| Any otherwise matching marker with known or suspected external restore, deletion, corruption or rollback | Protective-unknown; matching digest alone does not establish current complete history, and this release has no supported external restore or override |
| Absent with marker other than a valid recorded genesis-absent predecessor, unexplained partial transaction or boundedly discovered matching `.corrupt.*` evidence | Protective-unknown; never treat as fresh install |
| Unreadable file, invalid UTF-8/JSON, size-limit overflow, invalid/unsupported marker or v2 envelope, unsupported version, missing/invalid required v2 protection section, or marker mismatch | Protective-unknown; preserve evidence and do not rebuild, overwrite, issue, release, reassign, or reset |

This distinction is mandatory: existing allocator registry-loss adoption can
reconstruct observed codes but cannot reconstruct old pending fences,
attestations, unreadable-slot holds or release tombstones. An exclusion or
new live empty slot cannot clear protective-unknown. Since the allocator
shares a code space, withhold issuance and reuse across affected shared
accounting. In the new version, lockless RC entries must still set up and
display their calendars without requiring Keymaster, while new lockless code
issuance remains withheld until allocator safety state is restored. Preserve
lockless first setup/refresh, calendar display and calendar-only check-in
while the allocator is busy **or** locking is unsupported, unsafe or
unavailable. The bounded typed fallback must not raise through setup/refresh
or require registry/marker I/O; withhold shared issuance, reuse, release and
new dependent hardware/ownership dispatch until locking and safety gates are
available. Preserve already-loaded claims and fences. Unavailable locking
alone does not assert persistent ledger corruption or grant access.
Calendar-source/network and already-running
filesystem operations are outside the five-second lock deadline. This does
not assert that every other nonmutating read avoids the allocator lock.
Preserve claims and fences already loaded and avoid hardware or ownership mutation
where information is unavailable; do not claim that codes missing from
damaged state remain visible or present, or that they are absent or reusable.
Expose persistent sanitized status directing the operator to preserve the
current registry, marker and quarantine evidence, and explaining that
external recovery is unavailable with no supported restore or override in
this release when history cannot be verified. Do not expose PINs, raw
storage contents or raw exceptions.
Keep calendar-only behavior where it does not mutate access. Use the
RC-owned bounded raw-byte reader for both registry and marker; never invoke
HA `Store.async_load`, `load_json`, or another path that can quarantine,
log storage paths/raw errors, or conflate corruption with `None`. HA
2026.11.0 `Store.async_load` does quarantine invalid JSON, logs the storage
key, original and quarantine paths plus error, and returns `None`
([source](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/helpers/storage.py#L425-L475)).
The direct reader must preserve damaged bytes. When the original path is
absent, detect a prior HA quarantine by a bounded scan for the exact
integration storage-key `.corrupt.*` filename pattern; do not open, move,
delete, or log those files or their paths. If the bounded scan is
inaccessible or exceeds its cap, quarantine absence is unproven and the
result is protective-unknown, never first-run.

**Repair and external backups**: surface a persistent sanitized status,
stop RC/HA before inspecting a candidate, and preserve the original registry,
marker and quarantine evidence. For v1 plus a quarantined sibling the approved
policy permits only a **verified backup**, not operator-approved risky
recovery; this release has no supported verifier or external recovery path.
A candidate would need a complete envelope and matching marker. Schema, supported
versions, transaction/generation and the marker's digest of the exact
envelope bytes are necessary consistency checks only; even a successful
re-acknowledgement cannot establish currency or prove that later work did not
occur. Do not overwrite evidence or resume merely because these checks pass.

External recovery also requires independently verifiable authoritative
evidence that the candidate covers every committed protective transaction
and every possible dispatched hardware effect through a proven quiescent
incident boundary, including outstanding claims and fences. Evidence must
cover the entire interval, all participating writers and operations, and be
independently protected from the same rollback; otherwise a gap is unknown.
These are necessary evidentiary properties, not a defined verifier or an
operator authorization mechanism. The current allocator has delayed HA
Store snapshots and no independent authoritative transaction/hardware-effect
journal or rollback-resistant witness. This release has no genuine verifier
for historical external backups and no supported restore or override where
history cannot be verified. The maintainer chose `block_and_proceed`: ship
without a staged release and keep affected installations blocked potentially
indefinitely. Intact v1 upgrades remain supported under existing validation,
quiescence and no-corruption/partial-history gates. Fresh installs with
verified absence remain supported under existing adoption gates.
Do not implement an acceptance or override path based on hypothetical evidence.
This is not a claim that verifying a backup is mathematically impossible.
Any future verifier must be separately designed and reviewed before it may
authorize recovery; a different risk policy needs separate maintainer approval.

A matching digest, checksum, backup timestamp or successful backup is not
proof of recency. An operator statement or identity attestation, a lack of
log entries, current slot observations, and HA repair confirmation do not
prove that missing transactions or hardware effects never happened. No
combination of these replaces independent authoritative evidence. A
markerless v1 backup cannot establish completeness or justify creating a
genesis marker in this recovery case. If independent completeness cannot be
verified, persistent sanitized status must say external recovery is
unavailable, no supported restore or override exists in this release, and
the current registry, marker and quarantine evidence must be preserved.
Explain that a markerless v1 backup alone cannot prove completeness.
Issuance, reuse and release remain blocked.
This block may be indefinite; no external restore is supported in this release.
This includes legacy installations that never activated v2: a markerless v1
backup alone cannot prove complete history, and no risky recovery or new
legacy exception is authorized without a separate maintainer policy decision.
HA may present
its generic storage-corruption repair issue when v4.0.3's old Store
quarantines malformed JSON; submitting that confirmation is not proof that
the allocator envelope and marker were restored. Any default/fresh v1
registry v4.0.3 creates or writes after its old Store returns `None` is not
restored safety state; with the surviving activation marker, the new raw
reader classifies it as protective-unknown. The new reader itself never
quarantines. If no complete state or independent proof exists, keep
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

The registry and marker are loaded as bounded raw byte strings in the
executor and decoded only after size, file type and path checks. For an
envelope, compute SHA-256 over the exact bytes produced once by
`prepare_save_json`; the marker stores this digest. Recovery computes SHA-256
over the exact bytes read from disk and compares the values before schema
validation. It must not reserialize decoded JSON to calculate or confirm a
digest. Distinguish verified missing paths from unreadable, malformed,
oversized and unsupported files; only the former can participate in the
explicit genesis-absence case.

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

Recovery after an ordinary **process crash**, under the single-writer,
ordered durable-write and no external restore/deletion assumptions, with no
known evidence contradicting them, first validates the bounded raw bytes,
then re-acknowledges exact validated bytes using the atomic writer in
protocol order (pending marker before envelope; finalized envelope before
final marker). This rewrite does not reserialize or alter bytes. Do not make
the allocator available until the required file and directory fsync or
platform write-through acknowledgement succeeds. If re-acknowledgement
fails, or any raw read/write reports an error, remain protective-unknown.
This crash protocol reasons about a live transaction's recorded predecessor
or target and its pre-dispatch ordering, not about an external historical
backup. A marker digest establishes byte correlation, not recency or absence
of intervening operations. A later external restore of *both* files can
produce the same bytes as an uninterrupted crash; in that case, the two
histories are not always distinguishable. If both files are silently rolled
back with no surviving contrary evidence, the reader may accept the stale
pair under its ordinary-crash assumptions and lose later safety state, potentially
permitting unsafe PIN reuse; neither the marker nor this design guarantees
detection or safe recovery. Preventing undetected rollback requires an
independent outside record protected from the same rollback; no such safeguard
is provided by this design. The maintainer explicitly accepted this residual
risk (`accept_residual`). If restore/rollback is known or suspected, the
ordinary crash assumptions do not apply and the external repair gate above
remains closed.

1. If marker is pending and actual validated disk bytes match a recorded
   prior envelope digest/version/generation, re-acknowledge those exact prior
   bytes, restore the prior finalized marker and discard only the target
   intent, then retry it if still eligible. This is safe only when the
   embedded prior finalized record or validated v1 genesis is trustworthy,
   the writer's quiescence/order is proven, and target-dependent dispatch
   was impossible before final-marker acknowledgement. Do not lose prior
   pending clear A or other prior effects. For an explicit genesis-absent
   predecessor, require registry `ENOENT` and no activation/partial/quarantine
   evidence, then remove only that exact pending marker and fsync its parent
   directory; do not write, replace or delete an envelope path that was
   absent. If the marker removal/fsync is uncertain, or any condition is
   unprovable, enter protective-unknown instead.
2. If disk bytes match the target digest/version/generation, validate the
   complete target and re-acknowledge those exact bytes before finalizing the
   marker idempotently and restoring the committed bookkeeping and fences.
   If a writer reported failure after possible replacement (including
   directory-fsync failure), visible target bytes alone are not success; this
   explicit successful re-acknowledgement is required. Restoration is **not**
   dispatch of a hardware service: never infer a clear call from the
   envelope or marker. Observe the original identity and slot using fresh
   verified evidence. Finalization does not establish whether an earlier
   operation reached hardware; only the existing retry policy may re-dispatch
   after identity, capabilities and all applicable safety gates are
   revalidated. An already empty slot can recover from fresh evidence without
   replaying the reset.
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
RC-owned atomic replacement/fsync and serialized writes are the engineering
assumptions for these process-crash cases, not proof against faulty storage,
external edits or restoring older files. If such rollback is known or
suspected, keep protective-unknown even when an old digest matches.
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
When an executor worker is submitted, retain its underlying future and await
it through `asyncio.shield` in a cancellation-drain loop. For every
`CancelledError`, record cancellation and consume only that cancellation
request with `Task.uncancel()` (or the supported equivalent), then continue
shielded waiting; repeated cancellations must not cancel or abandon the
worker. Keep the allocator lock until the worker's result or exception is
known, map it to a typed outcome and apply success/protective-unknown state
while still locked. If cancellation was observed, do not dispatch any
target-dependent hardware action; preserve the acknowledged fence or unknown
state, release locks, and only then propagate cancellation. Never expose a
raw worker exception while draining. Coalesce only updates that cannot
weaken a fence. Benchmark and test slow I/O, repeated cancellation, lock
lifetime, success/failure outcomes, no dispatch after cancellation,
coalescing and lock inversions; blocking the event loop or an unbounded
flush queue is unacceptable. Lockless setup, refresh, display and
calendar-only check-in must retain the bounded busy/unavailable behavior
described above.

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
safety state. Preserve the evidence and remain protective-unknown: the
external-backup gate above requires independently verifiable authoritative
completeness evidence, and this plan defines no verifier for historical
external backups. A matching restored pair alone is not safe recovery.
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
