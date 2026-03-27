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
- [Installation](#installation)
    - [MANUAL INSTALLATION](#manual-installation)
    - [INSTALLATION VIA Home Assistant Community Store (HACS)](#installation-via-home-assistant-community-store-hacs)
- [Setup](#setup)
- [Reconfiguration](#reconfiguration)
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

- Creates a customizable number of event sensors for the current and upcoming
  events
    - sensor.rental_control_my_calendar_event_0
    - sensor.rental_control_my_calendar_event_1
    - sensor.rental_control_my_calendar_event_2
    - (...)
- Creates a calendar entry for use with calendar cards
    - calendar.rental_control_my_calendar
- Each event sensor has dynamically added attributes extracted from the event
  description when available:
    - `last_four` -- the last 4 digits of the phone number of the booking guest
    - `number_of_guests` -- the number of guests in the reservation
    - `guest_email` -- the email address of the booking guest
    - `phone_number` -- the phone number of the booking guest
    - `reservation_url` -- the URL to the reservation

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

### Keymaster Monitoring Switch

When you configure a Keymaster lock the integration creates a **Keymaster
Monitoring** switch entity. Turning it **on** makes Keymaster unlock events on
the configured lock trigger an immediate check-in transition (the sensor moves
from `awaiting_checkin` to `checked_in` the moment the guest uses their door
code). When **off** (the default), time-based automatic check-in applies.

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
- Slot command retry with escalation: if a lock code set/clear command fails
  after 3 attempts the integration creates a persistent notification alerting
  the user to take manual action
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

## Installation

To make full use of this integration, install
[Keymaster](https://github.com/FutureTense/keymaster) as this integration
depends upon it.

### MANUAL INSTALLATION

1. Download the
   [latest release](https://github.com/tykeal/homeassistant-rental-control/releases/latest)
1. Unpack the release and copy the `custom_components/rental_control` directory
   into the `custom_components` directory of your Home Assistant
   installation
1. Restart Home Assistant
1. Follow the instructions in the setup section to finish the configuration

### INSTALLATION VIA Home Assistant Community Store (HACS)

1. Ensure that [HACS](https://hacs.xyz/) is present
1. Then press the following button [![Open your Home Assistant instance and open
a repository inside the Home Assistant Community
Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tykeal&repository=homeassistant-rental-control&category=Integration)
1. Install the `Rental Control` integration
1. Restart Home Assistant
1. Follow the instructions in the setup section to finish the configuration

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
  times apply to the calendar events. If the events come in with times already
  attached they _will_ get overwritten (most rental hosting platforms provide
  day in / day out in the events)
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
      override) data in the calendar or event sensor:
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
- Reservation URLs match against the first URL in the Description

An example calendar entry with this data might look like this:

```
Title: John and Jane Doe
Description:
    Phone: 555-555-5555
    Email: jdoe@example.com
    Guests: 2
    https://www.example.com/reservation/123456789
```

The integration extracts the following information from this event:

```
Slot name: John and Jane Doe
Phone number: 555-555-5555
Last four: 5555
Email: jdoe@example.com
Number of guests: 2
Reservation URL: https://www.example.com/reservation/123456789
```

### Automation Examples

Here are some examples of automations that work with Rental Control

- Manage thermostat for guests and between guests
    ```yaml
    alias: Manage Thermostat for Guests
    mode: single
    triggers:
      - entity_id:
          - sensor.rental_control_my_calendar_event_0
        attribute: description
        trigger: state
        to: 'No reservation'
        for:
          hours: 1
          minutes: 0
          seconds: 0
        id: No Reservations
      - entity_id:
          - sensor.rental_control_my_calendar_event_0
          attribute: eta_days
          for:
            hours: 1
            minutes: 0
            seconds: 0
          above: 1
          id: Between Guests
          trigger: numeric_state
      - entity_id:
          - sensor.rental_control_my_calendar_event_0
        attribute: eta_minutes
        below: 180
        id: Guests
        trigger: numeric_state
      - entity_id:
          - sensor.rental_control_my_calendar_event_1
        attribute: eta_minutes
        below: 180
        id: Guests
        trigger: numeric_state
      - entity_id:
          - calendar.rental_control_my_calendar
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
