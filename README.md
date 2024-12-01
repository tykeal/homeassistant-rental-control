<!--- SPDX-License-Identifier: Apache-2.0 -->
<!--- SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org. -->

# Rental Control management for Home Assistant

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/tykeal/homeassistant-rental-control/main.svg)](https://results.pre-commit.ci/latest/github/tykeal/homeassistant-rental-control/main)

Home Assistant Rental Manager is designed to handle the need for custom
calendars and sensors to go with them related to managing rental properties.

# Table of Contents

-   [Features](#features)
-   [Installation](#installation)
    -   [MANUAL INSTALLATION](#manual-installation)
    -   [INSTALLATION VIA Home Assistant Community Store (HACS)](#installation-via-home-assistant-community-store-hacs)
-   [Setup](#setup)
-   [Reconfiguration](#reconfiguration)
-   [Known issues](#known-issues)
-   [Frequently Asked Questions](#frequently-asked-questions)
    -   [Why does my calendar events say `Reserved` instead of the guest's name?](#why-does-my-calendar-events-say-reserved-instead-of-the-guests-name)
    -   [Where can I find my rental calendar's `ics` URL?](#where-can-i-find-my-rental-calendars-ics-url)
    -   [How do I use custom calendars?](#how-do-i-use-custom-calendars)

## Features

-   Ingests ICS calendars from any HTTPS source as long as it's a text/calendar
    file
-   Configurable refresh rate from as often as possible to once per day (default
    every 2 minutes)
-   Define checkin/checkout times which will be added to all calendar entries
    that are all day events
-   Ability to ignore 'Blocked' and 'Not available' events
-   Creates a customizable number of event sensors that are the current and
    upcoming events
    -   sensor.rental_control_my_calendar_event_0
    -   sensor.rental_control_my_calendar_event_1
    -   sensor.rental_control_my_calendar_event_2
    -   (...)
-   Creates a calendar-entry that can be used with calendar cards
    -   calendar.rental_control_my_calendar
-   Calendars can have their own timezone definition that is separate from the
    Home Assitant instance itself. This is useful for managing properties that are
    in a different timezone from where Home Assistant is
-   Events can have a custom prefix added to them to help differentiate between
    entities if more than one calendar is being tracked in an instance
-   Forcing a calendar refresh is currently possible by submitting a
    configuration change
-   Optional code length starting at 4 digits (requires even number of digits)
-   3 door code generators are available:
    -   A check-in/out date based 4 digit (or greater) code using the check-in
        day combined with the check-out day (default and fallback in the case
        another generator fails to produce a code)
        -   Codes can can optionally be regenerated if the reservation start or
            end dates are at least 1 day in future
    -   A random 4 digit (or greater) code based on the event description
    -   The last 4 digits of the phone number. This only works properly if the
        event description contains '(Last 4 Digits): ' or 'Last 4 Digits: '
        followed quickly by a 4 digit number. This is the most stable, but only
        works if the event descriptions have the needed data. The previous two
        methods can have the codes change if the event makes changes to length
        or to the description.
-   All events will get a code associated with it. In the case that the criteria
    to create the code are not fulfilled, then the check-in/out date based
    method will be used as a fallback
-   Each event has dynamically added attributes which consist of extracted
    information if available in the event description. The following attributes
    now get added:
    -   Last four -- the last 4 digits of the phone number of the booking guest
    -   Number of guests -- the number of guests in the reservation
    -   Guest email -- the email of the booking guest
    -   Phone number -- the phone number of the booking guest
    -   Reservation url -- the URL to the reservation
-   Integration with [Keymaster](https://github.com/FutureTense/keymaster) to
    control door codes matched to the number of events being tracked
-   Custom calendars are supported as long as they provide a valid ICS file via
    an HTTPS connection.
    -   Rental Events should be created as all day events (which is how all of
        the rental platforms provide events)
    -   Maintenance style events should be created with start and end times
    -   The event Summary (aka event title) _may_ contiain the word Reserved.
        This will cause the slot name to be generated in one of two ways:
        -   The word Reserved is followed by ' - ' and then something else, the
            something else will be used
        -   The word Reserved is _not_ followed by ' - ' then the full slot will
            be used
        -   The Summary contains nothing else _and_ the Details contain
            something that matches an Airbnb reservation identifier of
            `[A-Z][A-Z0-9]{9}` that is a capital alphabet letter followed by 9
            more characters that are either capital alphabet letters or numbers,
            then the slot will get this
        -   If the the Summary is _just_ Reserved and there is no Airbnb code in
            the Description, then the event will be ignored for purposes of
            managing a lock code.
        -   Technically any of the othe supported platform event styles for the
            Summary can be used and as long as the Summary conforms to it.
        -   The best Summary on a manual calendar is to use your guest name. The
            entries do need to be unique over the sensor count worth of events
            or Rental Control will run into issues.
    -   Additional information can be provided in the Description of the event
        and it will fill in the extra details in the sensor.
        -   Phone numbers for use in generating door codes can be provided in
            one of two ways
            -   A line in the Description matching this regular expression:
                `\(?Last 4 Digits\)?:\s+(\d{4})` -- This line will always take
                precedence for generating a door code based on last 4 digits.
            -   A line in the Description matching this regular expression:
                `Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})` which will then
                have the "air" squeezed out of it to extract the last 4 digits
                in the number
        -   Number of guests
            -   A line in the Description that matches: `Guests:\s+(\d+)$`
            -   Alternatively, the following lines will be added together to get
                the data:
                -   `Adults:\s+(\d+)$`
                -   `Children:\s+(\d+)$`
        -   Email addresses can be extracted from the Description by matching
            against: `Email:\s+(\S+@\S+)`
        -   Reservation URLS will match against the first (and hopefully only)
            URL in the Description

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

1. Ensure that [HACS](https://hacs.xyz/) is installed
1. Then press the following button [![Open your Home Assistant instance and open
a repository inside the Home Assistant Community
Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=tykeal&repository=homeassistant-rental-control&category=Integration)
1. Install the `Rental Control` integration
1. Restart Home Assistant
1. Follow the instructions in the setup section to finish the configuration

## Setup

The integration is set up using the GUI.

-   Press the following button to install the `Rental Control` integration
    [![Open your Home Assistant instance and start setting up a new
integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=rental_control)
-   Follow the prompts and then press `OK` on the question about installing
    `Rental Control`
-   Enter a name for the calendar, and the calendar's `ics` URL (see FAQ)
-   By default it will set up 5 sensors for the 5 nex upcoming events
    (sensor.rental_control\_\<calendar_name\>\_event_0 ~ 4). You can adjust this
    to add more or fewer sensors
-   The calendar refresh rate defaults to every 2 minutes but can be set to 0
    for as often as possible (roughly every 30 seconds) to once per day (1440).
    This is adjustable in minute increments
-   The integration will only consider events with a start time 365 days (1 year)
    into the future by default. This can also be adjusted when adding a new
    calendar
-   Set your checkin and checkout times. All times are in 24 hour format. These
    times will be added to the calendar events. If the events come in with times
    already attached they _will_ be overwritten (most rental hosting platforms
    only provide day in / day out in the events)
-   For configuration managing a Keymaster controlled lock, make sure that you
    have defined the lock during initial setup and that you have the starting
    slot set correctly for the integration.

    -   It is _very_ important that you have Keymaster fully working before
        trying to utilize the slot management component of Rental Control.
    -   **NOTE:** The Keymaster slots that are defined as being managed will be
        completely taken control of by Rental Control. Any data in the slots
        will be overwritten by Rental Control when it takes over the slot unless
        it matches event data for the calendar.
    -   The following portions of a Keymaster slot will influence (that is
        override) data in the calendar or event sensor:
        -   Checkin/out TIME (not date) will update the calendar event and also
            the sensor tracked information. **NOTE:** If you are using a
            timezone that is _not_ the system timezone on your calendar, you
            will likely run into weird and unexpected issues as that is not
            presently supported!
        -   Door code - by default when the slot is updated by the integration
            the code that is extracted / created by the sensor will be used. If,
            however, you have a need to override the code you may do so after
            the slot has been updated. This is useful if you have a non-managed
            slot that has the same door code (or starting code, typically first
            4 digits) that is the generated code and thus causing the slot to
            not function properly

## Reconfiguration

This integration supports reconfiguration after initial setup

-   Press this button [![Open your Home Assistant instance and show an
integration.](https://my.home-assistant.io/badges/integration.svg)](https://my.home-assistant.io/redirect/integration/?domain=rental_control)
-   Select the calendar and then select `Configure`
-   Reconfigure as if you were setting it up for the first time

**NOTE:** Changes may not be picked up right away. The default update cycle of
the calendar is to check for updates every 2 minutes and events are refreshed
around every 30 seconds. If you want to force a full update right away, select
the `...` menu next to `Configure` and select `Reload`

## Known issues

While the integration supports reconfiguration a few things may not fully update
after a reconfiguration. If you are having issues with reconfigured options
not being picked up properly try reloading the particular integration
installation or restart Home Assistant.

## Frequently Asked Questions

### Why does my calendar events say `Reserved` instead of the guest's name?

AirBnB does not include guest or booking details in the invite. What is included
in the `ics` data varies by provider. Calendar `ics` URLs from some 3rd party
tools (e.g. Host Tools and Guesty) do include guest information and will show
that rather than `Reserved` in calendar events.

### Where can I find my rental calendar's `ics` URL?

Each provider has slightly different instructions:

-   [AirBnB](https://www.airbnb.com/help/article/99)
-   [VRBO](https://help.vrbo.com/articles/Export-your-reservation-calendar)
-   [Host Tools](https://help.hosttools.com/en/articles/5128627-how-do-i-export-an-ical-link-from-host-tools)

### How do I use custom calendars?

Custom calendars can be used as long as they provide a valid ICS file via an
HTTPS connection. The events on the calendar can be done in multiple ways.

It is recommended that the event Summary (aka event title) contain the guest's
name and not the word `Reserved`. It is strongly recommended that any calendar
entries across the sensor count worth of events be unique. If the entries are not
unique, Rental Control may run into issues as the event Summary is used in the
slot management.

Data that will be pulled from the Description of the event (and the match keys):

-   Phone numbers for use in generating door codes can be provided in one of two
    ways
    -   A line in the Description matching this regular expression:
        `\(?Last 4 Digits\)?:\s+(\d{4})` -- This line will always take
        precedence for generating a door code based on last 4 digits.
    -   A line in the Description matching this regular expression:
        `Phone(?: Number)?:\s+(\+?[\d\. \-\(\)]{9,})` which will then have the
        "air" squeezed out of it to extract the last 4 digits in the number
-   Number of guests
    -   A line in the Description that matches: `Guests:\s+(\d+)$`
    -   Alternatively, the following lines will be added together to get the data:
        -   `Adults:\s+(\d+)$`
        -   `Children:\s+(\d+)$`
-   Email addresses can be extracted from the Description by matching against:
    `Email:\s+(\S+@\S+)`
-   Reservation URLS will match against the first (and hopefully only) URL in
    the Description

An example calendar entry with all of this data might look like this:

```
Title: John and Jane Doe
Description:
    Phone: 555-555-5555
    Email: jdoe@example.com
    Guests: 2
    https://www.example.com/reservation/123456789
```

The following information would be extracted from this event:

```
Slot name: John and Jane Doe
Phone number: 555-555-5555
Last four: 5555
Email: jdoe@example.com
Number of guests: 2
Reservation URL: https://www.example.com/reservation/123456789
```
