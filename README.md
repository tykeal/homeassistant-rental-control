# Rental Control management for Home Assistant

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/tykeal/homeassistant-rental-control/main.svg)](https://results.pre-commit.ci/latest/github/tykeal/homeassistant-rental-control/main)

Home Assistant Rental Manager is designed to handle the need for custom
calendars and sensors to go with them related to managing rental properties.

## Features

-   Ingests ICS calendars from any HTTPS source as long as it's a text/calendar
    file
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

## Planned features

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
-   The integration will only consider events with a start time 365 days (1 year)
    into the future by default. This can also be adjusted when adding a new
    calendar
-   Set your checkin and checkout times. All times are in 24 hour format. These
    times will be added to the calendar events. If the events come in with times
    already attached they _will_ be overwritten (most rental hosting platforms
    only provide day in / day out in the events)

## Reconfiguration

This integration supports reconfiguration after initial setup

-   Go to Configuration -> Integrations and find the calendar you wish to modify
-   Select the calendar and then select `Configure`
-   Reconfigure as if you were setting it up for the first time

**NOTE:** Changes may not be picked up right away. The update cycle of the
calendar is to check for updates every 2 minutes and events are refreshed around
every 30 seconds. If you want to force a full update right away, select the
`...` menu next to `Configure` and select `Reload`
