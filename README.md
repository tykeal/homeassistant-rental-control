# Rental Control management for Home Assistant

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/tykeal/homeassistant-rental-control/main.svg)](https://results.pre-commit.ci/latest/github/tykeal/homeassistant-rental-control/main)

Home Assistant Rental Manager is designed to handle the need for custom
calendars and sensors to go with them related to managing rental properties.

## Features

-   Ingests ICS calendars from any HTTPS source as long as it's a text/calendar
    file
-   Configurable refresh rate from as often as possible to once per day (default
    every 2 minutes)
-   Define checkin/checkout times which will be added to all calendar entries
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
-   3 door code generators are available:
    -   A check-in/out date based 4 digit code using the check-in day combined
        with the check-out day (default and fallback in the case another
        generator fails to produce a code)
    -   A random 4 digit code based on the event description
    -   The last 4 digits of the phone number. This only works properly if the
        event description contains 'Last 4 Digits' followed quickly by a 4 digit
        number. This is the most stable, but only works if the event
        descriptions have the needed data. The previous two methods can have the
        codes change if the event makes changes to length or to the description.
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
1. Configure the Rental Control

### INSTALLATION VIA Home Assistant Community Store (HACS)

1. Ensure that [HACS](https://hacs.xyz/) is installed
1. Go to `HACS` -> `Integrations`
1. Search for and install the `Rental Control` integration
1. Restart Home Assistant
1. Go to `Configuration` -> `Devices & Services`
1. Press `+ ADD INTEGRATION`
1. Add `Rental Control`
1. Configure the parameters
1. Adding additional calendars can be done by adding another `Rental Control`
   integration in `Devices & Services`

## Setup

The integration is set up using the GUI.

-   Go to Configuration -> Integrations and click on the "+"-button.
-   Search for "Rental Control"
-   Enter a name for the calendar, and the URL
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
        trying to utilize the slot management component of Rental Control. In
        particular the `packages` directory configuration as Rental Control
        generates automations using a similar mechanism to Keymaster.
    -   **NOTE:** It is very important that the Keymaster slots that you are
        going to manage are either completely clear when you setup the
        integration _or_ that they follow the following rules:

        -   The slot name == Prefix(if defined) Slot_name(per the event sensor)
        -   The slot code == the defined slot code matches what is currently in
            the event sensor
        -   The start and stop dates and times match what are in the sensor

        Failing to follow these rules may cause your configuration to behave in
        unexpected way.

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
    -   An additional "mapping" sensor will be generated when setup to manage a
        lock. This sensor is primarily used for fireing events for the generated
        automations to pick up.

## Reconfiguration

This integration supports reconfiguration after initial setup

-   Go to Configuration -> Integrations and find the calendar you wish to modify
-   Select the calendar and then select `Configure`
-   Reconfigure as if you were setting it up for the first time

**NOTE:** Changes may not be picked up right away. The default update cycle of
the calendar is to check for updates every 2 minutes and events are refreshed
around every 30 seconds. If you want to force a full update right away, select
the `...` menu next to `Configure` and select `Reload`

## Known issues

While the integration supports reconfiguration a few things are not presently
working correctly with this. If you are needing to change
