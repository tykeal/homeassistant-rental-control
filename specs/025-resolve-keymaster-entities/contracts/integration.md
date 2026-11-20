<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Contract: Home Assistant Integration Boundaries

There is no new HTTP or GraphQL API. These contracts describe existing HA
entity services, bus events, config flow and public RC services; inventing
REST endpoints would misrepresent this feature.

## Supported entity identity

Both KM baselines use `entry_id + "_" + slugify(property_key)`. In the table
`n` is the actual managed slot number; the property key is the input, not
the unique ID. Domain is the prefix before the first dot; platform is
`keymaster`. The registry record must have that exact unique ID and the
selected KM config-entry ownership.

| Purpose | Property key |
|---|---|
| Name | `text.code_slots:n.name` |
| PIN | `text.code_slots:n.pin` |
| Per-slot reset | `button.code_slots:n.reset` |
| Slot enabled | `switch.code_slots:n.enabled` |
| Date limit enabled | `switch.code_slots:n.accesslimit_date_range_enabled` |
| Date start | `datetime.code_slots:n.accesslimit_date_range_start` |
| Date end | `datetime.code_slots:n.accesslimit_date_range_end` |
| Child override evidence | `switch.code_slots:n.override_parent` |

Source: [text](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/text.py),
[button](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/button.py),
[switch](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/switch.py),
[datetime](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/datetime.py);
the same keys were checked against v0.6.5. Enumerate any further properties
actually used in existing paths before converting them. A `button.reset_lock`
is not a per-slot reset. No current address or area is identity evidence.
On an inherited child slot, text/datetime and most switches may be
unavailable; override and notifications switches are exempt. In both
baselines a per-slot reset button can remain available despite unavailable
inherited text controls. Evaluate each capability and confirmation
independently; a pressable reset button is not proof of child write or
confirmation readiness.

## User actions and operation results

| Action | Boundary | Required outcome |
|---|---|---|
| Select or repair lock | Existing HA config/options flow | Distinguishable current KM entry; durable entry ID; retain unresolved legacy data and unrelated settings; auditable same-original-physical-lock attestation is separate from selecting an entry |
| Observe/adopt | Registry + live HA states | Current verified entry/property/slot only; unreadable is not empty |
| Issue/amend/change dates | Existing text/switch/datetime entity services | Preflight all needed targets and confirmations; revalidate across awaits; partial changes remain outstanding |
| Reset/cleanup | Existing per-slot button and applicable text service | Same-slot reset; local current name/PIN evidence required; preserve original pending fence and separate PIN hold |
| Check in/out | Existing RC calendar and event processing | Verified current date entities; event source and inherited topology checked; lockless calendar behavior remains independent of KM, subject to the protective-unknown issuance gate |
| `force_reissue`, `set_slot_exclusion` | Existing RC service schemas | Keep supported unambiguous lockname+slot and RC sensor selectors and dry-run behavior; ambiguous or unverified hardware target is refused |
| Recovery/reload | HA entry/registry/state lifecycle | Invalidate old evidence, rebind once, retry eligible work only after revalidation |

No whole-lock reset, service-completion callback or reset bus notification is
proof of local or physical clearing. Exclusion is not evidence or a write
authorization. Keep all existing active-credential, adoption, ownership and
release gates, including the limited existing reissue exemptions.

`set_slot_exclusion` retains the current two-stage RC-target behavior:
registered RC sensor lookup and its existing uniquely matching reservation
attribute fallback, or lockname+slot range resolution; on withdrawal preserve
the persisted-exclusion/out-of-range fallback and existing refusal results.
These identify an RC entry only and cannot establish KM identity. Before
**creating** an exclusion, require the spec's verified association and
existing unreadable/managed/unclaimed guards. Withdrawing an exclusion
remains config cleanup and does not clear holds or authorize hardware.
Dry-run uses the same resolution/guards without writes.

### Legacy repair and attestation

When independent evidence cannot prove **legacy-unknown** provenance, display
distinguishable current KM entries and an explicit, unselected confirmation
that the selected current entry is attached to the **same original physical
lock**. This may apply even if the original KM entry was deleted and recreated.
Explain that this is an auditable operator assertion, not machine proof of
physical continuity; a current name, slug, entry ID or matching address does
not prove it. The selected current entry and its capabilities still require
independent current validation. Cancel, no attestation, missing/disabled or
ambiguous selection, and unavailable state retain the original hold and
existing safety gates. A disabled candidate can be identified but cannot
supply live capability evidence. Uncertainty or ambiguity fails closed.

Bind legacy-unknown ownership only after current entry validation and a
durable, auditable attestation/provenance transaction. Preserve all shared-lock
RC owners, original slot fences, reservation claims and exclusion semantics.
Neither attestation nor replacement selection releases a PIN, changes a guest
code, confirms a clear, or bypasses existing release/adoption/readiness checks;
fresh trustworthy evidence from the original physical lock and exact slot
remains required for release. Attestation never transfers work already known
to belong to entry A onto B, even when both refer to the same physical lock.

### Registry persistence and downgrade

The shared registry uses one authoritative HA-compatible major-version-2
envelope and a single serialized writer; see
[data-model.md](../data-model.md#authoritative-registry-envelope-and-transactions).
Write/serialization failure, uncertain disk outcome or stopping state is not
confirmation and cannot authorize dispatch or release. Migrating v1
preserves its entire ledger plus unknown-origin holds; HA config-entry
updates are separate, so startup reconciles from the committed envelope.
The persistence path MUST NOT call HA's `Store.async_load`, `load_json`,
`write_utf8_file_atomic`, `save_json`, or Store save/quarantine behavior for
the registry or marker. HA loading can quarantine malformed JSON, log its
paths and exception text, publish them in a repair issue, and return `None`;
this is neither a private load nor proof of absence. Use an RC-owned,
bounded raw-byte reader in the executor for both paths. Only verified
`ENOENT` for both paths without activation, partial-write or quarantine
evidence is absence; unreadable, malformed, oversized, unsupported or
symlinked files are distinct protective-unknown outcomes and their evidence
MUST remain in place. Bound registry reads to 32 MiB, marker reads to
64 KiB, and each directory evidence/cleanup scan to 4,096 entries; accept
exactly-at-limit input and fail closed at limit+1 or when an absence scan
cannot complete. Count all directory entries, not just matches; inspect the
4,097th only to detect overflow, and require end-of-directory for a complete
4,096-entry scan. Collect cleanup candidates before deletion and delete none
on overflow/incomplete enumeration. Where feasible open with `O_NOFOLLOW`
and validate the descriptor with `fstat`. On Windows reject reparse traversal
of all components with verified handle-based opening or fail closed without
disk access; ACL or final-component checks alone are insufficient.
Evidence-scan overflow/incompletion has persistent sanitized actionable
status and protective-unknown; cleanup leaves candidates intact and reports
sanitized maintenance status. No typical directory size is assumed.
Present empty/invalid wrappers are not absence. Apply the data model's
validated v1 same-major minor compatibility/default and v2 safety validation
without HA write-on-read behavior. The old v4.0.3 HA Store downgrade behavior remains
separate: it rejects a decoded top-level integer `version: 2` before payload
inspection, but may quarantine malformed JSON and return `None`.

Use an RC-owned adapter around HA's runtime `atomicwrites.AtomicWriter`
dependency; do not add a package dependency or assume a brittle exact API
signature. Verify its required behavior on minimum HA 2026.11.0 and supported
CI HA lines, and fail closed rather than use HA logging wrappers if missing.
Serialize each immutable snapshot once with `prepare_save_json`, hash the
exact resulting bytes, and write those same bytes. Compare the digest with
exact raw disk bytes on recovery; never reserialize decoded data to establish
agreement. Before any transaction replacement, preflight the exact serialized
target envelope and pending/final marker against the same respective read
caps; an oversize output fails without mutation or dependent dispatch.
Convert `Exception` failures at serialization, encoding, hashing,
raw load/decode, writing, replace/fsync and executor boundaries—including
non-`OSError` errors and sensitive causes/contexts—to typed fixed failure
codes. Never retain, log, chain, expose or raise raw/chained errors, paths,
tracebacks or PIN-bearing content to HA. On cancellation, retain and
`asyncio.shield` the underlying worker in a drain loop; handle repeated
cancellation requests without cancelling/abandoning it, keep locks until the
result is known and state is applied, suppress target-dependent dispatch,
then propagate cancellation.

Temporary files MUST use a verifiably RC-owned prefix and opaque per-operation
ID. Bounded stale-temp cleanup is allowed only after successful validated
recovery, with the shared writer lock held and workers quiescent; only
regular non-symlink files matching the exact owned pattern may be removed.
All participating RC readers, writers and cleanup MUST use the same
process-shared OS lock at
`.storage/rental_control.code_registry.io_lock`; tests must prove a second
process holding it blocks cleanup. All in-process operations acquire the
allocator async lock before the OS lock, releasing in reverse order.
Acquire with nonblocking POSIX `flock` or Windows fixed-range `LockFileEx`,
bounded retry and one five-second monotonic deadline beginning before
asynchronous allocator-lock acquisition; OS-lock retries use the remaining
time. Never block the event loop. Busy is retryable without this request
reading/writing registry or marker, running cleanup or dispatching dependent
hardware work; lock-file attempts and other already-active work may continue.
Unsupported
or unsafe locking fails closed with sanitized unavailable status, but does
not itself establish persistent protective-unknown. Cancellation interrupts
contention retry and drains the worker; actual started I/O still drains to
completion, without a claim that arbitrary stalled filesystem I/O is bounded.
Only platform-tested contention error codes are busy. On Windows bind
`kernel32` with `ctypes.WinDLL(..., use_last_error=True)`, capture
`ctypes.get_last_error()` immediately on failure, and classify raw
`ERROR_LOCK_VIOLATION` 33 before Python errno conversion; raw access denied 5
is unavailable. Unknown, unsupported and permission errors are sanitized
unavailable. Unsafe/unowned existing lock paths are never chmodded or have
their ACL modified by RC. Windows locks a range past
EOF on a stable private file without initializing content; a crash leaving
an empty file is recoverable. Never replace, rename or unlink the lock path
while participants can run; stop all HA/RC processes before path repair.
Do not claim verified Windows protected-operation support until native lock
and safe-traversal tests pass. Otherwise gate protected operations while
retaining calendar-only functionality; do not remove platform support.
This lock coordinates disk operations,
not allocator/hardware work across HA processes. The scan is capped at 4,096
directory entries, never includes authoritative registry/marker or
`.corrupt.*` files, and any uncertainty leaves candidates untouched.
With no valid marker authenticating the exact v1 bytes, a matching
`.corrupt.*` sibling or incomplete evidence scan makes even a valid v1
registry protective-unknown; do not migrate/adopt from it or allow
attestation to override missing history. A valid finalized v1-genesis marker
matching the exact v1 bytes after a proven crash rollback permits retrying
activation only after re-acknowledgement and quiescence/live-state checks,
not when external restore, loss or corruption is known or suspected.
Any writer error after possible replacement, including directory-fsync
failure, is ambiguous: no dependent dispatch or success claim. On restart,
validate then re-acknowledge exact validated bytes before finalization or
resume; a failed re-ack keeps the allocator protective-unknown.
An unrecoverable v2, missing required protection section, quarantined corrupt
file, or absent envelope with activation/partial-state evidence is
protective-unknown: no adoption-based rebuild, issuance, reuse, release or
overwrite. In the new version, lockless RC entries still set up and display
their calendars without Keymaster, but new lockless code issuance is withheld
until the shared allocator safety state is restored. Use the marker/config
correlation and verified restore protocol in the data model; current slot
states and exclusions cannot reconstruct lost pending holds.
While the allocator is busy or locking is unsupported, unsafe or unavailable,
preserve lockless setup, first refresh, display and calendar-only check-in via
a bounded non-raising typed fallback. Withhold shared issuance, reuse, release
and new dependent hardware/ownership dispatch; preserve loaded claims and
fences. Unavailable locking alone does not establish persistent corruption.
Do not promise every
nonmutating read avoids the lock or bound calendar network/already-started
filesystem operations.
Expose persistent, sanitized, actionable status directing the operator to
restore a verified, sufficiently current complete envelope and matching
marker. For v1 with quarantine evidence the approved path is verified backup,
not risky operator override: validate the restored pair and exact digest, and
independently verify completeness through the incident/quiescence point as
specified in the data model. A markerless v1 backup, digest, timestamp, slot
observation or attestation alone is not such proof; if unavailable, keep the
gate. Preserve claims and fences already loaded, avoid hardware or ownership
mutation where information is unavailable, and do not claim that codes missing
from damaged state remain visible or present, or that they are absent or
reusable. HA may separately present its generic storage-corruption repair
issue after quarantining invalid JSON; confirming that issue is not restoration
or validation of the allocator state.
If no sufficiently current verified complete marker-bearing backup exists,
persistent sanitized status must say so, explain that a markerless v1 backup
alone cannot prove completeness, and state there is no automatic recovery or
operator override; issuance and release remain blocked.
This can continue indefinitely if no qualifying backup can be established.
That includes legacy installations that never activated v2: markerless v1
alone is insufficient, and another recovery policy requires separate approval.
Each pending marker records a proven prior finalized/genesis version,
generation and exact-byte digest plus a distinct target transaction,
generation and digest. After process crash an exact prior match permits
rolling back **only that pending target** when pre-dispatch ordering and
the predecessor are proven; an exact target match permits finalization.
All other cases remain protective-unknown. Earlier in-flight operations keep
their original fences and are never treated as proof the new intent dispatched.
Restoring an envelope or fence is bookkeeping, not a hardware action. After
restart observe the original slot using fresh verified evidence; never infer
a clear call from the envelope. Only the existing retry policy may re-dispatch
after identity, capability and safety-gate revalidation.

Config-history pruning defaults to retaining the chain when using public HA
APIs, which do not acknowledge a durable config save. Any optional on-disk
checkpoint adapter depends on HA's private `core.config_entries` format and
must validate a known envelope and entry structure plus writer quiescence;
unknown/inaccessible data retains the chain and surfaces pressure.

An actual v4.0.3 HA Store v1 loader must reject a JSON-decoded object with an
integer top-level `version: 2` before inspecting its inner payload. This can
prevent all RC entries, including lockless ones, from loading; it does not
depend on successful validation of the payload or its safety metadata. Keep
this old-version downgrade block distinct from new-version behavior. With
malformed JSON, HA can quarantine the file and return `None`; v4.0.3 then
treats the registry as absent and may enter adoption recovery without enforcing
the new marker. HA's generic repair confirmation does not restore the file.
Any default/fresh v1 registry v4.0.3 creates or writes after HA returns `None`
for the quarantined file is not restored safety state; with the surviving
activation marker, the new reader classifies it as protective-unknown.
This path is not a safety guard or safe recovery. Stop RC/HA and preserve the
quarantined bytes and marker. Reinstalling compatible newer RC alone cannot
reconstruct missing safety state: restore a verified, sufficiently current
complete envelope and matching marker through the stopped repair process.
Without that restore, remain protective-unknown. Test the actual path. No
guard is promised for other versions or deleted/replaced/restored storage.

## Registry and lifecycle messages

Entity-registry bus `entity_registry_updated`: create/remove use
`action, entity_id`; update uses `action, entity_id, changes` (old values),
and rename includes `old_entity_id`. Registry state is updated before the
event. A tracker keyed by old address sees the rename but must be rebound.
Config-entry changes are dispatcher notifications and state hooks, not
`hass.bus` events. Use public supported hooks and unsubscribe all
subscriptions on rebind and unload. After any callback revalidate the
registry mapping and generation; never trust the event address alone.
Entity absence, disablement, entry setup/reload/removal and `restored: true`
are not live readiness. Late state must cause reevaluation without a manual
reload; unrelated state cannot do so as if it were the intended slot.

Use an integration-owned lifecycle manager per RC entry: observe entity
registry create/remove/update globally or with equivalent coverage for late
entities (a tracker for only already-known IDs cannot see creation of an
unknown address), and filter by exact current expected identities and
old/new addresses. Handle removals even though payload has no unique ID by
matching the previously verified mapping; handle changes of entry ownership,
platform-relevant registration and `disabled_by` as invalidations. Track KM
config-entry add/remove/update via the supported dispatcher, and the selected
entry's state changes via its lifecycle hooks; use entity/live-state signals
to cover platform readiness. Rebind the selected-entry hook when association
changes. Register state listeners before taking a fresh snapshot, then
revalidate the generation and snapshot after subscription to close the
discovery/subscription race. Atomically replace address→identity routing,
unsubscribe prior trackers and reject queued callbacks with old generations.
Coalesce storms without dropping a later invalidation; different RC entries
sharing a KM entry each own their mappings and teardown.

## Keymaster event

`keymaster_lock_state_changed` in both baselines carries `entity_id`
(underlying physical lock), `lockname`, `state`, `code_slot_num`,
`code_slot_name`, `notification_source`, `action_code`, `action_text`.
Require a valid `entity_id` matching a current verified source; reject
conflicting identity fields, stale links, shared physical addresses without
unique attribution, invalid/zero/out-of-range slots and non-unlock events.
The slot name is not an identity credential. For inherited child check-in,
prove same-slot inheritance and parent/child link; an overridden child slot
does not check in a parent guest. The separately emitted
`keymaster_code_slot_reset` carries `entity_id` and `code_slot_num` but is
not a clear confirmation.

Each KM config entry's current `lock_entity_id` is the event's expected
physical source, and current `parent_entry_id` is a topology input. Because
KM can derive the latter via name-based backfill, independently validate the
entry, relationship and slot inheritance; never treat name backfill alone as
proof. A corrected slot-bearing unlock may follow an initial slot-zero
unlock; reject the zero event and apply existing once-per-stay behavior to
the attributable event. Reused/renamed physical IDs require current verified
configuration, not substitution based on a matching name.

## Version result contract

Use `homeassistant.loader.async_get_loaded_integration` to retrieve already
loaded KM metadata; inspect its raw manifest version for release shape and
the integration's `version`/HA AwesomeVersion comparator for ordering. A
missing/not-loaded integration, invalid metadata or comparator failure is
unknown; explicitly catch `homeassistant.loader.IntegrationNotLoaded` and
never let it fail RC startup. HA 2026.11.2 supplies AwesomeVersion 25.8.0
and its loaded `Integration.version` returns that comparator. A separate
entry/capability readiness check still applies. `async_get_integration` may
load metadata and is not equivalent to the loaded-only lookup. Process in
order:

1. Exact unsuffixed `0.0.0` or `v0.0.0`: `development`.
2. Missing/unparseable metadata, not exactly three numeric dot-separated
   release components after an optional `v`, or suffix not recognized as
   SemVer/PEP 440: `unknown`.
3. If `< v0.6.0` is true: `outside_support`; else if `>= v0.6.0` is true:
   `supported`; else or comparison failure: `unknown`.

`v0.0.0-dev` and `v0.6.0-rc.1` are below floor; `v0.6.1rc1` is supported;
`v0.6.0+local` is unknown because neither comparison is true. `7`,
`2026.11`, `20260101`, `0.6`, `0.0`, and `v.0.0.71` are unknown due to
shape. Versions v0.6.0/v0.6.5 are supported. Unknown and below-floor are
diagnoses, not new version-only blocks. All operations remain subject to
identical required identity, capability, state and safety checks.

## Privacy contract

Logs (including debug), notifications and downloadable diagnostics use
allowlisted safe context and actionable status. Never output a PIN, raw
state/event/service payload, or exception text that may echo credentials.
Distinguish redacted data from unavailable data; tests inject synthetic PINs
into unexpected event fields and exceptions.
