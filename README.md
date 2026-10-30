<!--- SPDX-License-Identifier: Apache-2.0 -->
<!--- SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org. -->

# Rental Control management for Home Assistant

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/tykeal/homeassistant-rental-control/main.svg)](https://results.pre-commit.ci/latest/github/tykeal/homeassistant-rental-control/main)

Rental Control is a Home Assistant integration that handles custom calendars
and sensors for managing rental properties.

# Table of Contents

- [Features](#features)
    - [Calendar Management](#calendar-management)
    - [Entities Created](#entities-created)
    - [Check-in Tracking](#check-in-tracking)
    - [Keymaster Monitoring Switch](#keymaster-monitoring-switch)
    - [Early Checkout Expiry Switch](#early-checkout-expiry-switch)
    - [Manual Checkout Action](#manual-checkout-action)
    - [Home Assistant Events](#home-assistant-events)
    - [Door Code Generation](#door-code-generation)
    - [Keymaster Integration](#keymaster-integration)
        - [Slot Name Trimming](#slot-name-trimming)
        - [Lock Code Buffer Times](#lock-code-buffer-times)
        - [Keymaster Event Diagnostics](#keymaster-event-diagnostics)
    - [Using Door Codes Without Keymaster](#using-door-codes-without-keymaster)
- [Installation](#installation)
    - [Keymaster (optional)](#keymaster-optional)
    - [Calendar Triggers in Home Assistant 2026.11](#calendar-triggers-in-home-assistant-202611)
- [Setup](#setup)
- [Reconfiguration](#reconfiguration)
- [Downloading Diagnostics](#downloading-diagnostics)
- [Known issues](#known-issues)
- [Frequently Asked Questions](#frequently-asked-questions)
    - [Why does my calendar events say `Reserved` instead of the guest's name?](#why-does-my-calendar-events-say-reserved-instead-of-the-guests-name)
    - [Where can I find my rental calendar's `ics` URL?](#where-can-i-find-my-rental-calendars-ics-url)
    - [How do I use custom calendars?](#how-do-i-use-custom-calendars)
    - [Automation Examples](#automation-examples)
- [Development & Testing](#development--testing)

## Features

### Calendar Management

- Ingests ICS calendars from any HTTPS source that serves a text/calendar file
- Configurable refresh rate from as often as possible to once per day (default
  every 2 minutes)
- Define checkin/checkout times that apply to all-day calendar entries
- Ability to ignore 'Blocked' and 'Not available' events
- Ability to treat a calendar as confirmed bookings only, for feeds like
  Booking.com that no longer name their guests
- Calendars can have their own timezone definition that is separate from the
  Home Assistant instance itself. This is useful for managing properties that
  are in a different timezone from where Home Assistant is
- Events can have a custom prefix to help differentiate between entities if
  you track more than one calendar in an instance
- Submitting a configuration change forces a calendar refresh
- Calendar fetch failure resilience: if a fetch fails the integration
  serves the last good data; if a fetch returns an empty calendar when
  events existed before, it tolerates up to 2 consecutive empty
  responses before allowing the event list to clear

### Entities Created

For a calendar named **My Calendar** the integration creates the following
entities. Entity IDs follow the pattern `<domain>.<device_name>_<entity_name>`:

- **Event sensors** (one per sensor count):
    - `sensor.my_calendar_rental_control_event_0`
    - `sensor.my_calendar_rental_control_event_1`
    - `sensor.my_calendar_rental_control_event_2`
    - (...)
- **Calendar** entity for use with calendar cards:
    - `calendar.my_calendar_rental_control`
- **Check-in tracking sensor** (always created):
    - `sensor.my_calendar_check_in`
- **Keymaster Monitoring switch** (when Keymaster lock configured):
    - `switch.my_calendar_keymaster_monitoring`
- **Early Checkout Expiry switch** (when Keymaster lock configured):
    - `switch.my_calendar_early_checkout_expiry`

> **Note:** Existing installs keep their original entity IDs. Home
> Assistant caches entity IDs in its entity registry, so only new
> integrations get the updated naming pattern.

- Each event sensor has dynamically added attributes extracted from the event
  description when available:
    - `last_four` -- the last 4 digits of the phone number of the booking guest
    - `number_of_guests` -- the number of guests in the reservation
    - `guest_email` -- the email address of the booking guest
    - `phone_number` -- the phone number of the booking guest
    - `reservation_url` -- the URL to the reservation
    - `booking_id` -- the booking / reservation identifier
    - Other `Field: Value` lines in the description that the above dedicated
      extractors do not capture become attributes with slugified keys
      (e.g., `Check-In Time: 3:00 PM` becomes `check_in_time`)

### Check-in Tracking

The integration creates a **check-in tracking sensor** for each configured
rental that monitors guest occupancy through a four-state state machine.
Configuring a Keymaster lock enables lock-related transitions and behaviors:

| State | Description |
| --- | --- |
| `no_reservation` | No relevant calendar event |
| `awaiting_checkin` | Event identified, waiting for guest arrival |
| `checked_in` | Guest has arrived |
| `checked_out` | Guest has departed, post-checkout linger active |

The sensor declares the **enum** device class so Home Assistant and other
integrations know the full set of valid states.

**Automatic transitions:**

- `no_reservation` → `awaiting_checkin`: when the coordinator picks up a new
  calendar event
- `awaiting_checkin` → `checked_in`: at the configured check-in time
  (automatic) **or** when the guest uses their door code (requires keymaster
  monitoring)
- `checked_in` → `checked_out`: at the configured check-out time (automatic)
  **or** via the manual checkout action
- `checked_out` → `no_reservation` / `awaiting_checkin`: after the cleaning
  window expires (transitions to `awaiting_checkin` if a same-day follow-on
  reservation exists)

**Sensor attributes:**

- `checkin_state`, `summary`, `start`, `end`, `guest_name`
- `checkin_source` (`automatic` or `keymaster`)
- `checkout_source` (`automatic` or `manual`)
- `checkout_time`, `next_transition`

The check-in sensor state **persists across Home Assistant restarts** and
the integration validates stale states on startup automatically.

**Keymaster event diagnostics:** When enabled in the options flow, the
check-in sensor gains a `keymaster_event_diagnostics` attribute
containing a ring buffer of the last 10
`keymaster_lock_state_changed` events seen by the integration. Each
entry includes the timestamp, lock name, slot number, state, and
disposition (e.g., `accepted`, `rejected_state`, `rejected_slot_zero`,
`rejected_out_of_range`). The integration only records events from
monitored locks. This feature defaults to off; enable it in the
options flow.

**Debug action:** The integration provides a `rental_control.set_state`
service action for testing and development. It forces the check-in sensor into
any valid state (`no_reservation`, `awaiting_checkin`, `checked_in`,
`checked_out`). Use this for debugging only — it bypasses the normal state
machine transitions.

### Keymaster Monitoring Switch

When you configure a Keymaster lock the integration creates a **Keymaster
Monitoring** switch entity. Turning it **on** makes Keymaster unlock events on
the configured lock trigger an immediate check-in transition (the sensor moves
from `awaiting_checkin` to `checked_in` the moment the guest uses their door
code). When **off** (the default), time-based automatic check-in applies.

The integration automatically discovers **child locks** associated with the
configured Keymaster parent lock. It monitors unlock events on any child lock
identically to the parent, so a guest unlocking a secondary lock (e.g., a
Z-Wave deadbolt paired with a relay lock) also triggers check-in.

### Early Checkout Expiry Switch

When you configure a Keymaster lock the integration also creates an **Early
Checkout Expiry** switch entity. Turning it **on** and then performing a
manual checkout via the checkout action shortens the lock code expiry time to
the current time plus a 15-minute grace period instead of the original
reservation end time. This prevents a departed guest from re-entering the
property.

### Manual Checkout Action

The integration provides a `rental_control.checkout` service action for use
in automations or the developer tools. It transitions the check-in sensor
from `checked_in` to `checked_out`. The sensor must be in the `checked_in`
state and the current time must fall within the active reservation window.

### Force Reissue Action

The `rental_control.force_reissue` service action lets an operator rotate one
reservation or managed lock slot through the shared code allocator. Target
exactly one reservation with `entity_id`, or target any managed slot with both
`lockname` and `slot`; slot targeting attaches the live reservation when one
occupies the slot and otherwise clears a ghost code. You must pass the optional `force` flag when the
reservation has already checked in, because rotating the code revokes a
credential the guest may be holding. Use `dry_run` to preview the replacement
code without changing locks, sensors, or the shared registry.

For lock-backed entries, the reservation sensor continues to publish the last
confirmed lock code until the physical slot reports the replacement write. If
Home Assistant restarts after accepting a request but before the next refresh
consumes it, the in-memory request lapses; invoke the service again.
Any already-created replaced-code hold remains in the registry, and each
refresh retries it until the allocator can safely release it.

### Unreadable Lock Slots

The integration refuses to issue new door codes while any managed Keymaster
slot stays unreadable — a slot whose Keymaster PIN or name entity reports
`unavailable` or `unknown`, or no longer exists at all. Such a slot may still
hold a working guest credential that the integration cannot see, so issuing into that uncertainty risks a code collision or
clobbers a credential a guest relies on. The behaviour deliberately fails
closed.

In that situation the integration raises a persistent notification naming the
config entry, the lock, and the offending slot numbers, and logs a matching
warning. The integration dismisses that notification as soon as every managed
slot reports its state again.

Watch the scope:

- When a slot goes unreadable before Home Assistant starts, or before the
  affected entry finishes one clean adoption cycle, the integration withholds
  codes for **all** Rental Control entries, not just the affected one. Every
  entry shares one door code space, so the integration must account for every
  entry before it can safely issue any code. You may then also see a
  `code adoption pending` notification that lists the entries still waiting,
  annotated with their unreadable slot numbers.
- When a slot goes unreadable later, after that entry already adopted its
  codes cleanly, only that entry stops receiving codes.

**Remedy:** restore readable Keymaster entities for the named slot, so that
its PIN and name entities report a real state again. Deleting those Keymaster
entities does **not** clear the condition: Rental Control still watches every
slot in its configured range and treats a slot with missing entities as
unknown, which keeps the freeze in place. When the slot disappears permanently,
reconfigure this entry's start slot or event count so that the managed range
no longer covers it. Once the slot reads correctly, or leaves the
managed range, the next refresh resumes issuance and clears the notification.
When the slot is permanently dead and you cannot reshape the managed range,
exclude it with the slot exclusion action described below.

### When the Integration Withholds Issuance

An unreadable slot is not the only thing that can stop issuance. A readable
managed slot holding a code that matches no current reservation also fails the
gate closed, because the integration will not issue codes while it cannot
account for a code already on the lock.

Whenever the integration withholds issuance, it logs a single warning naming
the config entry, the lock, every slot holding the gate shut, and the reason for
each slot — `unreadable` or `holds a code that matches no current reservation`.
The warning repeats only when the set of blocking slots changes, and an
informational line records the moment that entry's gate reopens. Issuance then
resumes on the next refresh, unless another entry has yet to finish its first
adoption cycle; until every entry has adopted once, no entry issues codes,
because all entries share one door code space.

An unmatched code usually belongs to a booking that has gone away, and clears
itself once Keymaster finishes clearing the slot. If it persists, clear that
code at the lock.

### Slot Exclusion Action

The `rental_control.set_slot_exclusion` service action tells one entry to stop
counting one unreadable slot against the issuance gate, which restores door
code issuance without reshaping the managed range. Target the entry with
`entity_id` or with `lockname`, and name the slot with `slot`. Pass
`excluded: false` to withdraw an exclusion, `reason` to record why you made
it, and `dry_run: true` to see what the action would decide before it decides
it.

The action refuses, and tells you why, when:

- the slot sits outside the entry's managed range,
- the slot currently reads correctly, or
- the entry's own allocator records already claim the slot.

No override exists for any of these refusals. The readable-slot refusal is
what keeps you from excluding a slot that holds a working guest credential.

An exclusion is a standing statement about one slot on one entry, and the
integration re-checks it every refresh. When the slot reads correctly again,
or leaves the managed range, the integration drops the exclusion and dismisses
its notification; nothing you need to remember to undo. While an exclusion
stands, the integration raises a persistent notification naming the entry, the
lock, the slot, and your recorded reason, in place of the unreadable-slot
notification for that slot.

Accept the residual risk before you use it: an excluded slot may still hold a
code the integration cannot read, so a newly issued code could duplicate it.
The integration keeps treating the slot as blocked and never writes to it or
clears it.

Exclusions live in the config entry's stored data rather than in its options,
so **saving the options flow for an entry drops every exclusion it holds** and
returns that entry to the fail-closed behaviour. Invoke the action again if
the slot is still dead.

### Home Assistant Events

The integration fires events on the Home Assistant event bus for use in
automations:

| Event | Fired When |
| --- | --- |
| `rental_control_checkin` | Guest transitions to checked-in |
| `rental_control_checkout` | Guest transitions to checked-out |

Both events include attributes: `summary`, `guest_name`, `entity_id`, `start`,
`end`, and `source`.

### Door Code Generation

- Optional code length starting at 4 digits (requires even number of digits)
- 3 door code generators are available:
    - A check-in/out date based 4 digit (or greater) code using the check-in
      day combined with the check-out day (default and fallback in the case
      another generator fails to produce a code)
        - Codes optionally regenerate when the reservation start or end
          dates are at least 1 day in the future
    - A random 4 digit (or greater) code based on the event description
    - The last 4 digits of the phone number. This works when the event
      description contains '(Last 4 Digits): ' or 'Last 4 Digits: ' followed
      by a 4 digit number. This is the most stable generator, but requires
      the event descriptions to have the needed data. The previous two methods
      can have the codes change if the event makes changes to length or to
      the description.
- All events get a code associated with them. If the criteria to create the
  code are not fulfilled, the check-in/out date based method serves as
  a fallback

### Keymaster Integration

- Integration with [Keymaster](https://github.com/FutureTense/keymaster) to
  control door codes matched to the number of events tracked
- Automatic slot assignment with **deduplication**: the same guest reservation
  never occupies more than one lock code slot even when calendar data shifts
  between refreshes
- **Slot reconciliation**: the integration periodically verifies that all
  populated Keymaster slots match current calendar events. When a reservation
  gets cancelled or removed from the calendar, its slot clears automatically.
  Matching uses guest name, time-range overlap, and UID tiebreaker to handle
  edge cases like same-name guests with different date ranges
- Slot command retry with escalation: if a lock code set/clear command fails
  after 3 attempts the integration creates a persistent notification alerting
  the user to take manual action

#### Bookings-Only Calendars

Booking.com's iCal export no longer identifies guests. Every event
carries the title `CLOSED - Not available`, whether it covers a real
reservation or a date the host blocked. The integration normally skips
events summarised `Blocked` or `Not available`, because Airbnb, Google
Calendar and PMS feeds use those words for genuine blocks, so such a
feed produces no door codes at all.

- **`bookings_only_feed`** (boolean, default: on for `booking.com`
  calendar URLs, off for everything else) — assert that this calendar
  publishes confirmed bookings and nothing else

A Booking.com calendar defaults to bookings-only, whether you added it
before or after this option existed, so its door codes work without
any action from you. Turn the option off in the integration's options
if your feed also exports blocked dates: in Booking.com's extranet
that is the difference between exporting booked dates only and
exporting booked plus closed dates. Every other calendar defaults to
off and keeps the normal `Blocked`/`Not available` filtering.

Leaving the option on for a calendar that does publish blocked dates
issues guest door codes for those blocks, so only turn it on for a
calendar that never publishes them.

When the option is on and an event carries no usable name, the
integration names the slot after a short token derived from the
event's calendar `UID`, for example `Booking A3F9K2`. The token is a
stable hash, so the reservation number itself never reaches the lock,
and the name survives an amendment to the stay: when a booking's dates
move upstream, the reservation keeps its slot and its door code rather
than taking a fresh slot and a new code. A feed that publishes no
usable `UID` falls back to naming the slot after the stay, for example
`Booking 23 Oct - 25 Oct 2026`; such a name does shift with the dates.
The option also takes precedence over
"ignore events that are not standard reservations", which would
otherwise discard the same events.

#### Slot Name Trimming

Some locks (e.g., Schlage via Keymaster) impose a character limit
for slot names. The integration can optionally trim event names to fit
within a configured length on word boundaries. Enable this feature in
the options flow when you configure a Keymaster lock:

- **`trim_names`** (boolean, default: off) — enable slot name trimming
- **`max_name_length`** (integer, default: 16, min: 4) — character
  length cap for slot names

When enabled, the integration trims names on word boundaries. If a
single word exceeds the cap, the integration hard-truncates it. The
event prefix counts toward the limit.

#### Lock Code Buffer Times

Configurable pre/post buffer times control when lock codes activate
and deactivate relative to the reservation schedule:

- **`code_buffer_before`** (integer, default: 0) — minutes before
  check-in to activate the lock code
- **`code_buffer_after`** (integer, default: 0) — minutes after
  checkout to deactivate the lock code

The buffer **only** affects when the physical lock code is valid on
the lock. It does not change calendar event display times, check-in
sensor timing, event override matching, or auto check-in/checkout
scheduling. If a guest arrives during the buffer window and uses
their code, the check-in sensor correctly transitions to `checked_in`
(when you enable Keymaster monitoring).

#### Keymaster Event Diagnostics

An opt-in diagnostic attribute on the check-in tracking sensor records
the last 10 `keymaster_lock_state_changed` events seen by the
integration. Enable it in the options flow:

- **`enable_keymaster_event_diagnostics`** (boolean, default: off)

When enabled, the check-in sensor gains a
`keymaster_event_diagnostics` attribute. Each entry includes the
timestamp, lock name, slot number, state, and disposition. The
integration only records events from monitored locks.

- Custom calendars work as long as they provide a valid ICS file via an HTTPS
  connection.
    - Create rental events as all-day events (the way rental platforms provide
      them)
    - Create maintenance style events with explicit start and end times
    - The event Summary (aka event title) _may_ contain the word Reserved.
      This causes the slot name to generate in one of two ways:
        - When Reserved appears followed by ' - ' and then something else, the
          integration uses the part after the dash
        - When Reserved is _not_ followed by ' - ' then the full summary
          becomes the slot name
        - When the Summary contains nothing else _and_ the Details contain
          something that matches an Airbnb reservation identifier of
          `[A-Z][A-Z0-9]{9}` that is a capital alphabet letter followed by 9
          more characters that are either capital alphabet letters or numbers,
          then the slot will get this
        - If the Summary is _just_ Reserved and there is no Airbnb code in the
          Description, then the integration ignores the event for purposes of
          managing a lock code.
        - Any of the other supported platform event styles for the Summary
          work as long as the Summary conforms to the pattern.
        - The best Summary on a manual calendar is your guest name. The entries
          need unique names over the sensor count worth of events or Rental
          Control will run into issues.
    - The Description of the event can include extra details that the
      integration extracts into sensor attributes.
        - Phone numbers for generating door codes in one of two ways
            - A line in the Description matching this regular expression:
              `\(?Last 4 Digits\)?:\s+(\d{4})` -- This line always takes
              precedence for generating a door code based on last 4 digits.
            - A line in the Description matching this regular expression:
              `Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})` which will then
              have the "air" squeezed out of it to extract the last 4 digits
              in the number
        - Number of guests
            - A line in the Description that matches: `Guests:\s+(\d+)$`
            - The following lines also work and their values sum together:
                - `Adults:\s+(\d+)$`
                - `Children:\s+(\d+)$`
        - The integration extracts email addresses from the Description by
          matching against: `Email:\s+(\S+@\S+)`
        - Reservation URLs match against the first URL in the Description

### Using Door Codes Without Keymaster

An entry with no Keymaster lock still generates a door code for every
reservation and publishes it on the event sensors. Automations can use
it to drive a lock that Keymaster does not support. Rental Control
never touches such a lock: adding, changing and removing the code is
your automation's job, and the sensor attributes below are its only
input. This section describes what those automations can rely on.

Each event sensor carries these attributes. While the sensor shows
`No reservation`, every one except `summary` is empty (`None`).

| Attribute | Value |
| --- | --- |
| `start` | Timezone-aware datetime when the stay begins, in the calendar's configured timezone. All-day events use the configured check-in time |
| `end` | Timezone-aware datetime when the stay ends, likewise using the configured checkout time |
| `slot_name` | The guest's name as the integration derives it from the event; see [Bookings-Only Calendars](#bookings-only-calendars) for anonymous bookings |
| `slot_code` | The door code, a string of digits of the configured code length. It can start with `0`, so treat it as text |
| `slot_number` | Always empty on an entry without a lock; do not key anything on it |
| `uid` | The event's calendar `UID`, when the feed provides one |
| `summary`, `description`, `location`, `eta_days`, `eta_hours`, `eta_minutes` | Event details and the time until `start` |

The integration guarantees the following:

- **When `slot_code` is present.** From the first refresh after a
  reservation enters the calendar window until the stay ends, as long
  as the event yields a `slot_name`. An event that yields no slot name,
  such as a bare `Reserved` with no reservation code, never gets one.
- **When `slot_code` is absent.** At checkout the check-in sensor moves
  to `checked_out` (at the end time, or earlier through the manual
  checkout action), and from the next refresh the code may be empty
  while the event is still listed. When the integration declines to
  issue a code, the field is empty and the log carries a warning or
  error saying why. A refresh that fails outright keeps publishing the
  last codes it issued.
- **An empty `slot_code` is not a removal instruction.** It means the
  integration has no code to publish. Drive removal from the stay's
  `end`, or from the lock's own expiry, and cache the code yourself if
  your lock needs the value to delete it.
- **Stability.** A stay's code does not change between refreshes while
  the booking stays the same. A booking whose name, dates or
  description change can receive a new code, depending on the
  [code generator](#door-code-generation), so react to changes in
  `slot_code` rather than reading it once. For anonymous bookings, a
  `slot_name` derived from the calendar `UID` survives date changes;
  one derived from the dates does not.
- **Checkout-day retention.** An event stays in the event sensors and
  the calendar entity for the whole local day on which it ends, until
  local midnight. A noon checkout stays visible for twelve hours after
  `end`.
- **A cancelled booking disappears.** Once a reservation leaves the
  feed, its event leaves the sensors, so delete its code from your
  lock through your own records.

> **Warning:** an automation that fires a fixed delay after `end` never
> runs if `end` plus that delay crosses local midnight, because the
> event has gone by then. A 22:00 checkout leaves only two hours. Keep
> the delay short enough for your latest checkout, or record `end` when
> you program the code.

Events sort by start time, so on a turnover day the departing stay
remains in `event_0` until midnight and the arriving guest appears in
`event_1`. Watch every event sensor, and make your lock actions
idempotent per `slot_name`.

A sketch for a lock that accepts time-bounded codes. Replace
`script.lock_add_code` and `script.lock_remove_expired_codes` with the
actions your lock provides:

```yaml
- alias: Program guest door code
  mode: queued
  triggers:
    - trigger: state
      entity_id:
        - sensor.my_calendar_rental_control_event_0
        - sensor.my_calendar_rental_control_event_1
      attribute: slot_code
  conditions:
    - condition: template
      value_template: >
        {{ trigger.to_state.attributes.slot_code not in [none, ''] }}
  actions:
    - action: script.lock_add_code
      data:
        name: "{{ trigger.to_state.attributes.slot_name }}"
        code: "{{ trigger.to_state.attributes.slot_code }}"
        start: "{{ trigger.to_state.attributes.start }}"
        end: "{{ trigger.to_state.attributes.end }}"

- alias: Remove expired guest door codes
  triggers:
    - trigger: template
      value_template: >
        {% set end = state_attr('sensor.my_calendar_rental_control_event_0',
                                'end') %}
        {{ end is not none and now() >= end + timedelta(hours=3) }}
  actions:
    - action: script.lock_remove_expired_codes
```

## Installation

Rental Control is in the default catalog of the Home Assistant Marketplace
(Home Assistant 2026.11 and later) and of HACS.

1. In Home Assistant, go to **Settings** > **Marketplace**, or press the
   following button [![Open your Home Assistant instance and open a
repository inside the Home Assistant Marketplace or
HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tykeal&repository=homeassistant-rental-control&category=integration)
1. Search for `Rental Control` and install it
1. Rental Control has a config flow, so it goes straight to setup with no
   restart. Select **Add Integration** and follow the [Setup](#setup)
   section to finish the configuration

On Home Assistant before 2026.11, install through
[HACS](https://hacs.xyz/) instead: the button above opens HACS on those
versions. After installing, restart Home Assistant, then follow the
[Setup](#setup) section.

### Keymaster (optional)

[Keymaster](https://github.com/FutureTense/keymaster) is optional. Rental
Control needs it only to program lock slots automatically; see
[Keymaster Integration](#keymaster-integration) and, for Booking.com
feeds, [Bookings-Only Calendars](#bookings-only-calendars). Without
Keymaster, the integration still provides calendars, sensors and door
codes, for example to drive a lock from a blueprint.

### Calendar Triggers in Home Assistant 2026.11

From Home Assistant 2026.11, `calendar.event_started` and
`calendar.event_ended` triggers no longer default to
`offset_type: before`; the sign of the offset now sets the direction.
Check any YAML automation on a Rental Control calendar that uses an
offset without `offset_type`. The change does not affect triggers you
created in the UI. See the
[2026.11 release notes](https://rc.home-assistant.io/blog/2026/10/28/release-202611/)
for details.

## Setup

Set up the integration through the GUI.

- Press the following button to install the `Rental Control` integration
  [![Open your Home Assistant instance and start setting up a new
integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=rental_control)
- Follow the prompts and then press `OK` on the question about installing
  `Rental Control`
- Enter a name for the calendar, and the calendar's `ics` URL (see FAQ)
- By default the integration creates 5 sensors for the 5 next upcoming events
  (sensor.rental_control\_\<calendar_name\>\_event_0 ~ 4). You can adjust this
  to add more or fewer sensors
- The calendar refresh rate defaults to every 2 minutes but you can set it to
  0 for as often as possible (about every 30 seconds) or up to once per day
  (1440). This adjusts in minute increments
- The integration considers events with a start time up to 365 days (1 year)
  into the future by default. You can also adjust this when adding a new
  calendar
- Set your checkin and checkout times. All times use 24 hour format. These
  times apply to all-day calendar events. If the events come in with times
  already attached, the integration uses those times instead of the defaults
- **Honor event times**: enable this option if your calendar source (e.g., a
  PMS like Guesty) provides actual check-in/check-out times in the events.
  When enabled, the integration always uses the calendar-provided times and
  pushes any time changes to Keymaster. When disabled (the default), times
  stored in Keymaster slots take precedence after initial assignment
- The cleaning window (default 6 hours) controls how long the check-in sensor
  stays in the `checked_out` state after a guest departs before transitioning
  to `no_reservation` or `awaiting_checkin` for the next guest. You can set
  this from 0.5 to 48 hours
- For configuration managing a Keymaster controlled lock, make sure that you
  have defined the lock during initial setup and that you have the starting
  slot set for the integration.

    - Make sure you have Keymaster fully working before trying to use the slot
      management component of Rental Control.
    - **NOTE:** Rental Control takes full control of the Keymaster slots
      defined for management. Any data in the slots gets overwritten by Rental
      Control when it takes over the slot unless it matches event data for the
      calendar.
    - The following portions of a Keymaster slot will influence (that is
      override) data in the calendar or event sensor (unless you enable
      **Honor event times**, in which case the calendar always wins for
      events with explicit times):
        - Checkin/out TIME (not date) will update the calendar event and also
          the sensor tracked information. **NOTE:** If you use a timezone that
          is _not_ the system timezone on your calendar, you will run into
          weird and unexpected issues as that combination has no support yet!
        - Door code - by default when the integration updates the slot it uses
          the code that the sensor extracted / created. If you need to override
          the code you may do so after the slot update. This is useful if you
          have a non-managed slot that has the same door code (or starting
          code, typically first 4 digits) as the generated code and thus
          causes the slot to not function properly
    - **Slot name trimming**: if your lock has a character limit for slot
      names, enable **`trim_names`** and set **`max_name_length`** (default
      16, min 4). The integration trims names on word boundaries
    - **Lock code buffers**: set **`code_buffer_before`** and
      **`code_buffer_after`** (minutes) to make lock codes activate before
      check-in or stay active after checkout. Buffers only affect the
      physical lock code — not calendar times or sensor transitions
    - **Keymaster event diagnostics**: enable
      **`enable_keymaster_event_diagnostics`** to add a ring buffer of the
      last 10 keymaster events as an attribute on the check-in sensor.
      Useful for debugging lock code issues

## Reconfiguration

This integration supports reconfiguration after initial setup

- Press this button [![Open your Home Assistant instance and show an
integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=rental_control)
- Select the calendar and then select `Configure`
- Reconfigure as if you were setting it up for the first time

**NOTE:** Changes may not appear right away. The default update cycle checks
for updates every 2 minutes and events refresh around every 30 seconds. To
force a full update right away, select the `...` menu next to `Configure` and
select `Reload`

## Downloading Diagnostics

When reporting a problem — especially "the door code was never
created" — attach a diagnostics download. Open **Settings** >
**Devices & Services** > **Rental Control**, select the `...` menu next
to the affected entry and choose **Download diagnostics**.

The file reports the entry configuration, the shared door-code
allocator state, the latest reconciliation plan, the observed state of
every managed Keymaster slot, and the current reservations. You can
safely attach the file to a public issue. Diagnostics never include a
door code — only whether a slot holds a code, plus its digit count —
and the integration redacts the calendar URL, guest names, calendar
summaries, booking references and the free-form text of lock command
failures and slot exclusion notes. Reservation dates and durations drop
out entirely and reservation identities become per-download tokens,
because a code generator can rebuild a code from either one.

## Known issues

While the integration supports reconfiguration, some options may not fully
update after a change. If you have issues with reconfigured options not taking
effect, try reloading the particular integration installation or restart Home
Assistant.

## Frequently Asked Questions

### Why does my calendar events say `Reserved` instead of the guest's name?

AirBnB does not include guest or booking details in the invite. What the `ics`
data includes varies by provider. Calendar `ics` URLs from some 3rd party
tools (e.g. Host Tools and Guesty) do include guest information and will show
that rather than `Reserved` in calendar events.

### Where can I find my rental calendar's `ics` URL?

Each provider has slightly different instructions:

- [AirBnB](https://www.airbnb.com/help/article/99)
- [VRBO](https://help.vrbo.com/articles/Export-your-reservation-calendar)
- [Host Tools](https://help.hosttools.com/en/articles/5128627-how-do-i-export-an-ical-link-from-host-tools)

### How do I use custom calendars?

Custom calendars work as long as they provide a valid ICS file via an HTTPS
connection. You can structure the events on the calendar in different ways.

We recommend that the event Summary (aka event title) contain the guest's name
and not the word `Reserved`. We also strongly recommend that any calendar
entries across the sensor count worth of events have unique names. If the
entries are not unique, Rental Control may run into issues as the event Summary
drives the slot management.

The integration extracts the following data from the Description of the event
(and the match keys):

- Phone numbers for generating door codes in one of two ways
    - A line in the Description matching this regular expression:
      `\(?Last 4 Digits\)?:\s+(\d{4})` -- This line always takes precedence
      for generating a door code based on last 4 digits.
    - A line in the Description matching this regular expression:
      `Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})` which will then have the
      "air" squeezed out of it to extract the last 4 digits in the number
- Number of guests
    - A line in the Description that matches: `Guests:\s+(\d+)$`
    - The following lines also work and their values sum together:
        - `Adults:\s+(\d+)$`
        - `Children:\s+(\d+)$`
- The integration extracts email addresses from the Description by matching
  against: `Email:\s+(\S+@\S+)`
- Booking IDs match against: `Booking ID:\s*(.+)$`
- Reservation URLs match against the first URL in the Description
- **Dynamic attributes**: other `Field: Value` lines in the description that
  do not match the above patterns become sensor attributes with slugified keys
  (lowercase, spaces and special characters replaced with underscores)

An example calendar entry with this data might look like this:

```
Title: John and Jane Doe
Description:
    Phone: 555-555-5555
    Email: jdoe@example.com
    Guests: 2
    Booking ID: ABC-123456
    Property: Beach House
    https://www.example.com/reservation/123456789
```

The integration extracts the following information from this event:

```
Slot name: John and Jane Doe
Phone number: 555-555-5555
Last four: 5555
Email: jdoe@example.com
Number of guests: 2
Booking ID: ABC-123456
Reservation URL: https://www.example.com/reservation/123456789
Dynamic attribute "property": Beach House
```

### Automation Examples

Here are some examples of automations that work with Rental Control.

> **Note:** The entity IDs below use the current naming pattern. If you
> set up your integration before the naming change, your entity IDs may
> follow the older `sensor.rental_control_<calendar>_event_N` pattern.
> Check your entity registry for exact IDs.

- Manage thermostat for guests and between guests
    ```yaml
    alias: Manage Thermostat for Guests
    mode: single
    triggers:
      - entity_id:
          - sensor.my_calendar_rental_control_event_0
        attribute: description
        trigger: state
        to: 'No reservation'
        for:
          hours: 1
          minutes: 0
          seconds: 0
        id: No Reservations
      - entity_id:
          - sensor.my_calendar_rental_control_event_0
          attribute: eta_days
          for:
            hours: 1
            minutes: 0
            seconds: 0
          above: 1
          id: Between Guests
          trigger: numeric_state
      - entity_id:
          - sensor.my_calendar_rental_control_event_0
        attribute: eta_minutes
        below: 180
        id: Guests
        trigger: numeric_state
      - entity_id:
          - sensor.my_calendar_rental_control_event_1
        attribute: eta_minutes
        below: 180
        id: Guests
        trigger: numeric_state
      - entity_id:
          - calendar.my_calendar_rental_control
          to: 'on'
          id: Guests
          trigger: state
    conditions: []
    actions:
      - choose:
          - conditions:
              - condition: trigger
                  id: No Reservations
              sequence:
              - service: climate.set_temperature
                  target:
                  entity_id: climate.thermostat
                  data:
                  temperature: 65
          - conditions:
              - condition: or
                  conditions:
                    - condition: trigger
                        id: Between Guests
                    - condition: trigger
                        id: Guests
              sequence:
              - service: climate.set_temperature
                  target:
                  entity_id: climate.thermostat
                  data:
                  temperature: 72
    ```

## Development & Testing

### Running Tests

This project uses [pytest](https://docs.pytest.org/) with
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamworthy/pytest-homeassistant-custom-component)
for testing.

```bash
# Run all tests
python -m pytest tests/

# Run with coverage report
python -m pytest tests/ --cov=custom_components.rental_control --cov-report=term-missing

# Run unit tests
python -m pytest -m unit

# Run integration tests
python -m pytest -m integration

# Run a specific test file
python -m pytest tests/unit/test_config_flow.py -v
```

### Test Structure

| Directory | Marker | Description |
| --- | --- | --- |
| `tests/unit/` | `unit` | Fast isolated tests for individual components |
| `tests/integration/` | `integration` | Tests verifying component interactions |
| `tests/fixtures/` | — | Shared test data (ICS calendars, mock entries) |

### Pre-commit Hooks

All commits must pass [pre-commit](https://pre-commit.com/) checks:

```bash
pre-commit install
pre-commit run --all-files
```
