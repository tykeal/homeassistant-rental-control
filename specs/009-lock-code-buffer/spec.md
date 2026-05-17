# Feature Specification: Lock Code Buffer Times

**Feature Branch**: `009-lock-code-buffer`
**Created**: 2025-07-17
**Status**: Draft
**Input**: User description: "Add configurable pre/post buffer times for lock codes. Some property managers need lock codes to activate before the scheduled check-in time and/or remain active after the scheduled checkout time."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Configure Pre-Buffer for Early Guest Arrival (Priority: P1)

As a property manager, I want to set a buffer period before check-in so that lock codes activate early, giving guests who arrive ahead of schedule immediate access without needing to contact me.

**Why this priority**: This is the primary use case driving the feature. Early arrivals are the most common timing friction point for property managers, and providing seamless early access reduces guest frustration and support burden.

**Independent Test**: Can be fully tested by configuring a before-buffer value and verifying that the lock code validity start time is shifted earlier by the configured number of minutes.

**Acceptance Scenarios**:

1. **Given** the integration is configured with a 30-minute before-buffer, **When** a lock code is programmed for a reservation starting at 3:00 PM, **Then** the lock code becomes valid at 2:30 PM.
2. **Given** the integration is configured with a 0-minute before-buffer (default), **When** a lock code is programmed, **Then** the lock code validity start matches the event start time exactly.
3. **Given** a guest arrives 20 minutes early and the before-buffer is 30 minutes, **When** the guest enters their lock code, **Then** the code works and the check-in sensor transitions from "awaiting check-in" to "checked in."

---

### User Story 2 - Configure Post-Buffer for Late Checkout Grace (Priority: P2)

As a property manager, I want to set a buffer period after checkout so that lock codes remain active slightly past the scheduled checkout time, accommodating guests who are running a few minutes behind.

**Why this priority**: Late checkouts are common and a deactivated code at the exact checkout minute creates a poor guest experience. This complements the pre-buffer and completes the full buffer feature.

**Independent Test**: Can be fully tested by configuring an after-buffer value and verifying that the lock code validity end time is extended by the configured number of minutes.

**Acceptance Scenarios**:

1. **Given** the integration is configured with a 15-minute after-buffer, **When** a lock code is programmed for a reservation ending at 11:00 AM, **Then** the lock code remains valid until 11:15 AM.
2. **Given** the integration is configured with a 0-minute after-buffer (default), **When** a lock code is programmed, **Then** the lock code validity end matches the event end time exactly.

---

### User Story 3 - Adjust Buffer Settings for Active Reservations (Priority: P3)

As a property manager, I want to change buffer settings and have them apply to currently active reservation lock codes on the next refresh cycle, so I can adjust timing without manually reprogramming locks.

**Why this priority**: Allows managers to fine-tune buffer settings after initial setup without disrupting active reservations. This is a natural follow-on once buffers are configured.

**Independent Test**: Can be fully tested by changing buffer values in the options flow while an active reservation exists and verifying the lock code date range updates on the next coordinator refresh.

**Acceptance Scenarios**:

1. **Given** an active reservation has a lock code programmed with a 30-minute before-buffer, **When** the manager changes the before-buffer to 60 minutes, **Then** the lock code validity start is updated to reflect the new buffer on the next refresh cycle.
2. **Given** buffer values are changed, **When** the next coordinator refresh occurs, **Then** all active lock code slots are reprogrammed with the updated buffer-adjusted date ranges.

---

### User Story 4 - Seamless Upgrade for Existing Users (Priority: P1)

As an existing user upgrading the integration, I want my current behavior to remain unchanged until I explicitly configure buffer times, so the upgrade does not alter my lock code timing.

**Why this priority**: Equal to P1 because breaking existing users on upgrade is unacceptable. Default buffer values of 0 ensure backward compatibility.

**Independent Test**: Can be fully tested by upgrading from config version 9 to 10 and verifying that both buffer values default to 0 and all lock code timing remains identical to pre-upgrade behavior.

**Acceptance Scenarios**:

1. **Given** a user is running config version 9, **When** the integration upgrades to config version 10, **Then** both buffer options are added with a default value of 0.
2. **Given** a freshly upgraded installation with default buffer values, **When** lock codes are programmed, **Then** the lock code date ranges are identical to what they were before the upgrade.

---

### Edge Cases

- What happens when the before-buffer is larger than the time between consecutive reservations? The buffered start of the next reservation may overlap with the (potentially buffered) end of the previous one. Each reservation's lock code operates independently; the lock itself handles overlapping valid windows for different codes.
- What happens when a user enters a very large buffer value (e.g., 1440 minutes / 24 hours)? The system accepts any non-negative integer. The resulting lock code window extends accordingly. No upper-bound validation is enforced since legitimate use cases for large buffers exist (e.g., multi-day early access for cleaning crews).
- What happens when buffer values are changed while no lock entry is configured? The buffer options are only visible and editable when a lock entry is configured. The values are stored but have no effect until a lock is associated.
- How does the system handle a buffer that extends the code validity across a date boundary? The buffered datetime is calculated as a simple time offset; crossing midnight or date boundaries is handled naturally by datetime arithmetic.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a "code buffer before" configuration option that specifies the number of minutes before a reservation's start time that the lock code should activate.
- **FR-002**: System MUST provide a "code buffer after" configuration option that specifies the number of minutes after a reservation's end time that the lock code should deactivate.
- **FR-003**: Both buffer options MUST default to 0 minutes, preserving existing behavior for new and upgraded installations.
- **FR-004**: Both buffer options MUST accept only non-negative integer values (minimum 0).
- **FR-005**: Buffer values MUST only affect the lock code validity window sent to the lock management system (Keymaster date range start/end). They MUST NOT affect calendar event display times, check-in/checkout sensor transition timing, event override matching, or auto check-in/checkout timer scheduling.
- **FR-006**: System MUST subtract the "code buffer before" minutes from the reservation start time when setting the lock code validity start.
- **FR-007**: System MUST add the "code buffer after" minutes to the reservation end time when setting the lock code validity end.
- **FR-008**: Buffer configuration options MUST only be visible in the options flow when a lock entry is configured (consistent with existing lock-related option visibility).
- **FR-009**: System MUST migrate existing configurations from version 9 to version 10, adding both buffer options with default value 0.
- **FR-010**: When buffer values are changed, existing active lock code slots MUST be updated with the new buffer-adjusted date ranges on the next coordinator refresh cycle (lazy update).
- **FR-011**: When a guest arrives within the before-buffer window and uses their lock code, the check-in sensor MUST correctly transition from "awaiting check-in" to "checked in" upon receiving the lock state change event. This behavior requires Keymaster monitoring to be enabled; in automatic check-in mode, the transition occurs at the unbuffered event start time per FR-005.

### Key Entities

- **Code Buffer Before**: An integration-level configuration value (non-negative integer, minutes) representing how far in advance of a reservation's start time the associated lock code should become valid.
- **Code Buffer After**: An integration-level configuration value (non-negative integer, minutes) representing how long after a reservation's end time the associated lock code should remain valid.
- **Lock Code Validity Window**: The effective start and end datetimes sent to Keymaster, calculated as the reservation's start/end times adjusted by the respective buffer values.

## Assumptions

- Keymaster correctly enforces the date range start/end values it receives; no additional validation of buffered times is needed on the Rental Control side.
- The lock hardware supports time precision to the minute, which aligns with the minute-granularity buffer configuration.
- Overlapping lock code validity windows for consecutive reservations are handled gracefully by Keymaster and the lock hardware (different codes for different slots).
- The lazy update approach (applying buffer changes on the next refresh cycle) is acceptable and consistent with existing patterns like trim_names behavior.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Property managers can configure before and after buffer times in under 1 minute through the existing options flow.
- **SC-002**: Lock codes activate exactly at the configured number of minutes before the reservation start time, accurate to the minute.
- **SC-003**: Lock codes deactivate exactly at the configured number of minutes after the reservation end time, accurate to the minute.
- **SC-004**: Existing users upgrading to the new version experience zero change in lock code timing behavior without any manual action.
- **SC-005**: All internal integration logic (calendar display, check-in sensors, event overrides, auto check-in/checkout timers) continues to use unbuffered reservation times, with no observable side effects from buffer configuration.
- **SC-006**: Changes to buffer values are reflected in active lock code slots within one coordinator refresh cycle.
- **SC-007**: Guests arriving within the before-buffer window can use their lock code and are correctly recorded as checked in.
