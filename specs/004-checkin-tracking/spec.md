<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Guest Check-in/Check-out Tracking

**Feature Branch**: `004-checkin-tracking`
**Created**: 2025-07-15
**Status**: Draft
**Input**: User description: "Add a guest check-in/check-out tracking sensor that monitors occupancy state, transitions through defined states as guests arrive and depart, supports keymaster unlock detection and time-based auto check-in, and provides manual check-out with optional early lock code expiry."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Core Occupancy Tracking (Priority: P1)

As a property manager, I want a sensor that automatically reflects the current occupancy state of my rental property (no reservation, awaiting check-in, checked in, checked out) so that I can build automations around guest arrivals and departures without manually tracking calendar events.

**Why this priority**: This is the foundational capability. Without the tracking sensor and its state machine, no other feature in this spec delivers value. A property manager with only this story still gains visibility into occupancy state and can trigger automations on state changes.

**Independent Test**: Can be fully tested with a configured integration instance, a calendar containing events, and no keymaster configuration. The sensor transitions through all four states based solely on event timing, and Home Assistant events fire on each transition.

**Acceptance Scenarios**:

1. **Given** no upcoming events in the calendar, **When** the integration loads, **Then** the sensor displays `no_reservation`
2. **Given** an event exists that starts tomorrow, **When** the integration refreshes and identifies the event, **Then** the sensor transitions to `awaiting_checkin` and its attributes include the event summary, start time, end time, and guest name
3. **Given** the sensor is in `awaiting_checkin` and keymaster monitoring is disabled, **When** the event's start time arrives, **Then** the sensor automatically transitions to `checked_in` and a `rental_control_checkin` event fires with `source: automatic`
4. **Given** the sensor is in `checked_in`, **When** the event's end time arrives, **Then** the sensor automatically transitions to `checked_out` and a `rental_control_checkout` event fires with `source: automatic`
5. **Given** the sensor is in `checked_out` and another event starts on the same day (same-day turnover), **When** half the time gap between checkout and the next checkin elapses (per FR-006a), **Then** the sensor transitions to `awaiting_checkin` for the next event
6. **Given** the sensor is in `checked_out` and no further events exist, **When** the configured cleaning window elapses after checkout (per FR-006b), **Then** the sensor transitions to `no_reservation`
7. **Given** the sensor is in `checked_out` and the next event starts on a later day, **When** the post-checkout midnight boundary passes (per FR-006c), **Then** the sensor transitions to `no_reservation` and subsequently to `awaiting_checkin` at 00:00 on the next event's start day
8. **Given** the sensor is in `checked_out` for the current event, **When** the host extends the event's end time (e.g., guest forgot something and contacts host who extends the reservation), **Then** the sensor remains in `checked_out` and does not re-transition to `checked_in` or `awaiting_checkin` for the same event

---

### User Story 2 — State Persistence Across Restarts (Priority: P1)

As a property manager, I want the check-in tracking sensor to survive Home Assistant restarts without losing its current state so that I do not miss guest arrivals or departures during system maintenance.

**Why this priority**: Tied with Story 1 as P1 because state loss on restart would make the tracking sensor unreliable for any real-world use, undermining trust in the entire feature.

**Independent Test**: Can be tested by setting the sensor to a known state, restarting the integration, and verifying the state is correctly restored and validated against current time and calendar data.

**Acceptance Scenarios**:

1. **Given** the sensor is in `checked_in` during an active event, **When** Home Assistant restarts, **Then** the sensor restores to `checked_in` and continues tracking the same event
2. **Given** the sensor was in `checked_in` when HA shut down but the event has since ended, **When** Home Assistant restarts, **Then** the sensor transitions to `checked_out` and then evaluates the next event
3. **Given** the sensor was in `awaiting_checkin` and the event start has now passed (time-based mode), **When** Home Assistant restarts, **Then** the sensor transitions to `checked_in`
4. **Given** the sensor was in `checked_out` but a new event is now relevant, **When** Home Assistant restarts, **Then** the sensor transitions to `awaiting_checkin` for the new event

---

### User Story 3 — Keymaster Unlock Detection (Priority: P2)

As a property manager using keymaster-managed smart locks, I want the system to detect when a guest uses their assigned lock code for the first time so that the sensor transitions to `checked_in` only when the guest physically arrives, rather than at a fixed time.

**Why this priority**: Enhances accuracy of check-in detection for users with keymaster integration. Not required for basic functionality (time-based fallback works without it), but significantly improves the user experience for the common keymaster use case.

**Independent Test**: Can be tested by configuring a keymaster-linked integration instance, enabling the monitoring toggle, and simulating keymaster lock state change events with matching code slot numbers.

**Acceptance Scenarios**:

1. **Given** keymaster is configured and the monitoring toggle is enabled, **When** the sensor is in `awaiting_checkin` and a keymaster unlock event fires with a code slot matching the currently tracked event, **Then** the sensor transitions to `checked_in` and a `rental_control_checkin` event fires with `source: keymaster`
2. **Given** keymaster is configured and the monitoring toggle is enabled, **When** the sensor is in `awaiting_checkin` and a keymaster unlock event fires with code slot 0 (manual/RF unlock), **Then** the sensor remains in `awaiting_checkin` (no transition)
3. **Given** keymaster is configured and the monitoring toggle is enabled, **When** the sensor is already in `checked_in` and another keymaster unlock event fires for the same slot, **Then** the sensor remains in `checked_in` (subsequent unlocks are ignored)
4. **Given** keymaster is configured and the monitoring toggle is enabled, **When** a keymaster unlock event fires with a code slot outside the managed range, **Then** the sensor state is unaffected
5. **Given** keymaster is configured but the monitoring toggle is disabled, **When** the event start time arrives, **Then** the sensor transitions to `checked_in` automatically (time-based fallback)

---

### User Story 4 — Manual Guest Check-out (Priority: P2)

As a property manager, I want to manually check out a guest before the event's scheduled end time so that I can accurately reflect early departures and trigger departure automations immediately.

**Why this priority**: Adds active control beyond passive time-based tracking. Not required for the sensor to function, but delivers significant value for managers who need to react to early departures.

**Independent Test**: Can be tested by placing the sensor in `checked_in` state and invoking the checkout action, verifying the transition to `checked_out` and the firing of the checkout event.

**Acceptance Scenarios**:

1. **Given** the sensor is in `checked_in` and the current date-time is within the active reservation window (on or after event start and before event end), **When** the user invokes the checkout action, **Then** the sensor transitions to `checked_out` and a `rental_control_checkout` event fires with `source: manual`
2. **Given** the sensor is in `awaiting_checkin`, **When** the user invokes the checkout action, **Then** the action fails with an error indicating checkout is only available when checked in
3. **Given** the sensor is in `checked_in` but the current date-time is outside the active reservation window, **When** the user invokes the checkout action, **Then** the action fails with a descriptive error
4. **Given** the sensor is in `checked_in` but the current time is after the event end, **When** the user invokes the checkout action, **Then** the action fails with an error (the automatic checkout should have already occurred)

---

### User Story 5 — Early Check-out with Lock Code Expiry (Priority: P3)

As a property manager using keymaster, I want the option for a manual early check-out to immediately begin expiring the guest's lock code (with a short grace period) so that departed guests cannot re-enter the property after checking out.

**Why this priority**: This is a security enhancement layered on top of manual check-out (Story 4) and keymaster (Story 3). Requires both to be functional first, and only applies to a subset of users with keymaster-configured locks.

**Independent Test**: Can be tested by configuring keymaster, enabling the early expiry toggle, checking in a guest, then invoking the checkout action and verifying that the keymaster slot's end date is updated to a near-future time.

**Acceptance Scenarios**:

1. **Given** keymaster is configured, the early expiry toggle is enabled, and a guest is checked in, **When** the user invokes the checkout action, **Then** the keymaster slot's date range end is updated to the earlier of "now + 15 minutes" or the event's original end time
2. **Given** keymaster is configured, the early expiry toggle is enabled, and the checkout action is invoked with less than 15 minutes remaining before the event end, **When** the slot update occurs, **Then** the slot end time is set to the event's original end time (grace period does not extend beyond the scheduled checkout)
3. **Given** keymaster is configured but the early expiry toggle is disabled, **When** the user invokes the checkout action, **Then** the keymaster slot dates are not modified and the lock code remains active until the event naturally ends
4. **Given** keymaster is not configured, **When** the early expiry toggle entity does not exist, **Then** no lock code expiry behavior is available (toggle entity is absent)

---

### User Story 6 — Same-Day Turnover Handling (Priority: P3)

As a property manager with back-to-back bookings, I want the sensor to correctly handle same-day turnovers so that the departing guest's checkout completes before the arriving guest's check-in begins tracking.

**Why this priority**: Edge case handling for a common real-world scenario. The core state machine (Story 1) handles normal single-event flows; this story ensures correctness during overlapping turnover days.

**Independent Test**: Can be tested by configuring two events where event 0 checks out and event 1 checks in on the same day, then verifying the sensor tracks the departing guest until their event ends before shifting to the arriving guest.

**Acceptance Scenarios**:

1. **Given** event 0 ends today and event 1 starts today, **When** event 0's end time arrives, **Then** the sensor transitions to `checked_out` for event 0 and remains there for half the gap between event 0's end and event 1's start before transitioning to `awaiting_checkin` for event 1 (per FR-006a)
2. **Given** event 0 ends today and event 1 starts today, **When** the sensor is tracking event 0 before event 0's end time, **Then** the sensor continues showing event 0's data (does not prematurely switch to event 1)
3. **Given** event 0 ends today and event 1 starts on a later day, **When** event 0's end time arrives, **Then** the sensor transitions to `checked_out`, remains there until the post-checkout midnight boundary (00:00 immediately following the checkout day), transitions to `no_reservation`, and then transitions to `awaiting_checkin` at 00:00 on event 1's start day (per FR-006c)
4. **Given** event 0 ends today and no further events exist, **When** event 0's end time arrives, **Then** the sensor transitions to `checked_out` and remains there for the configured cleaning window before transitioning to `no_reservation` (per FR-006b)

### Edge Cases

- **No events in calendar**: Sensor stays in `no_reservation` indefinitely until an event appears
- **HA restart during active event**: Restored state is validated against current time and event data; adjusted if stale (see Story 2)
- **Multiple rapid unlock events**: Only the first matching unlock transitions from `awaiting_checkin` to `checked_in`; all subsequent unlocks are ignored
- **Manual/RF unlock (no code slot)**: Unlocks with `code_slot_num == 0` never trigger check-in detection regardless of toggle state
- **Post-checkout linger period**: The sensor intentionally delays in `checked_out` before transitioning. The duration depends on the scenario: same-day turnover uses half the gap to next checkin (FR-006a), no follow-on uses a configurable cleaning window (FR-006b), and next-day+ reservation uses post-checkout midnight boundary (FR-006c). The "checkout time" used for these calculations is the actual time the transition to `checked_out` occurred (event end time for automatic, invocation time for manual)
- **Event cancelled or removed from calendar**: Sensor transitions to `no_reservation` or shifts tracking to the next available event on the next coordinator update
- **Calendar fetch failure with cached data**: Sensor continues operating on the coordinator's cached event data, leveraging the existing stale-data preservation mechanism
- **Lock code expiry timing on early checkout**: The slot's date range end is set to the earlier of "now + 15 minutes" or the event's original end time, ensuring the grace period never extends past the scheduled checkout
- **Keymaster monitoring toggle changed mid-event**: If toggled on while in `awaiting_checkin`, the sensor begins listening for unlock events immediately. If toggled off while in `awaiting_checkin`, the sensor falls back to time-based auto check-in at event start. If the sensor is already `checked_in`, toggling has no effect on the current event.
- **Event end time extended after checkout**: If a guest has already been checked out (sensor state is `checked_out`) and the host subsequently extends the event's checkout/end time (e.g., the guest contacts the host after departure), the sensor MUST remain in `checked_out`. The checkout is final once triggered; the system does not re-transition to `checked_in` or `awaiting_checkin` for the same event even if the underlying event data changes afterward.
- **Overlapping with event overrides**: The check-in tracking sensor is read-only; it does not affect slot assignment logic in the existing event overrides system

## Requirements *(mandatory)*

### Functional Requirements

#### State Machine

- **FR-001**: System MUST provide a single check-in tracking sensor per integration instance that displays one of four states: `no_reservation`, `awaiting_checkin`, `checked_in`, `checked_out`
- **FR-002**: System MUST track the single most relevant event at any given time — the nearest upcoming or currently active event from the sorted calendar data
- **FR-003**: System MUST transition from `no_reservation` to `awaiting_checkin` when the coordinator identifies a relevant upcoming event
- **FR-004**: System MUST transition from `awaiting_checkin` to `checked_in` either automatically at event start time (time-based mode) or upon matching keymaster unlock detection (keymaster mode)
- **FR-005**: System MUST transition from `checked_in` to `checked_out` automatically at the event's end time
- **FR-006**: After transitioning to `checked_out`, the sensor MUST NOT immediately transition to the next state. The sensor MUST linger in `checked_out` for a scenario-dependent duration before transitioning:
  - **FR-006a** *(Same-day turnover)*: When the current event checks out and the next event checks in on the same calendar day, the sensor MUST remain in `checked_out` for **half the time gap** between the checkout time and the next event's start time, then transition to `awaiting_checkin`. *(Example: checkout at 11:00, next checkin at 16:00 → 5h gap → sensor stays `checked_out` for 2.5h until 13:30, then transitions to `awaiting_checkin`.)*
  - **FR-006b** *(No follow-on reservation)*: When the guest checks out and no further events exist in the calendar, the sensor MUST remain in `checked_out` for a configurable **cleaning window** duration (in hours) after the checkout time (see FR-008), then transition to `no_reservation`.
  - **FR-006c** *(Next reservation on a different day)*: When the guest checks out and the next event starts on a different (later) calendar day, the sensor MUST remain in `checked_out` until the **post-checkout midnight boundary**, defined as **00:00 immediately following the checkout day**, then transition to `no_reservation`. The sensor MUST subsequently transition from `no_reservation` to `awaiting_checkin` at **00:00 on the calendar day** the next event begins.
- **FR-007**: Once the sensor has transitioned to `checked_out` for a given event (whether via automatic time-based checkout or manual checkout), the sensor MUST remain in `checked_out` even if the underlying event's end time is subsequently extended or modified. The `checked_out` state is final for the current event; changes to event data MUST NOT re-transition the sensor back to `checked_in` or `awaiting_checkin` for the same event
- **FR-008**: System MUST provide a configurable "cleaning window" option (in hours) that defines how long the sensor lingers in `checked_out` when no follow-on reservation exists (FR-006b). The default MUST be 6 hours. This option MUST be configurable per integration instance via the options flow.

#### Sensor Attributes

- **FR-009**: The sensor MUST expose the currently tracked event's summary, start time, end time, and guest name/slot information as state attributes
- **FR-010**: The sensor MUST update its attributes whenever the coordinator provides new event data

#### State Persistence

- **FR-011**: The sensor MUST persist its state across Home Assistant restarts using the restore entity pattern
- **FR-012**: On startup, the sensor MUST validate restored state against current time and event data and transition if the restored state is no longer valid (see Story 2 acceptance scenarios)

#### Keymaster Unlock Detection

- **FR-013**: When keymaster is configured, the system MUST create a toggle entity that enables/disables keymaster unlock monitoring for check-in detection (default: disabled)
- **FR-014**: When the keymaster monitoring toggle is enabled, the system MUST listen for keymaster lock state changed events and match unlocks where the state is "unlocked" and the code slot number falls within the managed slot range for this integration instance
- **FR-015**: When a matching keymaster unlock is detected and the sensor is in `awaiting_checkin`, the system MUST transition to `checked_in`
- **FR-016**: When a matching keymaster unlock is detected and the sensor is already in `checked_in`, the system MUST ignore the event (no re-trigger)
- **FR-017**: Unlock events with `code_slot_num == 0` (manual/RF unlocks) MUST NOT trigger check-in detection

#### Manual Check-out Action

- **FR-018**: System MUST provide a checkout action that can be invoked on the check-in tracking sensor entity
- **FR-019**: The checkout action MUST only succeed when all guard conditions are met: sensor is in `checked_in` state and the current date-time is within the active reservation window (on or after the event start date-time and strictly before the event end date-time)
- **FR-020**: The checkout action MUST fail with a descriptive error when any of these guard conditions is not met

#### Early Check-out Lock Code Expiry

- **FR-021**: When keymaster is configured, the system MUST create a toggle entity that enables/disables early check-out lock code expiry (default: disabled)
- **FR-022**: When the early expiry toggle is enabled and a manual checkout occurs, the system MUST update the keymaster slot's date range end to the earlier of "now + 15 minutes" or the event's original end time
- **FR-023**: When the early expiry toggle is disabled or keymaster is not configured, a manual checkout MUST NOT modify keymaster slot dates

#### Home Assistant Events

- **FR-024**: System MUST fire a `rental_control_checkin` event on the Home Assistant event bus when transitioning to `checked_in`, including entity ID, event summary, event start, event end, and source (either `keymaster` or `automatic`)
- **FR-025**: System MUST fire a `rental_control_checkout` event on the Home Assistant event bus when transitioning to `checked_out`, including entity ID, event summary, event start, event end, and source (either `manual` or `automatic`)

#### Conditional Entity Creation

- **FR-026**: The keymaster monitoring toggle entity MUST only be created when keymaster is configured for the integration instance
- **FR-027**: The early check-out expiry toggle entity MUST only be created when keymaster is configured for the integration instance
- **FR-028**: The check-in tracking sensor entity MUST always be created for every integration instance regardless of keymaster configuration

#### Same-Day Turnover

- **FR-029**: During same-day turnovers, the sensor MUST continue tracking the departing guest until their event end time before shifting to the arriving guest's event

### Key Entities

- **Check-in Tracking Sensor**: Represents the occupancy state of the rental property. Key attributes: current state (one of four values), tracked event summary, event start/end times, guest name/slot information. One sensor per integration instance. Linked to the integration's device.
- **Keymaster Monitoring Toggle**: Controls whether keymaster unlock events are used for check-in detection. Only exists when keymaster is configured. Default: disabled.
- **Early Check-out Expiry Toggle**: Controls whether manual check-out triggers near-term lock code expiry. Only exists when keymaster is configured. Default: disabled.
- **Tracked Event**: The single calendar event currently being monitored by the sensor. Determined by event relevance/priority logic (nearest upcoming or currently active event). Not a standalone entity — represented as sensor attributes.

## Assumptions

- The existing coordinator's sorted event data (event 0, event 1, etc.) provides sufficient information to determine event relevance and priority without additional calendar queries
- The keymaster integration's `keymaster_lock_state_changed` event format is stable and will continue providing `code_slot_num` as an integer identifying the keypad slot used
- Lock providers that do not support code slot identification will report `code_slot_num == 0`, which is correctly handled by the "no trigger" behavior
- The 15-minute grace period for early lock code expiry is a fixed value suitable for all use cases (allows a guest to re-enter briefly if they forgot something)
- The "active reservation window" for manual checkout guard validation is defined as the period from the event start date-time (inclusive) to the event end date-time (exclusive)
- The existing integration utilities for updating keymaster slot date ranges can be reused for the early expiry feature

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The check-in tracking sensor accurately reflects the correct occupancy state within one coordinator update cycle of the relevant time boundary (event start, event end)
- **SC-002**: State transitions produce the correct Home Assistant event bus events 100% of the time, with accurate source attribution (`keymaster`, `automatic`, or `manual`)
- **SC-003**: The sensor correctly restores its state after a Home Assistant restart and validates against current conditions, with no user intervention required
- **SC-004**: Keymaster unlock detection transitions the sensor to `checked_in` on the first matching unlock event, and ignores all subsequent unlock events for the same stay
- **SC-005**: The manual checkout action enforces all guard conditions and never permits checkout when conditions are not met
- **SC-006**: Same-day turnovers correctly sequence the departing guest's checkout before the arriving guest's awaiting-checkin transition
- **SC-007**: The early check-out lock code expiry feature updates the keymaster slot end time and never extends the grace period beyond the event's original end time
- **SC-008**: Toggle entities are only created when keymaster is configured, and are absent otherwise — no orphaned or non-functional entities
- **SC-009**: All new entities appear under the existing integration device in Home Assistant with appropriate naming and unique identifiers
- **SC-010**: The feature operates correctly in both keymaster-configured and keymaster-absent scenarios with no errors or degraded behavior
- **SC-011**: Post-checkout linger timing is accurate: same-day turnover transitions to `awaiting_checkin` within one coordinator update cycle of the half-gap target time; cleaning-window transitions to `no_reservation` within one coordinator update cycle of the cleaning window expiry; midnight-boundary transitions occur within one coordinator update cycle after 00:00

## Clarifications

### Session 2026-03-20

- Q: If a guest is already checked out and the host extends the event's checkout/end time, should the sensor re-transition back to `checked_in` or `awaiting_checkin`? → A: No. The `checked_out` state is final once triggered. Event data changes after checkout MUST NOT cause re-transition for the same event.
- Q: How long should the sensor linger in `checked_out` during a same-day turnover? → A: Half the time gap between the checkout time and the next event's start time (FR-006a).
- Q: How long should the sensor linger in `checked_out` when no follow-on reservation exists? → A: A configurable "cleaning window" in hours after checkout time, then transition to `no_reservation` (FR-006b, FR-008).
- Q: How should `checked_out` transition work when the next reservation is on a different day? → A: Linger until the post-checkout midnight boundary (00:00 immediately following the checkout day) → `no_reservation`, then transition to `awaiting_checkin` at 00:00 on the next event's start day (FR-006c).
