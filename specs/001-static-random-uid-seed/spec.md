# Feature Specification: Static Random Seed from iCal UID

**Feature Branch**: `001-static-random-uid-seed`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Switch the static_random code generator seed from the event description to the iCal UID"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stable Door Codes Across Description Changes (Priority: P1)

As a property manager using the static_random door code generator, I want the generated door code for a reservation to remain stable even when the calendar event description changes (e.g., guest adds a note, property management system updates booking details), so that I don't have to re-program locks or re-communicate codes to guests.

**Why this priority**: This is the core problem being solved. Description edits silently rotating door codes can cause guest lockouts and property manager confusion. Seeding from the iCal UID — which is immutable per event — eliminates this class of failures entirely.

**Independent Test**: Can be fully tested by creating a sensor with a known UID, generating a door code, changing the description, and verifying the code remains identical. Delivers the primary value of code stability.

**Acceptance Scenarios**:

1. **Given** a calendar event with a UID and a description, **When** the static_random generator produces a door code, **Then** the code is seeded from the UID (not the description).
2. **Given** a calendar event with UID "abc-123" and description "Guest: Alice", **When** the description changes to "Guest: Alice - early checkin", **Then** the generated door code remains exactly the same.
3. **Given** a calendar event with a UID, **When** the door code is generated multiple times across different update cycles, **Then** the same code is produced every time (deterministic).

---

### User Story 2 - Graceful Fallback for Calendars Without UIDs (Priority: P2)

As a property manager whose calendar source does not provide iCal UIDs (or provides them inconsistently), I want the door code generator to fall back to the previous description-based seeding, so that I still get deterministic codes rather than errors or empty codes.

**Why this priority**: Not all calendar providers include UIDs. Without a fallback, the feature would break for those users. This ensures backward compatibility for edge-case calendar sources.

**Independent Test**: Can be fully tested by creating a sensor with a `None` UID and a valid description, generating a door code, and verifying it matches the legacy description-seeded behavior.

**Acceptance Scenarios**:

1. **Given** a calendar event where UID is not available (None), **When** the static_random generator produces a door code, **Then** the code is seeded from the event description (legacy behavior).
2. **Given** a calendar event where UID is not available and the description is also None, **When** the door code generator runs, **Then** the system falls back to the date-based code generator (existing fallback behavior).

---

### User Story 3 - UID Available as Event Attribute (Priority: P3)

As a property manager or automation author, I want the iCal UID to be stored and exposed as an event attribute on each calendar sensor, so that I can reference it in automations or diagnostics and verify which UID is associated with a reservation.

**Why this priority**: Exposing the UID as an attribute is a prerequisite for P1 (it must be stored to be used as a seed) and provides additional transparency for users who want to inspect or use the UID in their own automations.

**Independent Test**: Can be fully tested by loading a calendar event with a known UID and verifying the UID appears in the sensor's event attributes.

**Acceptance Scenarios**:

1. **Given** a calendar event with UID "abc-123", **When** the sensor updates its event attributes, **Then** the attribute "uid" is set to "abc-123".
2. **Given** a calendar event without a UID, **When** the sensor updates its event attributes, **Then** the attribute "uid" is set to None.

---

### Edge Cases

- What happens when a calendar event's UID changes between fetches (e.g., re-created event with same summary/dates)? The door code will change, which is expected since it represents a new event.
- What happens when a calendar switches from not providing UIDs to providing them (e.g., calendar provider update)? The door code will change once as the seed source transitions from description to UID. This is expected and acceptable.
- What happens when multiple events share the same UID (recurring event instances)? Each instance should still produce a deterministic code based on the shared UID. If distinct codes are needed per instance, the existing date-based generator should be used instead.
- What happens during the upgrade from the previous version? Existing static_random users will experience a one-time code rotation because the seed value changes from description to UID. This is a known breaking change that must be documented.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST store the iCal UID from each calendar event as an event attribute during sensor event updates.
- **FR-002**: System MUST seed the static_random door code generator from the event's UID when the UID is available (not None).
- **FR-003**: System MUST fall back to seeding the static_random generator from the event description when the UID is None.
- **FR-004**: System MUST continue to fall back to the date-based code generator when both UID and description are None (preserving existing fallback behavior).
- **FR-005**: System MUST produce the same door code for a given UID regardless of how many times the code generation runs or how many times the event description changes.
- **FR-006**: System MUST produce different door codes for different UIDs (within the statistical expectations of the code space).
- **FR-007**: System MUST respect the configured code length when generating codes from the UID seed, exactly as it does today with the description seed.

### Key Entities

- **Calendar Event**: A reservation/booking from an iCal source. Key attributes: summary, start, end, location, description, uid. The uid is an immutable identifier assigned by the calendar source.
- **Event Attributes**: The set of sensor attributes derived from a calendar event. Currently includes summary, start, end, location, description, and ETA fields. Will be extended to include uid.
- **Door Code**: A numeric code of configurable length generated deterministically from event data. Used to program smart locks for guest access.

## Assumptions

- The iCal UID is immutable for a given calendar event over its lifetime. This is guaranteed by the iCalendar specification (RFC 5545), which states the UID is a globally unique identifier that persists for the life of the event.
- Calendar providers that supply UIDs do so consistently (i.e., a provider won't intermittently omit the UID for the same event across fetches).
- The one-time code rotation on upgrade is an acceptable trade-off for long-term code stability. Users will need to re-program any active door codes after upgrading.
- The existing fallback chain (static_random → date_based when description is None) is preserved and extended to include UID in the priority order: UID → description → date_based.

## Breaking Changes

This feature is a **BREAKING CHANGE** for existing users of the static_random code generator:

- **Impact**: All currently active door codes generated by static_random will rotate once upon upgrade because the seed value changes from the event description to the event UID.
- **Affected users**: Only users configured with the `static_random` code generator. Users of `date_based` or `last_four` generators are not affected.
- **Mitigation**: Users should plan to update any active lock codes after upgrading. The change should be clearly documented in release notes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Door codes generated with static_random remain identical when only the event description changes, for 100% of events that have a UID.
- **SC-002**: Door codes for events without UIDs produce the same results as the previous version (full backward compatibility for the fallback path).
- **SC-003**: The iCal UID is visible as an event attribute on every calendar sensor that has a UID-bearing event.
- **SC-004**: All existing door code generation tests continue to pass (with updates to reflect the new seed source), confirming no regressions in code length, format, or determinism.
- **SC-005**: The upgrade path is documented, and the one-time code rotation is called out in release notes so that 100% of users can plan for the transition.
