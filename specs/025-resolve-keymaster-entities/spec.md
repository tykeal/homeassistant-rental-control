<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Resolve Keymaster Entities by Verified Identity

**Feature Branch**: `025-resolve-keymaster-entities`

**Created**: 2026-11-20

**Status**: Draft

**Input**: [Issue #954](https://github.com/tykeal/homeassistant-rental-control/issues/954): Manage the configured Keymaster lock regardless of entity naming, and diagnose missing capabilities without misreporting the installed version.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Manage reservations regardless of entity names (Priority: P1)

As a rental operator, I can put locks in areas and rename their entities without
breaking reservation access or changing which lock or slot Rental Control manages.

**Why this priority**: Reliable slot observation and confirmed changes are necessary
to issue guest access safely.

**Independent Test**: Exercise a reservation lifecycle with simulated Keymaster
entities whose current IDs bear no resemblance to the lock name. Include distractor
entities with the formerly expected IDs and track every read used as evidence and
every write target.

**Acceptance Scenarios**:

1. **Given** two separate Keymaster locks with overlapping slot numbers and distinct
   Rental Control entries, **When** Home Assistant generates different entity IDs or
   an operator assigns or changes device areas, **Then** startup and normal refresh
   read the name, PIN, enablement and applicable date limits from the intended
   slots, and verified existing reservations can be adopted under the existing
   adoption rules. Include the reported two-device layout: each has an area and
   its own calendar; Keymaster exposes slots 20–22 and 23–25 respectively, and
   each calendar manages two slots starting at 20 or 23. Both recover at startup
   without removing areas or recreating entries; neither writes its third,
   unmanaged slot or raises a false version alert. This is a regression fixture,
   not a claim that the original failure has been reproduced.
2. **Given** a readable eligible slot and verified controls, **When** Rental Control
   issues or updates a reservation, **Then** the correct entry and slot receive
   the applicable name, PIN, switch and date-window updates, and operation feedback
   is evaluated against that same slot. A date-only change preserves the code
   whenever existing reservation policy calls for it. Exercise both reservation
   amendments and an options change that recomputes buffered dates; neither
   confirmation path accepts the values of a similarly named entity.
3. **Given** a managed reservation is due for clearing, **When** the reset and any
   existing name cleanup are performed, **Then** the correct slot is targeted and
   is not released for reuse until current Keymaster name and PIN states for that
   slot meet the text evidence rule below. Exercise normal reset publication
   (a native `None` becoming HA `unknown`) in both baseline fixtures.
   Separately, use synthetic regression fixtures with current, verified
   states to exercise blank and whitespace-only strings. A literal name of
   `None` and an empty PIN also satisfy the existing empty-text check in a
   synthetic fixture. That confirms the observation only; it does not by
   itself release the PIN for reuse.
   A missing state record is not a native empty value.
   Missing, unavailable, restored-only, synthetic masked or still-populated
   states do not confirm the clear.
   Neither a whole-lock reset nor a reset notification substitutes for these
   observations. This confirms Keymaster's slot state, not hardware erasure.

### User Story 2 - Recover safely when Keymaster entities change (Priority: P1)

As a rental operator, I can restart Home Assistant, reload Keymaster, or rename
entities while it is running without rebuilding my configuration or leaving Rental
Control listening to an obsolete entity.

**Why this priority**: A transient startup gap or stale listener must not block
access permanently, route an operation elsewhere, or falsely confirm one.

**Independent Test**: Start with Keymaster platforms delayed, then rename entities
and reload Keymaster while Rental Control remains loaded. Capture effects of
current, stale and unrelated state events, including during an in-flight operation.

**Acceptance Scenarios**:

1. **Given** a saved lock association and delayed integration setup, registry
   registration or live state at startup, **When** the verified entities
   become ready, **Then** Rental Control refreshes its observations and resumes
   eligible work without manual reload,
   subject to the existing safety gates. It does not interpret not-yet-ready slots
   as empty or allow premature issuance or adoption.
2. **Given** a managed entity is renamed after setup, **When** its current ID
   receives manual name, PIN, enabled, date-limit, or reset feedback, **Then**
   Rental Control applies the existing feedback rules to its verified slot once,
   and neither the old ID nor an unrelated state event can change bookkeeping.
3. **Given** a rename or reload occurs between an operation and its confirmation,
   including reuse of an old ID by an unrelated entity, **When** confirmation is
   evaluated, **Then** only trustworthy evidence from the intended current slot
   counts; otherwise the operation stays unconfirmed. Repeated reload and unload
   do not leave duplicate listeners, orphaned callbacks, or stale targets.
4. **Given** a verified registry entity is deleted, disabled or becomes
   unavailable, **When** it returns, is re-enabled, or is registered under a
   new address with the same verified identity, **Then** the affected
   observations and subscriptions refresh without manual reload. If a
   conflicting record appears, the affected operation stays blocked until
   the conflict is resolved.

### User Story 3 - Preserve assignments and check-in routing (Priority: P1)

As an operator upgrading an existing deployment, I retain my calendar settings,
managed slots, and check-in behavior without re-creating entries or losing the
identity of active stays.

**Why this priority**: Resolving a different lock by mistake is worse than requiring
the operator to confirm an ambiguous assignment.

**Independent Test**: Upgrade persisted fixtures for separate locks, two Rental
Control entries with disjoint ranges on one Keymaster lock, parent and child locks,
ambiguous or missing legacy selections, and a lockless entry. Exercise check-in
and restart after migration.

**Acceptance Scenarios**:

1. **Given** uniquely attributable legacy lock settings, **When** Rental Control
   upgrades and restarts, **Then** both ordinary and shared-lock/disjoint-range
   entries retain their original assignment, range, active reservation and
   outstanding confirmation state. The migration itself does not reset or rewrite
   any lock slot and is safe to repeat.
2. **Given** duplicate titles, colliding normalized lock names, a disabled
   candidate, or a stale legacy selection, **When** the association is
   evaluated, **Then** title equality and registry readiness do not resolve
   an identity collision; even a disabled candidate counts toward ambiguity.
   No candidate means unresolved; one provably intended but disabled entry
   is identified as disabled, not replaced. Unverified assignments retain the
   legacy setting for repair and require explicit, distinguishable reselection
   if automatic verification is not possible. A same-named replacement for a
   removed verified entry is never silently selected.
3. **Given** a monitored parent, a linked child, and an unrelated lock, **When**
   actual v0.6.0 and v0.6.5 payloads report valid unlocks on a managed parent slot
   and on the child's corresponding inherited slot, **Then** each attributable event
   affects only the intended stay. An independently overridden child slot does
   not check in a parent guest based on matching slot number alone. Conflicting
   identity fields, a missing or invalid underlying lock `entity_id`, stale
   links, shared physical addresses without a unique
   source, unrelated locks, locked events and invalid, zero or out-of-range slots
   do not trigger check-in. Missing attribution for an inherited slot does not
   grant permission to write a child's unavailable controls or redirect a
   child's own Rental Control operations to its parent.
4. **Given** Rental Control intentionally has no lock configured, **When** it
   starts, reloads or runs calendar-only check-in behavior, **Then** it keeps
   existing lockless behavior without looking up Keymaster capabilities or raising
   a Keymaster warning.
5. **Given** a reservation is already associated with a verified managed slot,
   **When** automatic check-in, manual state changes, or normal/early checkout
   read or change the checkout limit, **Then** the applicable existing behavior
   uses only that slot's verified dates. Invalid keypad attribution does not
   disable calendar-only transitions or authorize any hardware action.
6. **Given** two calendars sharing one Keymaster lock with disjoint ranges and
   persisted observed-code owners, **When** one calendar is upgraded, reloaded,
   or receives `force_reissue` or `set_slot_exclusion` through its existing
   supported selector, **Then** the other entry's slot ownership, reservations
   and release guards remain intact. Lockname-plus-slot and supported Rental
   Control sensor selectors retain their unambiguous behavior; ambiguous
   targets are refused, and dry runs do not change slots or ownership.

### User Story 4 - Diagnose version and capability failures separately (Priority: P2)

As a rental operator, I get an actionable explanation for a blocked slot rather
than being told to upgrade a compatible Keymaster installation.

**Why this priority**: The correct remedial action depends on whether the
installation is unsupported or a specific control cannot be resolved or used.

**Independent Test**: Vary installed version evidence independently from lock
association, entity presence, enablement and runtime state. Inspect user-facing
messages, logs and downloadable diagnostics with synthetic PINs.

**Acceptance Scenarios**:

1. **Given** a compatible installed Keymaster version with a missing, disabled,
   unavailable, or multiply matched reset capability, **When** Rental Control
   diagnoses the blocked action, **Then** it reports the applicable reason with
   the affected entry, slot and capability and a corrective action, never an
   incompatible-version warning based on the reset failure. An unrelated button
   with the old ID does not satisfy the capability.
2. **Given** packaged v0.6.0 and v0.6.5 installs and a source checkout declaring
   v0.0.0 in separate fixtures, **When** version is diagnosed and a slot operation
   is requested, **Then** the packages are reported as supported and v0.0.0
   as an explicitly allowed development placeholder, not incompatible or
   unknown. Operations proceed only with all required checks satisfied.
   The packaged v0.6.0 and v0.6.5 fixtures exercise the read, write, date-update,
   reset, confirmation and check-in scenarios using each release's contracts.
3. **Given** verification fails or an operation cannot be confirmed, **When**
   the operator inspects logs, warnings or exported diagnostics, **Then** the
   output describes actionable context without including PINs or dumping raw
   states, payloads, service data or exception text that may contain credentials.
   Include a synthetic PIN in an echoed exception and an unexpected event field
   as negative privacy tests; test both normal and debug logging.
4. **Given** a missing, unparseable or otherwise unknown loaded version,
   **When** a slot operation is requested, **Then** diagnostics report the
   version as unknown. The operation may proceed when every required identity,
   capability, state and safety check passes and is withheld if any required
   check fails. Exercise both outcomes for each unknown-version case.
5. **Given** reliable loaded metadata for a release below v0.6.0 other than
   the v0.0.0 exception, **When** compatibility is diagnosed, **Then** the
   warning reports the version and the v0.6.0 support floor independently of
   any capability failure. Version status alone adds no new hardware block.
   Exercise otherwise identical valid and invalid capabilities to show that
   the existing safety rules still determine whether the operation can proceed,
   without promising support for the older release.
6. **Given** loaded metadata for `0.0.0`, `v0.0.0-dev`, `v0.6.0-rc.1`,
   `v0.6.0+local`, `v0.6.1rc1`, `7`, `2026.11`, `20260101`, `0.6`, and
   `0.0` and `v.0.0.71` in separate fixtures, **When** compatibility is diagnosed,
   **Then** only the unsuffixed zero version is the placeholder exception.
   `v0.0.0-dev` and `v0.6.0-rc.1` are below the floor, and
   `v0.6.1rc1` is supported. `v0.6.0+local` is unknown because neither
   floor comparison is true. `7`, `2026.11`, `20260101`, `0.6`, and `0.0`
   are unknown because they lack the required release shape, even if the
   comparator recognizes them. For the unknown cases, test that the same
   operation can proceed with all required checks satisfied and is withheld
   if any required check fails. Loaded metadata `v.0.0.71` is also unknown:
   the extra dot after `v` fails the release shape. It does not qualify for
   the `v0.0.0` exception.

### User Story 5 - Keep guest access and ownership safe during repair (Priority: P1)

As an operator with a guest already holding a code, I can diagnose and repair
an uncertain lock without accidentally resetting, overwriting or redistributing
that code.

**Why this priority**: A lost observation is not proof that the guest's code
stopped working, even after a restart or lock reselection.

**Independent Test**: Simulate an active stay and a different slot pending clear.
Break identity and state in turn; exercise restart, association repair, ordinary
and forced-release paths, and an operator slot exclusion.

**Acceptance Scenarios**:

1. **Given** an observed working guest code, **When** its lock becomes unresolved,
   disabled or ambiguous, or the required states become unreadable, **Then**
   Rental Control does not clear, overwrite, rotate or shorten that guest's
   access as a consequence of the uncertainty. It keeps existing reservations,
   code ownership and holds, does not newly advertise an unverified code as
   working, and resumes reconciliation only with fresh verified evidence.
2. **Given** a pending clear on lock A, **When** Rental Control restarts, the
   entity address changes or is reused, the entry is replaced, or an operator
   selects lock B with the same slot number, **Then** an empty slot on B,
   another slot on A, or any unrelated entity at a reused address does not
   clear A's hold, release its code for reuse, or direct A's cleanup to B.
   A rename preserving the same verified identity can recover on fresh evidence
   from the original slot on A; if A cannot be verified, the hold and actionable
   repair status survive without an automatic release.
   Separately, after slot confirmation on A, keep the PIN reserved if another
   allocation owner or a forced-reissue hold remains. Exercise a stale owner
   whose slot is still represented by another owner, an adoption conflict, and
   a still-observed PIN. The stale owner may be retired under the existing
   rule without freeing the PIN; the conflict and observed PIN retain the
   corresponding protection under the existing release guards.
3. **Given** a verified lock association and an eligible unreadable managed slot,
   **When** an operator uses the existing exclusion service, **Then** the
   current exclusion rules still apply. It can affect the issuance gate only,
   not mark the slot cleared, drop its holds, or authorize a write to it. An
   exclusion still expires on verified readability or range removal, and an
   options save retains its documented exclusion-reset behavior.
4. **Given** an entire association is unverified, or an unverifiable slot still
   has an active claim, **When** repair or exclusion is requested, **Then**
   exclusion cannot establish an association or bypass the relevant gate, and
   a claimed slot is refused. Test unobserved-at-startup versus previously
   adopted entry failures: retain existing cross-entry and entry-wide holds
   respectively, subject to the existing allocator recovery rules. No default
   change to block only individual slots.
5. **Given** a verified baseline slot starts with missing or unavailable
   controls and then publishes initially empty text states from its live
   integration, **When** startup observation or pending-clear recovery runs,
   **Then** only the current, ready publication counts. Exercise HA's actual
   `unavailable` registry placeholder (`restored: true`) before live setup,
   an old observation invalidated by reload, a synthetic masked value, and a
   reset event without both text states becoming empty: none count as evidence
   for release. Also test a synthetic `unknown` marked `restored: true` to
   check that an empty-looking placeholder is rejected.
   A failed provider clear followed by empty Keymaster text is not
   represented as physical-lock verification.

### Edge Cases

- An exact-looking entity in another integration, on another Keymaster entry, with
  the wrong platform/slot/property, or not backed by a verified registry entry
  cannot satisfy a capability. Duplicate candidates for an identity are not
  resolved by picking the first, even if one is disabled.
- `unknown` is not a universal readiness failure or an empty value for all
  capability types. Recognize it as the baseline empty-text representation
  only in the current, available Keymaster name/PIN observation described below.
- Removing, disabling, or replacing the selected entry while an operation is
  pending invalidates affected observations as evidence for subsequent actions.
  Recovery requires revalidating the association and capabilities; stale state is
  not proof that a new target is safe.
- Entries sharing a Keymaster lock keep their disjoint ranges; similar names and
  slot numbers across locks, unmanaged slots and events from unrelated children
  do not authorize any cross-entry action.
- If an operation has already begun when its target changes or disappears, no
  remaining step may proceed using unverified information. Partial effects are
  neither reported as success nor erased from outstanding reconciliation.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Rental Control MUST identify the selected lock by its actual
  Keymaster configuration entry. For each capability, it MUST verify registry
  ownership by that entry and a supported, exact stable entity unique ID for
  the expected platform, slot and property before using the entity's current
  address. Lock names, areas, device labels, and substring matches on current
  entity IDs MUST NOT serve as runtime routing or fallback evidence.
- **FR-002**: The verified association MUST govern slot snapshots, startup
  readability and readiness refreshes; direct PIN, name, enablement and
  date-range reads; adoption and override snapshots, including fresh text
  reads used by clear preflight and pending-clear release; issuance and its
  enablement/date-limit switches; direct and
  override date-window reads, writes and confirmations, including options/buffer
  updates and check-in/checkout; per-slot reset, fallback name clearing, and
  post-reset name/PIN confirmation; and state-change subscription, slot/property
  attribution and feedback reads. This applies to normal and recovery paths,
  service-triggered cleanup and reissue, not just initial setup. Neither a
  name-shaped address nor a whole-lock reset MAY substitute for a missing
  per-slot capability.
- **FR-003**: Rental Control MUST preserve existing issuance holds, adoption
  requirements, observation gates, feedback-suppression rules, and clear
  confirmation and reuse fences. Unverified or unreadable evidence MUST NOT
  count as an empty slot, a successful operation, or permission to expose or
  issue unverified access. Before dispatching a change, verify the capabilities
  required to perform and confirm it; if verification fails during the change,
  withhold success and retain the outstanding state for safe reconciliation.
- **FR-004**: A verified association MUST persist across restarts without relying
  on mutable names. Upgrade MUST preserve settings, ranges and reservation and
  reconciliation state, and can adopt a legacy association only where a unique,
  intended entry is verifiable. Resolve legacy values against the Keymaster
  config entries using the existing lock-name normalization, not titles, areas
  or generated addresses; collisions, absent entries and unverifiable legacy
  identity require repair, not a first match. An unverified entry MUST NOT become
  lockless. Offer explicit reselection without entry deletion and distinguish
  similarly named choices; selection alone does not prove that old ownership,
  exclusions or pending work belong to the replacement lock.
- **FR-005**: For registry or Keymaster lifecycle changes, Rental Control MUST
  stop using stale addresses as targets or evidence and resume eligible work
  after revalidation and fresh observation. Cover config-entry setup, reload,
  disablement and removal, relevant registry creation, removal, enablement and
  renames, and readiness signaled by live state after startup or recovery. Only
  current, attributable feedback may affect overrides, reservation state, or
  operation confirmation; repeated updates and unload MUST NOT accumulate
  active subscriptions.
- **FR-006**: Rental Control MUST isolate managed ranges across entries, including
  disjoint ranges on the same lock, and continue supported parent/child
  check-in behavior using verified topology, slot inheritance and unambiguous
  event attribution. Evaluate fields appropriate to the supported payload;
  for v0.6.0 and v0.6.5 require a valid event `entity_id` matching the verified
  underlying lock entity for the source Keymaster entry. `lockname` may
  corroborate that match or help disambiguate entries for that verified lock,
  but cannot replace the physical source. Do not ignore
  conflicting fields, infer a slot from an entity name, or treat a matching
  slot number or name alone as proof. Preserve existing monitoring, unlock,
  managed-range and stay checks. A child event does not extend the configured
  scope of writes. Lockless entries MUST remain independent of Keymaster.
- **FR-007**: Rental Control MUST distinguish a reliable, installed-version
  result from a missing or ambiguous lock association and from missing,
  disabled, not-ready, unavailable, ambiguous, or unsupported capabilities.
  The existing reset-button presence check MUST NOT produce a version verdict.
  Failures MUST indicate which entry, slot or capability is affected where
  applicable and what the operator can verify, enable, reselect, or wait for.
  Use Home Assistant's loaded manifest version and its version comparator.
  Apply the unsuffixed v0.0.0 exception first (with or without the `v`
  prefix) and report it as an allowed development placeholder. Next, before
  comparing to the floor, require a release label with an optional `v`,
  exactly three dot-separated numeric `major.minor.patch` components, and
  an optional suffix recognized by Home Assistant's comparator as part of a
  SemVer or PEP 440 version (such as a prerelease or build/local suffix).
  Report missing metadata, non-release shapes, and labels that do not parse
  as one of those version forms as unknown. For any remaining verified
  release version, report it as outside support if `<` stable
  v0.6.0 is true, or supported if `>=` stable v0.6.0 is true, including
  prereleases that meet that comparison. If neither comparison is true,
  or a comparison fails, report unknown, not supported by default.
  Unknown versions may operate only when all
  required identity, capability, state and safety checks pass. This change
  adds no version-only hardware block; no version status bypasses these
  checks, and no capability failure is evidence of version incompatibility.
- **FR-008**: Diagnostics for association, discovery, operations, and feedback
  MUST NOT disclose PIN values, raw state or event dumps, raw service payloads,
  or exceptions that may echo credentials. Preserve useful sanitized context,
  and distinguish unavailable data from diagnostic redaction.
- **FR-009**: An association change MUST NOT split accounting for entries
  sharing one lock, merge different locks with colliding aliases, or orphan or
  duplicate reservation ownership, observed-code aliases, adoption/conflict
  accounting, or reissue and release holds. Preserve public lockname-plus-slot
  and supported Rental Control sensor targeting (`force_reissue`,
  `set_slot_exclusion`) for unambiguous calls, including dry runs. A legacy
  selector is not authorization to touch an unverified Keymaster target.
- **FR-010**: Uncertainty MUST NOT shorten, erase, rotate or reassign an active
  guest's credential or release its reservation or code ownership. Preserve
  current initial cross-entry adoption/recovery holds and post-adoption
  entry-wide issuance holds where applicable. Keep the existing opt-in
  exclusion remedy for a verified association and eligible unreadable managed
  slot without bypassing identity, operation, ownership or clear-confirmation
  fences. Do not widen exclusion eligibility or narrow blocking to only affected
  slots by default.
- **FR-011**: Pending clears and code-release holds MUST stay bound to the
  verified Keymaster lock identity and exact slot for which they originated.
  The same requirement applies to inherited legacy holds whose original identity
  must be established before release. Reselection, address reuse, a missing
  observation or clear evidence from another lock or slot MUST NOT release a hold
  or move cleanup onto the replacement. Preserve outstanding ownership until
  existing release conditions are satisfied with trustworthy current evidence
  from the original lock and slot; if that evidence cannot be established,
  retain the hold and give the operator a safe repair path. An unconfirmed
  clear keeps the pending-clear fence and prevents slot reassignment. Confirming
  the clear resolves that slot fence, not the shared allocator's separate
  PIN reservation. Preserve current release guards: relevant allocation owners
  and forced-reissue holds still reserve the PIN; applicable adoption conflicts,
  missing or unreadable observations for the owning lock and slot, or a PIN
  still present in the relevant observation prevent guarded release. Preserve
  the existing limited reissue exemptions, and the rule that a redundant
  owner can be retired if another owner still represents the same verified
  slot and the PIN remains reserved. A PIN becomes eligible for reuse only
  when it is no longer reserved under those rules and existing issuance and
  collision checks pass.
- **FR-012**: Where the baseline text contract is verified, use the existing
  empty text representations: after trimming whitespace and ignoring case,
  the empty string, `unknown`, and `none`. Apply that rule only to a present
  state record for each verified, enabled entity on the currently loaded
  Keymaster platform. Native Python `None` from an available text entity
  appears in HA as the string `unknown`; no state record at all is unreadable,
  not empty. Reject unavailable states, HA states explicitly marked restored,
  observations invalidated by an association or lifecycle change, synthetic
  masked values, and still-populated values. A normal reset can confirm
  once both current text states meet that rule after dispatch; a pending clear
  can recover on fresh same-slot observation without replaying the reset event.
  Do not equate service completion, a reset event, a disabled slot switch, or
  local text confirmation with proof of removal from physical hardware.

### Key Entities

- **Rental Control entry**: A calendar with an optional Keymaster lock assignment,
  a managed slot range, and reservation and reconciliation state.
- **Keymaster entry**: The configured identity of a Keymaster lock, including any
  supported relationship to a parent entry.
- **Slot capability**: A slot property such as PIN, enabled, reset, or checkout
  limit, identified by its Keymaster entry and verified stable entity identity,
  with a current address and independent availability.
- **Compatibility diagnosis**: What is known about the installed Keymaster
  version, separate from the ability to use a selected lock or slot capability.
- **Pending clear or release hold**: Outstanding work or ownership that protects
  an original lock and slot until the required current evidence allows release.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The complete read, issue, date-update, and clear lifecycle passes
  for default IDs, area-associated/generated IDs, and manually renamed IDs; no
  operation changes or accepts evidence from a distractor outside its intended
  lock and slot range.
- **SC-002**: Each delayed-start, restart, live-rename and reload scenario
  recovers without deleting or re-creating an entry once its verified controls
  are ready. Each accepted feedback change is handled once; stale and unrelated
  events cause zero reservation or slot changes.
- **SC-003**: The legacy upgrade matrix retains all uniquely verified
  assignments and reservation state, keeps lockless behavior unchanged, and
  produces zero automatic reassignments for missing or ambiguous selections.
  Shared-lock and parent/child cases pass without cross-range effects.
- **SC-004**: Every negative test case with insufficient evidence preserves the
  applicable issuance, adoption and reuse fences. No missing capability is
  mislabeled a version incompatibility, and a diagnostic capture of all acceptance
  scenarios contains zero copies of the synthetic test PINs.
  The version matrix reports the expected status for both baseline packages,
  the development placeholder, unknown metadata, a verified below-floor
  release, and prereleases on both sides of the floor, with zero operations
  proceeding on failed safety checks.
- **SC-005**: Across uncertainty, restart, repair and reselection tests, zero
  active credentials are altered due to uncertainty, zero outstanding clears
  are confirmed by the wrong lock, and zero previously held codes are reassigned
  without satisfying the existing release conditions. Eligible exclusion tests
  reproduce the existing gate-only exception, without enabling unsafe operations.

## Scope, Assumptions & Evidence

- **Scope**: Change identity resolution, routing and diagnosis, not calendar
  scheduling, code generation, reservation ownership policy, or how Keymaster
  communicates with physical locks. Clear confirmation means Keymaster reports
  the verified slot empty, not that the physical lock erased the PIN. This
  feature neither adds hardware readback nor changes the existing PIN release
  or reuse rules; local clear evidence is not by itself authorization to reuse
  a PIN. The relevant operation fails closed while
  required capabilities cannot be verified or are unavailable, and normal
  eligibility resumes after valid live evidence returns. Unsupported identity
  formats and indistinguishable events are not routed by guessing. Validate
  on simulated or disposable test fixtures, not production locks.
- **Issue evidence**: The reporter saw failures with area-assigned devices and
  success after recreating entries without areas on Home Assistant 2026.11.0,
  Rental Control 4.0.2 and Keymaster v0.6.5. Recreation changed more than area
  assignment; the cause of the reported area behavior has not been reproduced.
  Home Assistant 2026.11 alone is not established as causal. The current Rental
  Control reset check tests for one constructed entity ID, not a version.
- **Legacy association**: The Rental Control field named `keymaster_entry_id`
  is misleading: the config flow takes a title and stores the Keymaster
  `lockname` (normally the slug, not an entry ID; legacy values are normalized
  to a slug at use). Runtime currently finds the first normalized match when
  discovering the parent. Duplicate titles may already have hidden a wrong
  selection; similar raw names may normalize to the same slug. A present-day
  match is not proof of continuity if the originally selected lock has been
  removed or replaced. Absence of legacy identity evidence is not permission
  to infer identity from a device area, a loaded entity, or an active PIN.
- **Coupled accounting**: Today's allocator persists owner `lockname` and
  slot, uses lock names and ranges in adoption/recovery observations and
  observed-code aliases, and uses lock/slot equivalence to decide whether
  a credential still has an owner or can be released. The public services
  also accept `lockname` selectors; RC currently filters keypad events by
  normalized parent/child lock names. The verified lookup contract does not
  prescribe replacing all of those uses with a new stored field. Planning must
  establish how legacy owners, exclusions and pending work will remain
  attributable before changing any equality rule, serialization or selector.
- **Compatibility baseline**: The maintainer set v0.6.0 as the release support
  floor with the explicit v0.0.0 development exception; v0.6.5 is the reported
  configuration. Verified both shipped release archives
  ([v0.6.0](https://github.com/FutureTense/keymaster/releases/tag/v0.6.0),
  [v0.6.5](https://github.com/FutureTense/keymaster/releases/tag/v0.6.5)).
  The packaged releases and their tagged entity source
  ([v0.6.0](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/entity.py),
  [v0.6.5](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/entity.py))
  construct the unique ID as `<Keymaster config entry ID>_<slugified property key>`.
  The property `text.code_slots:<slot>.pin` is an input to that calculation,
  not the unique ID. `button.code_slots:<slot>.reset` and `button.reset_lock`
  are distinct properties for different operations. Use the actual installed
  manifest version exposed by Home Assistant's loaded integration metadata:
  both release archives report their respective release versions; both tagged
  source manifests say `v0.0.0`. Rental Control's current `v0.1.0-b0` warning
  checks only for the old reset address and logs/posts a notification; it
  neither reads a version nor imposes a version-based operation block.
- **Event and state evidence**: The shipped `keymaster_lock_state_changed`
  payloads ([v0.6.0](https://github.com/FutureTense/keymaster/blob/v0.6.0/custom_components/keymaster/coordinator.py),
  [v0.6.5](https://github.com/FutureTense/keymaster/blob/v0.6.5/custom_components/keymaster/bus_events.py))
  both include `entity_id` (the underlying lock entity), `lockname`, `state`,
  `code_slot_num`, `code_slot_name`, `notification_source`, `action_code` and
  `action_text`. It does not carry a Keymaster config entry ID or PIN; slot
  names and provider metadata are not identity credentials. Both baselines
  use the same field shape for lock and unlock events and an empty slot name
  when absent. A locked event is not a check-in trigger, even if it includes
  a slot, and an unlock's display name is not routing evidence. The separate
  `keymaster_code_slot_reset` event carries the lock entity ID and slot,
  not evidence that the slot has cleared. Keymaster may mark inherited child
  controls unavailable and ignore local edits unless that slot overrides
  the parent. A child unlock is attributable to a parent booking only
  with verified topology and same-slot inheritance.
- **Clear and startup evidence**: In both shipped archives, text entities
  start unavailable. Their coordinator update marks them available only
  for a connected lock with an existing applicable slot, after evaluating
  child inheritance, and publishes the slot's native value. The initially
  missing or unavailable entity is therefore distinct from the normal live
  publication for an empty slot. On HA 2026.11, unavailable serializes as
  `unavailable`, and available with native `None` serializes as `unknown`.
  HA 2026.11's entity registry writes an `unavailable` placeholder marked
  `restored: true` before live setup. Require current loaded-platform state,
  with verified registry identity and no restored flag. After a reload
  or association change, revalidate and observe the current entities rather
  than carrying forward earlier evidence. Do not require a text transition
  for an already-empty live slot, and do not infer hidden restoration from
  the string `unknown` alone.
- **What clear confirmation means**: Current Rental Control startup waits
  for a present, non-`unavailable` state; the existing clear and pending-clear
  paths require two state records and use the empty-text helper. The helper
  also normalizes raw Python `None` as empty for callers with a value, but
  an absent HA state lookup does not satisfy those checks. FR-012 preserves
  literal `none` as an existing compatibility token, not a value the baseline
  archives are known to publish for an empty slot. Blank and whitespace-only
  strings are also synthetic compatibility fixtures, not baseline reset
  output. The helper cannot distinguish
  that token from a live slot name spelled `None`; the synthetic test documents
  the existing interpretation when the PIN is also empty, without broadening
  it to other tokens or granting new release authority. FR-012 explicitly
  rejects `restored: true`; existing
  code rejects HA's normal unavailable registry placeholder already, but
  does not inspect that flag if presented with an otherwise empty-looking
  state. The new identity and lifecycle checks also reject cached observations
  invalidated by setup or association changes. The shipped reset routines attempt provider
  clearing but do not use its return as a condition for replacing the local
  slot with empty name/PIN or emitting the reset event. The same available
  `unknown` pair can therefore follow a failed provider clear. It confirms
  only the local Keymaster observation under the existing policy; neither
  this feature nor those release archives establish hardware erasure.
  Keymaster's `[REDACTED]` text helper masks log output, not the live text
  publication in these baselines. Masked live test values are synthetic
  negative fixtures, not expected baseline output.
- **Version comparison evidence**: HA 2026.11 loads `AwesomeVersion` from
  the installed manifest and pins `awesomeversion` 25.8.0. Probes confirmed
  the optional `v` on the unsuffixed zero, below-floor ordering for
  `v0.6.0-rc.1`, above-floor ordering for `v0.6.1rc1`, and that
  `<`, `>=`, and `==` are all false for `v0.6.0+local` against the stable
  floor. Under FR-007 that case is unknown. The published Keymaster tags from
  v0.6.0 through v0.6.5 all use three numeric components and parse as SemVer.
  Historical tags such as v0.1.1-b3 also parse as SemVer and compare below
  the floor. The historical release tag `v.0.0.71` is not a loaded version
  measurement: its tagged source manifest declares `v0.0.0`, and its
  packaged manifest version was not verified. The fixture tests what happens
  if the literal tag spelling appears as loaded metadata, not what that
  archive actually reports. The comparator accepts the SemVer and PEP 440 prerelease
  spellings used in the acceptance cases. It can also recognize bare builds,
  two-component versions and calendar labels such as `2026.11`, so recognition
  by itself is not evidence of a Keymaster release version.
- **Deferred to planning**: Verify the supported Keymaster identity variants
  within the agreed compatibility policy and confirm migration, live-empty
  and event evidence for each supported topology. Choose how to store the
  association, preserve the ledger, subscribe and rebind, and surface targeted
  remediation without changing the required fail-closed behavior. If evidence
  cannot support an existing behavior without a policy change, obtain
  maintainer direction rather than guessing.

No forced storage migration, ban on corroborating event names, or switch to
slot-scoped blocking is prescribed by this draft.
