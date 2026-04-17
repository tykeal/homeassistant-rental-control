<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Honor PMS Calendar Event Times

**Feature Branch**: `007-honor-pms-times`
**Created**: 2025-07-22
**Status**: Draft
**Input**: User description: "Honor PMS Calendar Event Times — add option to use calendar-provided check-in/check-out times instead of stored override times, allowing PMS time changes to flow through to Keymaster"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — PMS Time Changes Flow Through to Lock Codes (Priority: P1)

A property manager uses a PMS (e.g., Guesty) that sets actual check-in and check-out times in calendar reservations. A guest requests an early check-in, and the PMS updates the reservation's start time accordingly. With the "Honor event times" option enabled, the integration detects that the calendar's check-in time differs from the previously stored time and pushes the updated time to Keymaster so the lock code becomes active at the new earlier time.

**Why this priority**: This is the core value of the feature — without it, PMS time updates are silently ignored and guests may arrive to find their lock code is not yet active.

**Independent Test**: Can be fully tested by enabling "Honor event times," modifying a reservation's start time in a test calendar, triggering a calendar refresh, and verifying that the stored override times are updated and a Keymaster time-update event fires.

**Acceptance Scenarios**:

1. **Given** "Honor event times" is enabled and a reservation with explicit check-in time 15:00 is already assigned to a Keymaster slot with stored override time 15:00, **When** the PMS updates the reservation's check-in time to 13:00 and a calendar refresh occurs, **Then** the system detects the time difference and pushes the updated 13:00 check-in time to Keymaster.
2. **Given** "Honor event times" is enabled and a reservation with explicit check-out time 11:00 is already assigned to a Keymaster slot, **When** the PMS extends checkout to 14:00 (late checkout) and a calendar refresh occurs, **Then** the system detects the time difference and pushes the updated 14:00 checkout time to Keymaster.
3. **Given** "Honor event times" is enabled and a reservation with explicit times is already assigned to a Keymaster slot, **When** a calendar refresh occurs and the PMS times have not changed, **Then** the system does not fire a time-update event (no unnecessary updates).

---

### User Story 2 — New Configuration Option in Options Flow (Priority: P1)

A property manager wants to control whether the integration honors PMS-provided event times or continues using stored override times (the current behavior). They navigate to the integration's options flow and see a new "Honor event times" toggle. This toggle is available regardless of whether Keymaster is configured, since it affects how event times are displayed even without Keymaster.

**Why this priority**: The configuration toggle is essential for the feature to be user-controllable and for backward compatibility to be preserved.

**Independent Test**: Can be fully tested by opening the integration's options flow, toggling "Honor event times" on and off, and confirming the setting persists across restarts.

**Acceptance Scenarios**:

1. **Given** the integration is configured (with or without Keymaster), **When** the user opens the integration's options flow, **Then** a "Honor event times" toggle is displayed with a default value of off (disabled).
2. **Given** the user enables "Honor event times" in the options flow and saves, **When** the integration reloads, **Then** the setting persists and the integration uses calendar-provided times for events with explicit times.
3. **Given** the user disables "Honor event times" (or it was never enabled), **When** a calendar refresh occurs, **Then** the integration continues to use stored override times for events already assigned to slots (current behavior preserved).

---

### User Story 3 — All-Day Events Fall Back to Default Times (Priority: P2)

A property manager uses a PMS that creates all-day calendar events (without explicit check-in/check-out times). Even with "Honor event times" enabled, the integration correctly falls back to the configured default check-in and check-out times for these all-day events, just as it does today.

**Why this priority**: This ensures the feature does not break existing behavior for PMS systems that use all-day events, which represents a large portion of current users.

**Independent Test**: Can be fully tested by enabling "Honor event times," creating an all-day reservation event (no explicit times), and verifying the configured default check-in/check-out times are applied.

**Acceptance Scenarios**:

1. **Given** "Honor event times" is enabled and an all-day calendar event has no explicit start/end times, **When** the event is processed during a calendar refresh, **Then** the system uses the configured default check-in and check-out times.
2. **Given** "Honor event times" is enabled and an all-day event was previously assigned to a Keymaster slot with stored override times, **When** a calendar refresh occurs, **Then** the system uses the stored override times (preserving the existing behavior for all-day events with overrides).
3. **Given** "Honor event times" is disabled and a calendar event has explicit times, **When** the event is already assigned to a Keymaster slot, **Then** the system uses the stored override times (current behavior, not the calendar times).

---

### Edge Cases

- What happens when a PMS changes a reservation from having explicit times to being an all-day event (or vice versa) between calendar refreshes? The system should handle the transition gracefully: if "Honor event times" is enabled and the event transitions from explicit times to all-day, the system falls back to default times or stored override times; if it transitions from all-day to explicit times, the system uses the new explicit times.
- What happens when the calendar source temporarily returns malformed time data for a reservation? The system should treat events with unparseable times as all-day events and fall back to defaults, logging a warning.
- What happens when "Honor event times" is enabled mid-session while events are already assigned to Keymaster slots? On the next calendar refresh, events with explicit times will use the calendar times; if those differ from stored overrides, a time-update event fires. This is the expected behavior — the user is opting into calendar-sourced times.
- What happens when multiple calendar refreshes occur in rapid succession with the same PMS time change? After the first refresh detects the change and updates the override, subsequent refreshes see matching times and do not fire duplicate updates.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The integration MUST provide a boolean configuration option labeled "Honor event times" in the options flow, defaulting to disabled (off).
- **FR-002**: The "Honor event times" option MUST be visible in the options flow regardless of whether Keymaster integration is configured.
- **FR-003**: When "Honor event times" is enabled and a calendar event has explicit start/end times, the system MUST use the calendar's actual times instead of any previously stored override times when building event data.
- **FR-004**: When "Honor event times" is enabled and a calendar event is an all-day event (no explicit times), the system MUST fall back to stored override times if an override exists, or to the configured default check-in/check-out times if no override exists.
- **FR-005**: When "Honor event times" is disabled, the system MUST preserve the current time resolution behavior: use stored override times when an override exists, then calendar explicit times, then configured defaults.
- **FR-006**: When a time difference is detected between the calendar-provided times and the stored override times, the existing time-update mechanism MUST propagate the new times to Keymaster without requiring any new Keymaster integration code.
- **FR-007**: The system MUST NOT fire time-update events when the calendar-provided times match the stored override times (no unnecessary updates).
- **FR-008**: The "Honor event times" setting MUST persist across integration reloads and Home Assistant restarts.

### Key Entities

- **EventOverride**: Stores the slot name, slot code, and start/end times for a reservation assigned to a Keymaster slot. When "Honor event times" is enabled, the override's stored times are superseded by calendar-provided times for events with explicit times, but remain authoritative for all-day events.
- **Configuration Option ("Honor event times")**: A boolean flag stored in the integration's options. Controls whether the event-building logic prioritizes calendar-provided times over stored override times.

## Assumptions

- PMS systems that provide explicit check-in/check-out times embed them in the iCal DTSTART and DTEND fields with time components (not date-only).
- All-day events are identifiable by their DTSTART/DTEND values being date-only (no time component), which is already handled by the existing exception path in the time resolution logic.
- The existing time-comparison and time-update mechanism works correctly and does not need modification — it already detects time differences and pushes updates to Keymaster.
- The existing "should update code" option remains orthogonal: it controls whether lock codes are regenerated after a time change is detected, while the new option controls whether time changes are detected in the first place.
- The options flow already supports boolean toggles (e.g., the existing "should update code" option), so the new option follows the same pattern.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When "Honor event times" is enabled and a PMS updates a reservation's check-in or check-out time, the updated time is reflected in the Keymaster slot within one calendar refresh cycle.
- **SC-002**: When "Honor event times" is disabled, the system behaves identically to the current release — no regressions in existing time resolution logic.
- **SC-003**: All-day events continue to use default check-in/check-out times (or stored override times) regardless of the "Honor event times" setting — zero all-day event regressions.
- **SC-004**: No duplicate or unnecessary time-update events are fired when calendar times match stored override times.
- **SC-005**: The configuration option is accessible and modifiable through the integration's options flow without requiring the integration entry to be re-created.
