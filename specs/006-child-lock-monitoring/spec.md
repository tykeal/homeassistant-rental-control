<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Child Lock Monitoring for Keymaster Parent/Child Lock Setups

**Feature Branch**: `006-child-lock-monitoring`
**Created**: 2025-07-18
**Status**: Draft
**Input**: User description: "Child Lock Monitoring for Keymaster Parent/Child Lock Setups — When rental-control is configured with a keymaster parent lock, unlock events from all child locks of that parent should also trigger check-in detection. The monitoring switch should control parent and all child locks. Child lock discovery should be dynamic."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Child Lock Unlock Triggers Check-in (Priority: P1)

As a property manager with multiple entrances (front door, side door, garage) managed by a keymaster parent/child lock setup, I want check-in detection to trigger when a guest unlocks ANY door — not just the parent lock's door — so that guests are reliably checked in regardless of which entrance they use.

**Why this priority**: This is the core problem being solved. Currently, guests who enter through a child lock door are never detected as checked in, causing automation failures, missed welcome sequences, and incorrect occupancy tracking. Without this, the entire parent/child lock setup is effectively broken for check-in detection.

**Independent Test**: Can be fully tested with a rental-control instance configured with a keymaster parent lock that has at least one child lock. Simulate unlock events from both parent and child locks and verify the checkin sensor transitions to `checked_in` in both cases.

**Acceptance Scenarios**:

1. **Given** a rental-control entry is configured with a keymaster parent lock that has two child locks, and a reservation is in `awaiting_checkin` state, **When** a guest unlocks the parent lock door using their assigned code slot, **Then** the checkin sensor transitions to `checked_in` with the correct guest and event details (existing behavior preserved)
2. **Given** a rental-control entry is configured with a keymaster parent lock that has two child locks, and a reservation is in `awaiting_checkin` state, **When** a guest unlocks a child lock door using their assigned code slot, **Then** the checkin sensor transitions to `checked_in` with the correct guest and event details
3. **Given** a rental-control entry is configured with a keymaster parent lock that has two child locks, and a reservation is in `awaiting_checkin` state, **When** a guest unlocks the other child lock door using their assigned code slot, **Then** the checkin sensor transitions to `checked_in` with the correct guest and event details
4. **Given** a rental-control entry is configured with a keymaster parent lock with child locks, and a reservation is in `awaiting_checkin` state, **When** someone unlocks a child lock door using a code slot that is NOT in the managed range, **Then** no check-in is triggered and the sensor remains in `awaiting_checkin`
5. **Given** a rental-control entry is configured with a keymaster parent lock with child locks, and no reservation is in `awaiting_checkin` state, **When** a guest unlocks any child lock door, **Then** no state transition occurs

---

### User Story 2 — Unified Monitoring Switch (Priority: P2)

As a property manager, I want the existing keymaster monitoring switch to control unlock event monitoring for the parent lock AND all its child locks as a single unit, so that I can enable or disable check-in detection for all entrances with one toggle.

**Why this priority**: Without unified monitoring control, a property manager would need to manage monitoring for each lock individually, which is error-prone and inconsistent with the current single-switch experience. This story ensures operational simplicity.

**Independent Test**: Can be tested by toggling the monitoring switch and verifying that unlock events from both parent and child locks are either all processed or all ignored based on the switch state.

**Acceptance Scenarios**:

1. **Given** the keymaster monitoring switch is enabled, **When** a guest unlocks any child lock door with a valid code slot, **Then** the unlock event is processed and check-in detection occurs
2. **Given** the keymaster monitoring switch is disabled, **When** a guest unlocks any child lock door with a valid code slot, **Then** the unlock event is ignored and no check-in detection occurs
3. **Given** the keymaster monitoring switch is enabled, **When** a guest unlocks the parent lock door, **Then** the unlock event is processed as before (existing behavior preserved)
4. **Given** the keymaster monitoring switch is disabled, **When** a guest unlocks the parent lock door, **Then** the unlock event is ignored as before (existing behavior preserved)

---

### User Story 3 — Dynamic Child Lock Discovery (Priority: P3)

As a property manager, I want rental-control to automatically detect when child locks are added to or removed from a keymaster parent lock configuration, so that I do not need to restart or reconfigure rental-control when my lock setup changes.

**Why this priority**: While most property managers set up their locks once and rarely change them, supporting dynamic discovery prevents stale configuration, avoids manual reconfiguration, and handles edge cases like temporary lock additions during renovations or replacements.

**Independent Test**: Can be tested by adding a new child lock to a keymaster parent configuration and verifying that rental-control begins processing unlock events from the new child lock without requiring a restart or reconfiguration of the rental-control entry.

**Acceptance Scenarios**:

1. **Given** a rental-control entry is configured with a keymaster parent lock that has one child lock, **When** a second child lock is added to the parent in keymaster, **Then** rental-control begins processing unlock events from the new child lock without requiring a restart or reconfiguration
2. **Given** a rental-control entry is configured with a keymaster parent lock that has two child locks, **When** one child lock is removed from the parent in keymaster, **Then** rental-control stops processing unlock events from the removed child lock without requiring a restart or reconfiguration
3. **Given** a rental-control entry is configured with a keymaster lock that has no children, **When** a child lock is added to that lock in keymaster, **Then** rental-control begins processing unlock events from the new child lock

---

### Edge Cases

- What happens when a child lock fires an unlock event simultaneously with the parent lock for the same code slot? Only one check-in transition should occur; the first valid event triggers the transition and subsequent events for an already-checked-in reservation are no-ops.
- What happens when the keymaster parent lock entry is removed or unloaded from Home Assistant? Rental-control should handle the absence gracefully — the monitoring switch reflects that no lock is configured, and no events are processed. This is existing behavior and should be preserved.
- What happens when a child lock fires an unlock event but the parent lock entry has been temporarily unloaded (e.g., keymaster reload)? The event should be ignored since the parent lock context is unavailable. No error should be raised.
- What happens when a lock that was a child is reconfigured as a standalone lock (no longer a child)? Rental-control should stop treating it as part of the parent/child group on the next discovery refresh.
- What happens when the code slot used at a child lock is valid but has been cleared from the parent since the last sync? The code slot validation uses the parent's managed range, so a slot number in the managed range but with a cleared code is still a valid code slot number — check-in proceeds based on slot number, not code content.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept unlock events from child locks of the configured keymaster parent lock and process them identically to parent lock unlock events for check-in detection purposes
- **FR-002**: System MUST validate child lock unlock events using the same criteria as parent lock events: state must be "unlocked", code slot number must be greater than zero, and the slot must be within the managed range
- **FR-003**: System MUST use the parent lock's code slot configuration for all validation — child locks share the parent's slots and no separate slot tracking is needed for children
- **FR-004**: System MUST treat the keymaster monitoring switch as controlling monitoring for both the parent lock and all its child locks — a single switch governs all lock event processing
- **FR-005**: System MUST dynamically discover child locks associated with the configured parent lock and update the set of monitored locks when children are added or removed
- **FR-006**: System MUST NOT require any configuration changes, integration reload, or Home Assistant restart when child locks are added to or removed from the keymaster parent lock
- **FR-007**: System MUST continue to function correctly when no child locks are configured — the parent-only behavior must be fully preserved as the default case
- **FR-008**: System MUST ignore duplicate check-in triggers — if multiple locks fire unlock events for the same reservation, only the first valid event triggers the check-in transition
- **FR-009**: System MUST include the identity of the lock that triggered the check-in (parent or specific child) in the check-in event data so that property managers can determine which entrance was used
- **FR-010**: System MUST NOT track or manage child lock entities (switches, text inputs, datetime, buttons) for code slot management — entity state tracking remains scoped to parent lock entities only, since child locks share the parent's code slots
- **FR-011**: System MUST handle gracefully the scenario where a child lock fires an event but the parent lock configuration is temporarily unavailable (e.g., during keymaster reload) — the event should be silently discarded without raising errors

### Key Entities

- **Parent Lock**: The primary keymaster lock entry configured in rental-control via `CONF_LOCK_ENTRY`. Owns the code slot configuration, entity state tracking, and is the anchor for child lock discovery. Identified by its lockname (slugified from the keymaster entry title).
- **Child Lock**: A keymaster lock entry that references a parent lock. Shares the parent's code slots. Has its own distinct lockname used in `keymaster_lock_state_changed` events. Discovered dynamically via the parent's child lock references.
- **Monitored Lock Set**: The collection of locknames (parent + all children) whose unlock events are accepted by the event bus listener. Updated dynamically as child locks are added or removed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of unlock events from child locks with valid code slots in `awaiting_checkin` state trigger check-in detection, matching the existing parent lock behavior
- **SC-002**: Check-in detection from a child lock unlock event completes within the same time as parent lock detection — no perceptible delay is introduced
- **SC-003**: Adding or removing a child lock in keymaster is reflected in rental-control's monitored lock set without any manual intervention (no restart, no reconfiguration)
- **SC-004**: The monitoring switch controls all locks (parent + children) as a single unit — toggling the switch once affects monitoring for every entrance
- **SC-005**: Zero false check-ins from child lock events — code slot validation rejects out-of-range slots and non-unlock states identically to parent lock validation
- **SC-006**: Properties with no child locks experience zero behavioral changes — the feature is fully backward compatible
- **SC-007**: Property managers can determine which specific entrance (lock) triggered a guest check-in from the check-in event data

## Assumptions

- Keymaster's parent/child lock relationship is accessible via keymaster's configuration entries (parent stores child references, child stores parent reference)
- Child locks always share the parent's code slots — there is no scenario where a child lock has independent code slot assignments
- The `keymaster_lock_state_changed` event schema is identical for parent and child locks, differing only in the `lockname` field
- The keymaster integration provides a stable mechanism to discover which locks are children of a given parent (via config entry data or device registry relationships)
- A single rental-control config entry is associated with exactly one keymaster parent lock; monitoring multiple independent parent locks requires multiple rental-control config entries
- The lock that fires the unlock event is always identifiable from the event's `lockname` field
