<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Slot Reconciliation

## R-001: Reconciliation Architecture

**Decision**: Run one authoritative reconciliation step from
`RentalControlCoordinator._async_update_data()` after calendar fetch/parse and
calendar-level miss tolerance, but before the refreshed coordinator data is
published to entities. This step replaces the independent per-`event_N` greedy
assignment path and subsumes `EventOverrides.async_check_overrides()` cleanup
policy. `RentalControlCalSensor` remains responsible for presenting event
attributes, but slot number/code attributes are read from the coordinator's
last desired plan instead of mutating Keymaster.

**Rationale**:

- The coordinator already owns the refresh cycle and has the fresh `new_calendar`
  list before `DataUpdateCoordinator` publishes it. Today it calls
  `async_check_overrides()` at `coordinator.py:522-525`, so this is the narrowest
  point to replace cleanup-only checking with full desired-vs-actual
  reconciliation.
- Current sensors schedule side-effectful slot assignment from
  `_handle_coordinator_update()` (`calsensor.py:470-489`) and then call
  `async_reserve_or_get_slot()` plus Keymaster services in
  `_async_handle_slot_assignment()` (`calsensor.py:521-607`). That means each
  sensor reasons about only one event and cannot prove global invariants such as
  soonest-N, duplicate collapse, or active-guest protection.
- Current `EventOverrides` still exposes incremental state (`_next_slot`,
  `_slot_uids`, and `_slot_miss_counts` at `event_overrides.py:113-122`) and a
  next-free algorithm (`event_overrides.py:182-218`). Those are useful
  implementation details for historical behavior but are not an authoritative
  plan when all slots are full or corrupted.
- Issue #589 and #535 both report that a nearer reservation can be visible in
  Rental Control but not programmed into Keymaster until restart/reload. Issue
  #546 reports a farther-future reservation displacing a nearer upcoming one.
  These failures require a refresh-level comparison of all eligible reservations
  and all managed slots, not a first-available slot reservation by each sensor.

**Implementation shape**:

- Add an internal `reconciliation.py` module with typed, testable helpers for
  building `Reservation` objects, selecting protected and soonest-N desired
  reservations, assigning deterministic slot indexes, and producing an
  apply-diff.
- Keep `EventOverrides` as the lock-protected owner of actual slot state,
  physical operation state, and diagnostics. Replace public reliance on
  `next_slot`, `async_reserve_or_get_slot()`, and `async_check_overrides()` with
  an `async_reconcile(coordinator, current_events)` method and smaller helpers.
- Convert `RentalControlCalSensor._handle_coordinator_update()` into a read-only
  reflection of the coordinator's latest `DesiredPlan`: it still computes and
  exposes summary, start/end, ETA, parsed attributes, slot name, and slot code,
  but it does not schedule set/clear/update service calls.
- Preserve `async_setup_keymaster_overrides()` as actual-state bootstrap, but it
  should populate observed slots and Store migration inputs rather than becoming
  the source of truth.

**Alternatives considered**:

- **Keep per-sensor assignment and improve overflow logic**: Rejected because it
  still lets multiple sensors make local decisions and cannot prove that no
  farther reservation remains assigned while a nearer one is unassigned.
- **Run reconciliation from `handle_state_change()`**: Rejected because
  Keymaster service calls generate state-change storms. Current code already
  sleeps to let storms settle (`util.py:691-693`) and then calls cleanup again
  (`util.py:809-817`), which risks re-entrancy loops.
- **Make Keymaster actual state the only truth**: Rejected because issue #521
  shows Keymaster can contain phantom name-only state that must be treated as
  corrupt actual state, not as desired state.

---

## R-002: Reservation Identity Key

**Decision**: Persist a derived stable fingerprint as the primary reservation
identity and store volatile calendar UID as a secondary alias only. The primary
fingerprint is versioned and built from normalized stable characteristics:
normalized unprefixed guest/slot name, UTC reservation start, UTC reservation
end, and the configured calendar/entry scope. Optional extracted booking IDs or
platform confirmation fields are stored as additional aliases when present.

**Rationale**:

- The spec requires identity to survive booking-platform calendar identifier
  churn (FR-010 and FR-011). Issue #546 identifies Guesty Lite as a platform in
  use, and owner guidance says Guesty Lite/Airbnb can reissue UIDs.
- Current code normalizes and stores UIDs only in memory (`_slot_uids` at
  `event_overrides.py:120-121` and UID matching at `event_overrides.py:279-334`),
  but Keymaster does not persist UIDs and they are lost on restart.
- Current check-in tracking uses `summary|start` as its identity key
  (`checkinsensor.py:324-340`), deliberately excluding end time for that state
  machine. Slot reconciliation needs a stronger key because two same-named stays
  at different dates must not collapse into one managed slot.
- The existing parser produces sorted `CalendarEvent` objects with normalized UID
  at `coordinator.py:887-895`, while `get_slot_name()` extraction is already
  shared by sensors and check-in tracking (`calsensor.py:412-417`,
  `checkinsensor.py:434-449`). Reusing the same untrimmed slot-name extraction
  preserves current user-facing naming semantics.

**Matching rules**:

1. Exact primary fingerprint match keeps the existing slot mapping.
2. If the UID alias matches and the normalized guest name matches, update the
   persisted fingerprint in place when start/end changed, because this is a
   likely date shift for the same reservation.
3. If an optional booking/confirmation alias matches and guest name matches,
   treat it as the same reservation even if UID changed.
4. If no alias matches, fall back to normalized guest name plus exact start/end.
5. If both UID and dates changed, try one conservative continuity rematch:
   compare the current reservation to persisted fingerprint history, the last
   observed actual slot, booking aliases, normalized name, and the
   non-overlap/whole-unit ordering. Rematch only when exactly one stored mapping
   is compatible and no current reservation or persisted mapping competes for
   it. Ambiguous candidates are treated as new/overflow diagnostics rather than
   collapsed.
6. Equal start times, although outside the normal whole-unit assumption, sort by
   identity key for deterministic tie-breaking without overlap policy.

**Alternatives considered**:

- **UID-primary identity**: Rejected because UID churn would recreate the #589
  and #546 failure family after refreshes or restarts.
- **Name-only identity**: Rejected because repeat guests and back-to-back stays
  could be collapsed incorrectly.
- **Summary + start only**: Rejected for slot mapping because end-date changes
  and same-start corrections must be distinguishable from check-in tracking's
  narrower needs.

---

## R-003: HA Store Persistence and Migration

**Decision**: Add a coordinator-owned `Store` with schema version 1 and a storage
key scoped by integration entry ID, e.g. `rental_control.slot_mappings.<entry_id>`.
Load it during `async_setup_entry()` after coordinator construction and before
`async_setup_keymaster_overrides()` and the first refresh. Save only when the
mapping, pending-clear state, aliases, or feed-miss counters change.

**Schema outline**:

```json
{
  "schema_version": 1,
  "entry_id": "...",
  "lockname": "front_door",
  "start_slot": 10,
  "max_slots": 5,
  "updated_at": "2026-06-19T22:33:30Z",
  "mappings": {
    "<identity_key>": {
      "slot": 10,
      "status": "occupied",
      "operation_id": null,
      "operation_kind": null,
      "identity": {
        "version": 1,
        "normalized_slot_name": "Jane Guest",
        "start": "2026-06-20T23:00:00+00:00",
        "end": "2026-06-23T18:00:00+00:00",
        "uid_aliases": ["..."],
        "booking_aliases": []
      },
      "missing_count": 0,
      "pending_set_since": null,
      "pending_clear_since": null,
      "last_observed_actual": {
        "slot_name": "Jane Guest",
        "has_code": true,
        "start": "2026-06-20T23:00:00+00:00",
        "end": "2026-06-23T18:00:00+00:00"
      }
    }
  },
  "blocked_slots": {
    "11": {
      "reason": "pending_clear",
      "previous_identity_key": "...",
      "since": "2026-06-19T22:33:30Z",
      "retry_count": 1
    }
  }
}
```

Do not persist raw access codes. Persist `has_code` and redacted/hash metadata
only if needed for diagnostics. The current code already reads code values from
Keymaster text entities during bootstrap (`coordinator.py:305-324`), and current
events can regenerate desired codes through existing generators.

**Migration from no stored state**:

- Treat missing Store data as schema version 0.
- Bootstrap observed RC-managed Keymaster slots exactly as today, including
  partial-reset detection for name-only slots (`coordinator.py:330-356`).
- On the first fresh calendar reconciliation, match observed populated slots to
  current reservations by stable fingerprint, alias, conservative continuity,
  and then name+time. Matching must normalize Keymaster display names with the
  existing event prefix and trim rules, and compare Keymaster buffered date
  ranges by reversing the configured before/after buffers against the same PMS
  time rules that built the reservation. Matched working slots are adopted into
  Store without clearing or reprogramming.
- Observed slots that do not match the desired plan become stale/manual/phantom
  actual state and are reconciled through confirmed clear. Ambiguous populated
  slots are blocked and diagnosed until a later refresh can disambiguate or an
  operator intervenes; they are not wiped blindly during migration.
- If Store scope no longer matches `lockname`, `start_slot`, or `max_slots`, keep
  reusable mappings that still fall inside the managed range and mark others as
  unmanaged/overflow diagnostics rather than touching slots outside the current
  RC-managed range.

**Alternatives considered**:

- **Keep runtime-only `_slot_uids` and `_slot_miss_counts`**: Rejected because
  restart survival and feed-miss tolerance are explicit requirements.
- **Persist Keymaster actual state only**: Rejected because actual state may be
  phantom or manually edited, and the desired plan must be authoritative.
- **Clear all slots on first upgrade**: Rejected because it could remove working
  codes for arriving or checked-in guests and contradicts the self-healing
  no-restart/no-clear-all requirement.

---

## R-004: Desired-Plan Computation

**Decision**: Compute a deterministic `DesiredPlan` from current eligible
reservations, protected checked-in reservations, persisted mappings, pending
clear blocks, and actual Keymaster state. Selection is by earliest start time,
with protected active guests pinned first and counted against capacity.

**Algorithm**:

1. Build `Reservation` records from the fresh sorted `new_calendar` returned by
   `_ical_parser()` (`coordinator.py:853-854`). Preserve existing parser filters
   for old events, far-future events, blocked/unavailable entries, PMS times, and
   event prefix handling (`coordinator.py:727-748`, `coordinator.py:766-845`).
2. Exclude reservations that existing check-in tracking considers checked out.
   Protect the existing `checked_in` reservation while its tracked end time is in
   the active stay window. The sensor already persists and exposes checked-in
   state and tracked event fields (`checkinsensor.py:219-230`,
   `checkinsensor.py:276-300`) and preserves checked-in state for transient data
   mismatches (`checkinsensor.py:625-675`).
3. Update feed-miss records: a previously assigned reservation absent from the
   feed remains selected through missing counts 1 and 2; on the third miss it is
   eligible for clearing unless protected.
4. Select protected active reservations first. If protected count exceeds
   capacity, keep their current slots and report capacity exhaustion; do not
   evict them mid-stay.
5. Fill remaining capacity with the soonest eligible non-protected reservations
   by start time, then identity key as tie-breaker.
6. Assign slots deterministically while minimizing churn:
   - keep protected reservations in their current physical/persisted slots;
   - keep selected reservations in their persisted slot when that slot is not
     blocked or needed by a protected reservation;
   - otherwise use the lowest available managed slot;
   - never assign into a pending-clear slot;
   - report non-selected reservations as overflow with rank and reason.
7. Produce a diff with explicit actions: `set`, `update_times`, `clear`,
   `confirm_clear`, `retry_clear`, `overwrite_manual_change`, `blocked`, and
   `noop`.

**Rationale**:

- The spec requires no farther reservation to remain assigned while a nearer
  eligible one is unassigned once required physical operations are confirmed.
  A global sort/selection pass is the direct way to prove that invariant.
- Current `async_check_overrides()` slices `cal[:coordinator.max_events]` before
  cleanup (`event_overrides.py:780-784`), so a newly nearer event can change the
  event window without any global assignment of which existing slot should be
  evicted. The new plan makes that selection explicit.
- Current `__assign_next_slot()` picks a free slot based on local occupancy
  (`event_overrides.py:191-218`), but full-slot overflow requires replacing the
  farthest unprotected reservation, not finding the next empty slot.

**Alternatives considered**:

- **Always reassign selected reservations to slots 0..N by sort order**:
  Rejected because it causes unnecessary churn and Keymaster service calls.
- **Preserve every existing slot unless it is expired**: Rejected because it
  leaves the issue #535/#546 failure family in place when a nearer reservation
  appears.
- **Use overlap matching**: Rejected as policy. Owner guidance states
  reservations are whole-unit and non-overlapping; overlap logic should not drive
  desired-plan selection.

---

## R-005: Confirmed-Clear and Apply-Diff Model

**Decision**: A slot transitions to reusable `free` only after a physical clear
operation succeeds and a post-operation actual-state read confirms that the
Keymaster slot is clear. If clear fails or confirmation is unavailable, the slot
remains `pending_clear`/blocked and unavailable for any different reservation.

**Rationale**:

- Current `async_check_overrides()` frees the in-memory slot even when
  `async_fire_clear_code()` raises. Lines `event_overrides.py:857-875` log the
  failure and still set `_overrides[slot] = None`, pop UID/miss state, and assign
  next slot. This is the defect that can double-assign a physical slot after a
  failed clear.
- Issue #521 shows a Keymaster reset can leave a slot name behind while other
  fields clear. Current `async_fire_clear_code()` presses reset, then checks the
  name and tries to force-clear it (`util.py:217-247`), but it returns no clear
  status to the caller. Reconciliation needs a result object that says whether
  the physical slot is confirmed clear.
- The existing startup partial-reset detection (`coordinator.py:330-356`) should
  become part of normal actual-state classification, not only a boot-time
  defense.

**Apply order**:

1. Observe actual RC-managed slots by reading Keymaster entities.
2. Classify each slot as `free`, `occupied`, `phantom`, `manual_drift`,
   `pending_clear`, or `unknown`.
3. Clear actions are applied before set/update actions that need the same slot.
4. A clear action marks Store and diagnostics as `pending_clear`, attempts the
   clear, re-reads name, pin, enabled state, use-date-range, and start/end
   entities, then only marks `free` if confirmation proves the slot is empty.
5. Failed or unconfirmed clears retain the previous identity or blocked-slot
   record, increment retry state, log the blocked reason, and retry on later
   refreshes. They do not make the slot available.
6. Set/update actions verify after service calls that actual name, code-present
   status, and buffered validity window match desired state; otherwise the next
   refresh repeats the diff and diagnostics show pending correction.

**Alternatives considered**:

- **Free in memory after issuing clear**: Rejected because service failure or
  partial reset can leave stale access active while another guest is assigned.
- **Only rely on Keymaster reset button success**: Rejected because #521 proves
  the reset service can partially succeed.
- **Block the entire integration after one clear failure**: Rejected because
  other confirmed-free slots can still be reconciled safely.

---

## R-006: Manual-Edit Detection and Logging

**Decision**: Treat any conflicting actual state in an RC-managed slot as drift
unless it was produced by the currently tracked reconciliation operation. The
next refresh logs the difference and overwrites it with the desired plan, or
clears it if the slot is not desired.

**Rationale**:

- The spec makes RC authoritative for managed slots and requires every overwrite
  to be logged. Current state-change handling simply updates
  `EventOverrides` from Keymaster state (`util.py:772-815`), which means a manual
  edit can become in-memory truth instead of being corrected.
- Current `async_update()` deduplicates some writes (`event_overrides.py:485-500`)
  but does not know whether a mismatch came from a manual edit, stale Keymaster
  state, or desired-plan drift.

**Detection details**:

- Compare desired slot name, code hash/presence, and buffered date range with
  actual Keymaster state on every refresh.
- If actual differs and the slot is not in a matching operation token from the
  current reconcile pass, log a warning including slot number, changed fields,
  previous desired identity, observed identity, and action. Do not log raw PINs.
- Slots outside `start_slot .. start_slot + max_events - 1` are ignored.
- Manual edits that produce phantom name-only state are classified as phantom
  recovery plus manual/external drift when the previous Store mapping proves RC
  ownership.

**Alternatives considered**:

- **Accept manual edits as user override**: Rejected because owner guidance makes
  RC authoritative over managed slots.
- **Disable state-change listeners during reconciliation**: Rejected because the
  integration still needs actual-state observations; operation tokens and the
  shared lock give enough context without dropping events.

---

## R-007: Concurrency and Re-Entrancy

**Decision**: Keep the single `asyncio.Lock` owned by `EventOverrides` and route
all coordinator reconciliation plus Keymaster state-change cache updates through
that lock. Physical service calls use an operation-token transaction so the
slot is fenced while the lock is released. State-change callbacks record
observed actual state and return; they do not run desired-plan reconciliation
or `async_check_overrides()`.

**Rationale**:

- Existing `EventOverrides` already owns `_lock` (`event_overrides.py:113-115`)
  and uses it in `async_reserve_or_get_slot()`, `async_update()`, and
  `async_check_overrides()` (`event_overrides.py:418-462`,
  `event_overrides.py:478-516`, `event_overrides.py:786-875`). Reusing that lock
  avoids creating a second concurrency model.
- Keymaster operations generate state changes. Current `handle_state_change()`
  sleeps, updates overrides, and then calls `async_check_overrides()` again
  (`util.py:691-693`, `util.py:809-817`). Under the new design this would be a
  re-entrant reconcile loop, so callbacks must only update actual snapshots or
  mark a dirty flag.
- Holding the lock while marking pending operations prevents another coroutine
  from seeing a slot as free before clear is confirmed. The transaction flow is:
  acquire the lock, compute/validate the diff, persist `pending_clear` or
  `pending_set` plus an operation token, release the lock for the HA service
  call, read Keymaster state directly for verification, then reacquire the lock
  and transition the slot to `free`, `occupied`, or `blocked` only if the token
  still matches. Callbacks that arrive during the service call may update an
  observed-state cache or dirty flag under the same lock, but they must not
  clear the pending fence or launch reconciliation.

**Alternatives considered**:

- **Per-slot locks**: Rejected because desired-plan invariants are global across
  all managed slots.
- **No state-change cache updates**: Rejected because manual-edit diagnostics and
  Keymaster actual-state comparisons need fresh observations.
- **Immediate refresh from every callback**: Rejected because it can create the
  same state-change storm/re-entrancy risk that the existing sleep tries to
  soften.

---

## R-008: Backward Compatibility and Rollout Risk

**Decision**: Preserve all existing user-visible entities and feature semantics;
only the authority for slot assignment moves to the coordinator reconcile pass.
The implementation must include regression tests for each preserved feature.

**Preserved behavior**:

- **Slot-name extraction and trimming**: Continue using `get_slot_name()` and the
  existing `trim_name()` logic used before Keymaster writes (`calsensor.py:412-417`,
  `util.py:341-347`). Store untrimmed normalized identity and apply trimming only
  to Keymaster display names.
- **Lock-code buffers**: Continue applying `apply_buffer()` before set/update
  service calls (`util.py:388-395`, `util.py:489-496`) and preserve config-change
  buffer updates (`coordinator.py:616-675`).
- **Honor PMS times**: Keep `_ical_parser()` time precedence for timed events,
  description-extracted times, prior overrides, and configured check-in/out
  (`coordinator.py:766-823`).
- **Date-based code regeneration and should-update-code**: Preserve the current
  date-based generator behavior and update-vs-clear decision currently made in
  `calsensor.py:251-326` and `calsensor.py:592-606`, moving it into the
  coordinator/reconciler side-effect layer.
- **Check-in tracking**: Keep `CheckinTrackingSensor` states and events, using
  its `checked_in` state to protect active guests and its `checked_out` state to
  avoid indefinite protection (`checkinsensor.py:757-806`,
  `checkinsensor.py:830-870`).
- **Diagnostics option**: Existing Keymaster event diagnostics remain; new
  desired-vs-actual diagnostics should be exposed alongside or through a
  coordinator diagnostic snapshot without removing current attributes
  (`coordinator.py:161-164`, `checkinsensor.py:293-300`).

**Rollout risks and mitigations**:

- First upgrade with populated slots could wipe working codes if migration is too
  aggressive. Mitigation: adopt matching actual slots into Store and use
  confirmed-clear before any removal.
- UID churn could duplicate reservations. Mitigation: primary derived identity
  with UID/booking aliases only as fallbacks.
- Clear failure could cause double assignment. Mitigation: `pending_clear` blocks
  assignment until physical confirmation.
- Reconciliation could fight manual edits repeatedly. Mitigation: log every
  overwrite with fields and desired identity so the owner can diagnose why RC
  restored a slot.
- Global planning could regress current small features. Mitigation: quickstart
  requires targeted regression tests for trimming, buffers, PMS times,
  date-based regeneration, and check-in tracking.

**Alternatives considered**:

- **Expose new event entities instead of preserving `event_N`**: Rejected because
  users and automations rely on existing entity names and attributes.
- **Ship diagnostics later**: Rejected because FR-014 and SC-010 require a single
  desired-vs-actual diagnostic capture for troubleshooting.
