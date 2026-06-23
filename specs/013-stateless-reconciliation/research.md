<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Stateless Slot Reconciliation

## R-001: Stateless Reconciliation Engine

**Decision**: Rework the existing pure planner in
`custom_components/rental_control/reconciliation.py` into a stateless engine
that accepts only:

1. `ObservedSlot` records read from physical Keymaster entities for the current
   refresh;
2. `DesiredReservation` records derived from the current calendar and current
   check-in tracking state; and
3. immutable runtime configuration such as managed slot range, prefix, trimming,
   buffers, and code-generation options.

The engine returns a per-slot action plan containing `noop`,
`update_in_place`, `reset`, `assign`, and `blocked` actions. The coordinator
continues to call the planner from `_async_update_data()` after calendar fetch
and before publishing coordinator data, because that method already owns the
refresh cycle and currently wires observation, reservation building,
`compute_desired_plan()`, apply, Store sync, and save at
`coordinator.py:2053-2173`.

**Rationale**:

- The live refresh cycle already has the correct orchestration point: it fetches
  current calendar data (`coordinator.py:2062-2077`), applies calendar miss
  tolerance (`coordinator.py:2078-2103`), observes managed slots
  (`coordinator.py:2122`, `coordinator.py:2128`), builds reservations
  (`coordinator.py:2124`), invokes `compute_desired_plan()`
  (`coordinator.py:2131-2141`), applies actions (`coordinator.py:2150-2152`),
  and saves Store data (`coordinator.py:2173`).
- Physical observation is already isolated in `_observe_managed_slots()`, which
  reads Keymaster name/PIN text entities and date/switch state for each managed
  slot (`coordinator.py:1735-1958`). The stateless engine keeps that physical
  read as an input but removes persisted status from classification.
- The current planner module is already pure enough to test in isolation: it
  defines `Reservation`, `ManagedSlot`, `SlotAction`, `DesiredPlan`, and
  `compute_desired_plan()` (`reconciliation.py:136-427`,
  `reconciliation.py:1638-1831`). Reworking it avoids introducing new
  dependencies.
- The current Store/mapping path is not stateless: `_build_reservations()`
  consults `_slot_mappings` for fingerprint history and missing counts
  (`coordinator.py:1118-1152`), rematches current reservations to persisted
  mappings (`coordinator.py:1290-1342`), synthesizes ghost reservations from
  Store records (`coordinator.py:1422-1430`), and then syncs Store as an
  authoritative mapping after apply (`coordinator.py:1596-1733`). That path is
  the core plan-vs-reality drift to remove.

**Replaces / deletes**:

- Delete persisted status as an input to slot classification. `_observe_managed_slots()`
  currently imports pending-clear fences from `event_overrides.pending_clear_slots`
  and persisted status at `coordinator.py:1753-1769` and changes classification
  based on Store state at `coordinator.py:1870-1918`; the stateless design uses
  only observed physical state for free/occupied/unknown/phantom.
- Delete Store-driven rematch and ghost logic from `_build_reservations()`:
  `_remap_observed_mappings_to_physical_reservations()`
  (`coordinator.py:1046-1117`), `find_reservation_rematch()` use
  (`coordinator.py:1290-1296`), mapping re-keying (`coordinator.py:1297-1376`),
  miss-count reset (`coordinator.py:1378-1387`), and ghost reservations
  (`coordinator.py:1422-1595`).
- Delete the persisted-authoritative fields from `EventOverrides.__init__()`:
  `_next_slot`, `_slot_miss_counts`, `_slot_uids`, `_persisted_mappings`,
  `_pending_clear_slots`, and `_pending_fences` (`event_overrides.py:162-177`).
  Keep the async lock (`event_overrides.py:160`), actual-state/diagnostics cache,
  retry tracking, and callback suppression.
- Delete `async_reserve_or_get_slot()` and `_find_overlapping_slot()` as
  production paths (`event_overrides.py:551-785`). The new planner owns global
  matching/allocation instead of per-event greedy reservation.
- Delete adoption as correctness machinery: `async_adopt_keymaster_slots()`
  (`coordinator.py:647-783`) and `_adopt_observed_coded_slots()`
  (`coordinator.py:785-863`) become unnecessary for correctness because a
  deleted Store cold start reads physical slot names directly.

**Alternatives considered**:

- **Patch the existing persisted planner**: Rejected because FR-001 and FR-002
  require physical Keymaster state plus calendar to be the only correctness
  inputs. Persisted pending clears, ghost reservations, and stale mappings would
  remain load-bearing.
- **Keep `EventOverrides` as the assignment owner**: Rejected because its legacy
  `_next_slot` path is greedy (`event_overrides.py:481-532`) and cannot prove
  soonest-N ordering or duplicate collapse across all slots.
- **Run reconciliation from Keymaster callbacks**: Rejected because callbacks are
  noisy and current code intentionally avoids re-entering reconciliation after a
  callback update (`util.py:1047-1055`).

---

## R-002: Stable Slot-Name Identity Matching

**Decision**: Match desired reservations to physical managed slots by stable
slot-name identity before considering code or date equality. A desired
reservation's stable name is the guest/reservation slot name returned by
`get_slot_name(summary, description, event_prefix)` (`util.py:784-847`) before
Keymaster display trimming. Its Keymaster display form is then generated by
applying the configured prefix and `trim_name()` (`util.py:399-439`), matching
how `async_fire_set_code()` writes names at `util.py:478-489`.

A physical slot's name is read from the Keymaster name text entity in
`_observe_managed_slots()` (`coordinator.py:1772-1833`). Matching normalizes
case and surrounding whitespace, strips the configured prefix when present, and
accepts exact full-name, exact display-name, and trim-aware prefix-aware forms.
When duplicate desired names or ambiguous physical names exist, the planner
pairs the name group by start-time order: desired reservations sort by
`(start, original_index)` and observed occupied slots sort by observed
Keymaster start time, then slot number when the physical start is missing. Each
desired reservation and each physical slot may be matched at most once. If a
trimmed display name collides across different full stable names, group counts
do not line up, or a duplicate-name/date-shift group cannot produce one
deterministic total order, the safe result is `blocked` with diagnostics rather
than guessing or allocating an extra slot.

**Rationale**:

- Issue #607 identifies the failure mode: matching by dates or generated code
  treats a changed reservation as absent. The default/date-based generator
  explicitly changes the code when start/end dates shift. The sensor documents
  that side effect at `sensors/calsensor.py:299-304`, and the coordinator
  generator builds the code from start/end day/month/year at
  `coordinator.py:864-889` and falls back to it from `_generate_slot_code()` at
  `coordinator.py:921-946`.
- Slot name survives length increases, length decreases, and full date shifts.
  The existing event-name extraction already normalizes multiple booking-source
  formats while preserving the guest/reservation identifier (`util.py:784-847`).
- Keymaster stores the display name, which may be prefixed and trimmed.
  `async_fire_set_code()` writes `event_prefix + slot_name` or the trimmed
  version when configured (`util.py:478-489`), so the matcher must accept both
  full and Keymaster-display forms.
- The live 012 rematch logic still starts from Store mappings and fingerprints
  that include start/end (`reconciliation.py:608-647`) and then tries UID/name
  and continuity rematches (`reconciliation.py:1056-1296`). A date-shifted
  reservation can therefore still depend on stale persisted history. The 013
  matcher instead treats the physical slot name as the durable identity that is
  re-read every cycle.

**In-place update path**:

- If a physical slot name matches a desired reservation and code/dates/name
  display are already correct, emit `noop` to avoid lock churn (FR-013).
- If the same matched reservation has only date-window drift and the PIN is
  unchanged, emit `update_in_place(update_times)` for the same physical slot,
  reusing the existing `async_fire_update_times()` service helper
  (`util.py:630-687`).
- If the same matched reservation needs a replacement PIN or display name, emit
  `update_in_place(replace_code)` bound to that same slot. The apply path clears
  that slot, verifies it is physically empty, and then sets the same reservation
  back into the same slot during the same apply operation. If clear is not
  confirmed, the set is skipped; the next refresh will still observe the old
  physical name and retry the same in-place update rather than allocate a second
  slot.
- Desired reservations that do not match any physical slot by name are assigned
  only to physical slots already confirmed empty. Assignment uses a fresh
  immediate physical read just before writing, not only the snapshot from plan
  time, so a slot that changed after observation becomes `blocked` instead of
  receiving a PIN.

**Alternatives considered**:

- **Code/date matching**: Rejected because date-based code changes when dates
  shift (`sensors/calsensor.py:299-304`; `coordinator.py:864-889`), recreating
  duplicate slot assignments.
- **Persisted fingerprint primary matching**: Rejected for correctness because
  the v1 fingerprint includes start/end (`reconciliation.py:608-647`), so a
  full date shift requires Store rematch history.
- **UID-primary matching**: Rejected because issue #607 explicitly demotes
  Store/UID aliasing to cache-only, and current UID matching lives in the
  retiring `_slot_uids` structure (`event_overrides.py:166-167`).

---

## R-003: Confirmed Reset Before Reapply

**Decision**: A slot is reusable only when the current physical Keymaster read
confirms both name and PIN are empty, blank, `unknown`, or `None` using the
existing case-insensitive helpers. `unavailable` is unreadable and therefore
conservative: the planner emits `blocked` and tries again on a later refresh.
No persisted pending-clear fence participates in this decision.

**Rationale**:

- `is_cleared_keymaster_text_state()` treats `None`, blank strings, and
  casefolded `unknown` as cleared (`util.py:64-79`), while
  `is_unreadable_keymaster_text_state()` treats casefolded `unavailable` as
  unreadable (`util.py:82-84`). The physical observation path already uses those
  helpers to compute `physically_empty` and `unreadable` at
  `coordinator.py:1798-1805`.
- `async_fire_clear_code()` already performs bounded physical confirmation. It
  presses Keymaster reset (`util.py:299-306`), waits briefly for propagation
  (`util.py:326-327`), force-clears lingering name when needed
  (`util.py:335-369`), checks the PIN text state (`util.py:370-376`), and
  returns an `OperationResult` that distinguishes confirmed, unconfirmed,
  failed, lingering name, and lingering PIN (`util.py:378-396`).
- `async_fire_set_code()` already uses a bounded name confirmation wait after a
  set (`util.py:611-627`), implemented by `_async_wait_for_expected_name()` at
  `util.py:213-243`. The redesign reuses bounded waits around individual
  service calls and does not block the whole refresh loop waiting for unrelated
  slots. Before any destructive clear or assign, the apply path also performs
  an immediate physical re-read of the target slot. A clear proceeds only when
  the current physical name still matches the expected reservation or reset
  reason; a set proceeds only when the current physical name and PIN are still
  confirmed empty.
- The current persisted fence path can wedge correctness: `_apply_clear()` marks
  `_pending_clear_slots` before the reset (`event_overrides.py:954-972`), and
  `_observe_managed_slots()` then imports those fences into future slot status
  (`coordinator.py:1753-1755`, `coordinator.py:1887-1904`). In the stateless
  model, a slot pending physical clear is simply observed as still occupied if
  the name/PIN remain present, or as free when both are physically empty.

**Alternatives considered**:

- **Persist pending-clear fences across cycles**: Rejected because FR-002 says
  stale pending-clear data must not affect assignment safety once the slot is
  physically empty.
- **Assume reset service success means free**: Rejected because Keymaster can
  report lingering name or PIN after reset (`util.py:340-376`), and FR-010
  requires physical confirmation.
- **Treat `unavailable` as empty**: Rejected because `unavailable` is explicitly
  unreadable (`util.py:82-84`) and FR-012 requires conservative handling.

---

## R-004: Cache-Only Store and Migration

**Decision**: Keep the HA Store key and schema only as cache/diagnostics. The
Store may retain UID aliases, booking aliases, stable-name alias history,
redacted last plan diagnostics, and migration breadcrumbs. It must not contain
or drive occupied/pending/blocked status used by selection, matching, reset,
assignment, or duplicate prevention. Missing, stale, corrupt, delayed, or
deleted Store data must produce the same physical actions as an empty cache.

**Rationale**:

- The current Store is loaded during setup (`__init__.py:84-99`) and by
  `async_load_slot_store()` (`coordinator.py:559-585`), then injected into
  `EventOverrides.load_persisted_mappings()` (`event_overrides.py:409-445`).
  That makes persisted mappings and pending clears influence runtime behavior.
- Current Store saves authoritative statuses and pending operation fields at
  `coordinator.py:591-621` and `_sync_slot_store_from_plan()`
  (`coordinator.py:1596-1733`). These fields directly recreate the wedging
  risks called out in issue #607.
- Physical state already contains enough durable identity for correctness: the
  Keymaster slot name is read every cycle (`coordinator.py:1772-1833`) and the
  desired stable name comes from the current calendar (`util.py:784-847`).
- Raw PINs are already excluded from persisted snapshots (`coordinator.py:579-584`,
  `coordinator.py:600-610`, `reconciliation.py:462-491`), and that boundary is
  kept.

**Migration**:

- Existing 3.5.x Store files are not wiped. On upgrade, load may parse them for
  non-authoritative alias/diagnostic cache entries, but `status`, `slot`,
  `pending_clear`, `operation_id`, `missing_count`, and `blocked_slots` are
  ignored for correctness.
- No working Keymaster slot is cleared merely because Store is missing, stale,
  corrupt, or contradictory. Existing coded slots are recognized by the
  physical name matcher on the first readable refresh.
- If the Store is deleted mid-run, the next refresh still computes from
  physical slots plus calendar. The only lost information is optional alias or
  diagnostic history.
- Cache writes should occur after a refresh for diagnostics/aliases only and
  must be safe to skip if Store saving fails.

**Alternatives considered**:

- **Persist authoritative slot mappings with better recovery**: Rejected by
  FR-002 and issue #607; recovery bugs are caused by making Store load-bearing.
- **Delete Store entirely**: Rejected because non-sensitive alias and diagnostic
  history can still help support and migration, provided tests prove deleting it
  does not change behavior.
- **Wipe Store and all slots on upgrade**: Rejected because issue #607 requires
  no wipe of working slots on upgrade, and FR-001 makes physical slots the truth.

---

## R-005: Soonest-N, Active Protection, Buffers, and Overrides

**Decision**: Desired reservations are derived from the same parsed calendar
semantics as today, then selected as the soonest eligible whole-unit,
non-overlapping reservations up to managed-slot capacity, with active checked-in
guests selected first and counted against capacity. The stateless planner
preserves lock-code buffers, Honor Event Times, manual time-of-day overrides,
manual door-code overrides, name trimming, deterministic code generation,
`should_update_code`, and check-in tracking.

**Rationale**:

- The coordinator already stores the relevant options: check-in/out time,
  managed range, max events, code generator, `should_update_code`, Honor Event
  Times, trimming, max name length, and before/after buffers at
  `coordinator.py:170-210`.
- Calendar parsing already preserves Honor Event Times and manual override
  fallback: explicit event times win when enabled (`coordinator.py:2418-2427`),
  description times and existing override times are fallback sources
  (`coordinator.py:2427-2457`), and disabled Honor Event Times falls back to
  stored/manual override or configured defaults (`coordinator.py:2458-2479`).
- Buffers are applied by the shared `apply_buffer()` helper
  (`util.py:442-463`) and are already used by reservation construction
  (`coordinator.py:1250-1260`) and physical set/update helpers
  (`util.py:530-537`, `util.py:648-655`).
- Active-guest state is exposed by the check-in tracking sensor attributes
  (`sensors/checkinsensor.py:276-300`) and is currently applied to reservation
  objects at `coordinator.py:1960-1999`. The stateless planner keeps that input
  but does not use Store missing counts for protection.
- The current pure planner already demonstrates the desired selection shape:
  protected reservations are selected first and remaining capacity is filled by
  non-protected reservations sorted by `(start, identity_key)` at
  `reconciliation.py:1399-1450`. The 013 implementation must replace the
  identity-key tie breaker with duplicate-name start-order pairing where names
  collide, but keep the soonest-N invariant.
- Manual door-code overrides are preserved by treating observed Keymaster PINs
  as in-memory physical facts for matched-name slots. The raw PIN is never
  persisted or logged. If the observed PIN matches the deterministic/generated
  code for the old observed reservation dates, a date shift with
  `should_update_code` regenerates the expected date-based code; otherwise the
  observed PIN is treated as a manual override and carried into the desired
  reservation. This preserves FR-015 while still satisfying date-shift code
  replacement for generated codes.
- Manual time-of-day overrides are preserved without Store authority by reading
  the matched physical slot date range and reversing configured buffers back to
  the reservation access window when Honor Event Times is not taking explicit
  PMS event times. This replaces the existing dependency on
  `event_overrides.get_slot_with_name()` in the parser fallback path
  (`coordinator.py:2414-2468`) with physical-state-derived override input.
- `handle_state_change()` continues to update in-memory slot feedback from
  Keymaster state changes (`util.py:850-1053`), including preserving untrimmed
  names when a trimmed Keymaster value matches the expected display form
  (`util.py:1021-1046`). It must remain non-authoritative and must not launch
  reconciliation (`util.py:1054-1055`). Because
  `update_event_overrides()` currently requests a refresh by default
  (`coordinator.py:2334-2358`), the implementation must replace this callback
  update with a non-refreshing observation-cache update or call it with
  `request_refresh=False`.

**Alternatives considered**:

- **Strict soonest-N without active protection**: Rejected because FR-016
  requires active guests not be evicted mid-stay.
- **Ignore observed PINs and always regenerate**: Rejected because FR-015
  requires manual code overrides to be preserved.
- **Keep Store miss-count ghost reservations**: Rejected because ghost
  reservations are Store-authoritative (`coordinator.py:1422-1595`) and conflict
  with FR-001/FR-002. Calendar fetch miss tolerance (`coordinator.py:2078-2103`)
  can remain because it governs the calendar input itself, not slot mappings.

---

## R-006: `event_N` Sensors

**Decision**: `event_N` sensors remain read-only reflections of the latest
calendar event and reconciled plan. They expose the same event summary, start,
end, parsed attributes, `slot_name`, `slot_number`, and `slot_code` attributes,
but do not reserve slots or call Keymaster services.

**Rationale**:

- The live `RentalControlCalSensor` already reads slot assignment and code from
  coordinator reconciliation state at `sensors/calsensor.py:416-437`, but it
  currently looks up date-bearing fingerprints (`sensors/calsensor.py:424-431`).
  The stateless plan must expose a compatibility lookup from the current event
  fingerprint/UID to the refresh-local `DesiredReservation.desired_id` so sensor
  attributes survive date shifts and the internal identity change remains
  invisible to users.
- The historical side-effect method `_async_handle_slot_assignment()` is now a
  no-op shim that documents the retired per-event mutation path
  (`sensors/calsensor.py:505-537`). The stateless redesign keeps that
  direction and removes any remaining dependency on `async_reserve_or_get_slot()`.
- Preserving `event_N` as read-only satisfies FR-017 while letting the
  coordinator enforce global invariants across all slots.

**Alternatives considered**:

- **Reintroduce per-sensor assignment for empty slots**: Rejected because it
  splits global soonest-N and duplicate prevention across multiple entities.
- **Remove slot attributes from sensors**: Rejected because existing users rely
  on the display behavior, and FR-017 requires preservation.

---

## R-007: Concurrency and Refresh Behavior

**Decision**: Keep one async lock around reconciliation apply operations and
Keymaster callback feedback. The coordinator computes and applies one atomic
plan per refresh; Keymaster state-change callbacks update in-memory observation
feedback and suppress coordinator-originated echoes, but never trigger a nested
reconciliation. Entity-readiness timing is no longer correctness-critical:
`unavailable` slots are blocked for one cycle and re-evaluated on the next
refresh.

**Rationale**:

- `EventOverrides` already has the lock used to serialize mutable slot state
  (`event_overrides.py:160`), and `async_apply_plan()` marks reconciliation
  active while applying actions (`event_overrides.py:866-952`). The design keeps
  the single-lock pattern while deleting persisted authoritative fields.
- Set/overwrite paths already suppress coordinator-originated callback echoes
  (`event_overrides.py:1049-1063`, `event_overrides.py:1229-1243`), and
  callbacks check `should_suppress_state_change()` before updating local state
  (`util.py:896-904`). Keep that feedback loop to avoid re-entrancy storms.
- `handle_state_change()` explicitly does not launch reconciliation after
  callback updates (`util.py:1047-1055`). Stateless correctness depends on the
  next scheduled/manual refresh rather than immediate callback-driven planning.
- The current startup-readability watcher exists because early unreadable
  Keymaster entities used to interact with Store/adoption timing. It watches
  name, PIN, and enabled entities (`__init__.py:233-278`), arms a delayed
  refresh when they become readable (`__init__.py:281-424`), and registers the
  normal Keymaster listener at `__init__.py:427-454`. In the stateless design it
  may be simplified or removed because unreadable slots are retried naturally;
  keeping a one-shot refresh is acceptable as an optimization, not correctness.

**Alternatives considered**:

- **Per-slot independent apply tasks**: Rejected because action ordering matters
  for confirmed reset before assignment and for duplicate collapse diagnostics.
- **Nested reconcile from callbacks**: Rejected because Keymaster service calls
  generate state-change storms and current code already suppresses callbacks.
- **Startup blocking until all slots readable**: Rejected because unavailable
  slots are conservative and stateless re-evaluation makes readiness timing
  non-load-bearing.

---

## R-008: Obsolete Machinery to Retire

**Decision**: Remove persisted-authoritative machinery that no longer
contributes to correctness, while retaining service helpers, callback feedback,
retry diagnostics, and the single lock.

**Retire / delete**:

- `_next_slot`, `next_slot`, `__assign_next_slot()`, and greedy slot selection
  (`event_overrides.py:162`, `event_overrides.py:192-204`,
  `event_overrides.py:481-532`). Stateless assignment is global and per-refresh.
- `_slot_uids` as authoritative matching state (`event_overrides.py:166-167`)
  and the UID phases in `_find_overlapping_slot()` (`event_overrides.py:551-716`).
  UID aliases may remain cache-only in Store.
- `_slot_miss_counts` and Store ghost reservations
  (`event_overrides.py:166`, `coordinator.py:1422-1595`). Calendar fetch miss
  tolerance may remain, but slot-mapping miss tolerance is no longer
  correctness input.
- `_pending_clear_slots`, `_pending_fences`, persisted operation IDs, and
  persisted blocked slots as assignment fences (`event_overrides.py:173-177`,
  `event_overrides.py:436-445`, `coordinator.py:1870-1904`). Physical empty
  state is the only reusable proof.
- `load_persisted_mappings()` validation and pending-clear import as runtime
  authority (`event_overrides.py:409-445`). A replacement cache loader must not
  raise because two stale cache records claim the same slot.
- `async_reserve_or_get_slot()` and `async_check_overrides()` production paths
  (`event_overrides.py:718-785`, `event_overrides.py:1509-1659`). The planner
  emits resets/assignments for stale, duplicate, and overflow cases.
- `async_adopt_keymaster_slots()` and `_adopt_observed_coded_slots()` as first
  upgrade/deleted Store correctness machinery (`coordinator.py:647-863`).
  Physical slots are recognized directly by name on every refresh.
- Store re-keying/rematch helpers that make Store authoritative
  (`coordinator.py:996-1117`, `reconciliation.py:1056-1296`).

**Keep / preserve**:

- `async_fire_clear_code()`, `async_fire_set_code()`, and
  `async_fire_update_times()` with bounded confirmation and retry diagnostics
  (`util.py:275-396`, `util.py:466-687`).
- `is_cleared_keymaster_text_state()` and
  `is_unreadable_keymaster_text_state()` for physical empty/readability
  classification (`util.py:76-84`).
- Slot-name extraction/trimming helpers (`util.py:399-439`, `util.py:784-847`).
- A latest-plan slot lookup for check-in unlock validation, replacing direct
  reads from `event_overrides.get_slot_name()` used today at
  `sensors/checkinsensor.py:1621-1641`. The replacement must compare the
  incoming Keymaster slot to the tracked reservation's latest planned/observed
  stable name without depending on deleted override maps.
- The coordinator refresh lock/apply pattern and callback suppression, with
  re-entrancy prohibited (`event_overrides.py:866-952`, `util.py:896-904`,
  `util.py:1047-1055`).
- `event_N` read-only sensor reflection (`sensors/calsensor.py:416-437`) and
  check-in tracking state (`sensors/checkinsensor.py:276-300`).

**Rationale**: Removing the obsolete machinery does not break preserved
features because those features are either derived from physical Keymaster
state each refresh, from the current calendar/config, or from explicit sensor
state. The implementation is large and safety-sensitive; tasks must migrate
unit and integration tests before deleting shims.

**Alternatives considered**:

- **Keep obsolete fields as compatibility shims indefinitely**: Rejected because
  hidden references could accidentally make Store authoritative again.
- **Delete `EventOverrides` wholesale**: Rejected for this stage because its
  lock, retry/error tracking, service-helper adapters, and callback suppression
  remain useful; implementation may rename/split it after tests pin behavior.
