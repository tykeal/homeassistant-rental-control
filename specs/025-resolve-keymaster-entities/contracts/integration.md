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
| Select or repair lock | Existing HA config/options flow | Distinguishable KM entries; durable entry ID, retain unresolved legacy data and unrelated settings; same-original attestation is separate from selecting an entry |
| Observe/adopt | Registry + live HA states | Current verified entry/property/slot only; unreadable is not empty |
| Issue/amend/change dates | Existing text/switch/datetime entity services | Preflight all needed targets and confirmations; revalidate across awaits; partial changes remain outstanding |
| Reset/cleanup | Existing per-slot button and applicable text service | Same-slot reset; local current name/PIN evidence required; preserve original pending fence and separate PIN hold |
| Check in/out | Existing RC calendar and event processing | Verified current date entities; event source and inherited topology checked; lockless behavior unchanged |
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

When independent evidence cannot prove the original legacy association,
display distinguishable current KM entries and an explicit, unselected
confirmation that the selected entry is the **same original entry/association**,
not merely a similarly named replacement or the same physical device under a
new entry. Explain that this is the operator's historical assertion; it is
not proof that a slot is empty or that hardware erased a PIN. Cancel, no
attestation, missing/disabled or ambiguous selection, and unavailable state
retain the original hold and existing safety gates. A disabled candidate can
be identified but cannot supply live capability evidence. Selecting a
replacement must be distinguishable and must not transfer old work.

Bind legacy-unknown ownership only after current entry validation and durable
attestation/provenance transaction. Preserve all shared-lock RC owners,
original slot fences, reservation claims and exclusion semantics. Neither
attestation nor replacement selection releases a PIN, changes a guest code,
or bypasses the existing release/adoption/readiness checks. For an already
verified original entry A, selection or attestation of B cannot rebind A's
pending work to B.

### Registry persistence and downgrade

The shared registry uses one authoritative HA-compatible major-version-2
envelope and a single serialized writer; see
[data-model.md](../data-model.md#authoritative-registry-envelope-and-transactions).
Write/serialization failure, uncertain disk outcome or stopping state is not
confirmation and cannot authorize dispatch or release. Migrating v1
preserves its entire ledger plus unknown-origin holds; HA config-entry
updates are separate, so startup reconciles from the committed envelope.
An unrecoverable v2, missing required protection section, quarantined corrupt
file, or absent envelope with activation/partial-state evidence is
protective-unknown: no adoption-based rebuild, issuance, reuse, release or
overwrite. Use the marker/config correlation and verified restore protocol in
the data model; current slot states and exclusions cannot reconstruct lost
pending holds.
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

An actual v4.0.3 HA Store v1 loader must reject a **valid, parseable** v2
envelope; this can prevent all RC entries, including lockless ones, from
loading. Reinstall compatible newer RC without deleting or restoring the
registry. With malformed v2 JSON, HA can quarantine it and return `None`;
v4.0.3 then treats the registry as absent and may enter adoption recovery
without enforcing the new marker. This path is not a safety guard: stop
RC/HA, preserve the quarantined bytes and marker, reinstall compatible RC,
and follow verified restore/operator repair. Test the actual path. No
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
