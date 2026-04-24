# Data Model: Static Random Seed from iCal UID

**Feature**: 001-static-random-uid-seed
**Date**: 2026-04-24

## Entities

### CalendarEvent (HA-provided dataclass — UNCHANGED)

The `homeassistant.components.calendar.CalendarEvent` dataclass already includes a `uid` field. The coordinator already populates it from the raw iCal `UID` property. **No changes to this entity.**

| Field       | Type             | Source          | Notes                        |
|-------------|------------------|-----------------|------------------------------|
| summary     | str              | iCal SUMMARY    | Event title                  |
| start       | datetime         | iCal DTSTART    | Timezone-aware               |
| end         | datetime         | iCal DTEND      | Timezone-aware               |
| location    | str \| None      | iCal LOCATION   | Optional                     |
| description | str \| None      | iCal DESCRIPTION| Optional                     |
| uid         | str \| None      | iCal UID        | **Already populated** — globally unique, immutable per RFC 5545 |

### Sensor Event Attributes (`_event_attributes` dict — MODIFIED)

The `RentalControlCalSensor._event_attributes` dict is the set of state attributes exposed on each calendar sensor entity. This is the primary data model being changed.

#### Current State

| Attribute   | Type             | Source                | Notes                    |
|-------------|------------------|-----------------------|--------------------------|
| summary     | str              | CalendarEvent.summary | Event title              |
| description | str \| None      | CalendarEvent.description | Event body          |
| location    | str \| None      | CalendarEvent.location | Venue address           |
| start       | datetime \| None | CalendarEvent.start   | Check-in time            |
| end         | datetime \| None | CalendarEvent.end     | Check-out time           |
| eta_days    | int \| None      | Computed              | Days until check-in      |
| eta_hours   | int \| None      | Computed              | Hours until check-in     |
| eta_minutes | int \| None      | Computed              | Minutes until check-in   |
| slot_name   | str \| None      | Computed              | Lock slot display name   |
| slot_code   | str \| None      | Generated             | Door code for this event |

#### New State (after this feature)

| Attribute   | Type             | Source                | Notes                    |
|-------------|------------------|-----------------------|--------------------------|
| summary     | str              | CalendarEvent.summary | Event title — unchanged  |
| description | str \| None      | CalendarEvent.description | Event body — unchanged |
| location    | str \| None      | CalendarEvent.location | Venue address — unchanged |
| start       | datetime \| None | CalendarEvent.start   | Check-in time — unchanged |
| end         | datetime \| None | CalendarEvent.end     | Check-out time — unchanged |
| **uid**     | **str \| None**  | **CalendarEvent.uid** | **NEW — iCal UID, immutable event identifier** |
| eta_days    | int \| None      | Computed              | Days until check-in — unchanged |
| eta_hours   | int \| None      | Computed              | Hours until check-in — unchanged |
| eta_minutes | int \| None      | Computed              | Minutes until check-in — unchanged |
| slot_name   | str \| None      | Computed              | Lock slot display name — unchanged |
| slot_code   | str \| None      | Generated             | Door code — **seed source changes** |

### Door Code Generation Logic (behavioral change)

#### Current Seed Priority

```
static_random generator:
  description is None? → fall back to date_based
  description is not None? → random.seed(description)
```

#### New Seed Priority

```
static_random generator:
  uid is not None? → random.seed(uid)
  description is not None? → random.seed(description)   # legacy fallback
  both None? → fall through to date_based                # existing fallback
```

### Validation Rules

| Rule | Applies To | Description |
|------|-----------|-------------|
| UID immutability | CalendarEvent.uid | Guaranteed by RFC 5545; not enforced by this integration |
| UID uniqueness | CalendarEvent.uid | Globally unique per calendar source; not validated by this integration |
| Code length | slot_code | Must match configured `code_length` (default: 4). Enforced by `_generate_door_code()` via `.zfill(code_length)` |
| Code format | slot_code | Numeric digits only. Enforced by `random.randrange()` + `str()` + `.zfill()` |

### State Transitions

No state machine changes. The door code generation is stateless — computed fresh on each coordinator update cycle based on current event attributes.

**Breaking change**: When upgrading, `slot_code` values for `static_random` users will change once as the seed transitions from description to UID. This is a one-time transition, not an ongoing state change.

## Relationships

```
CalendarEvent (from coordinator)
    │
    ├── uid ─────────────────────► _event_attributes["uid"]     (NEW: exposed as sensor attribute)
    │                                   │
    │                                   ▼
    │                              _generate_door_code()
    │                                   │
    │                              priority: uid > description > date_based
    │                                   │
    │                                   ▼
    ├── description ─────────────► _event_attributes["description"]
    │                                   │
    │                                   ▼ (fallback seed)
    │                              _generate_door_code()
    │
    ├── start/end ───────────────► _event_attributes["start"/"end"]
    │                                   │
    │                                   ▼ (final fallback)
    │                              _generate_door_code() → date_based
    │
    └── summary/location ────────► _event_attributes (display only)
```
