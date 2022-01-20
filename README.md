# Rental Control management for Home Assistant

[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/tykeal/homeassistant-rental-control/main.svg)](https://results.pre-commit.ci/latest/github/tykeal/homeassistant-rental-control/main)

This integration will create sensors for the next few future calendar events,
called:

-   sensor.rental_control_my_calendar_event_0
-   sensor.rental_control_my_calendar_event_1
-   sensor.rental_control_my_calendar_event_2
    (...)

And it will create a calendar-entry that can be used in the calendar cards etc.

-   calendar.rental_control_my_calendar

## Installation

To make full use of this integration, install
[Keymaster](https://github.com/FutureTense/keymaster) as this integration
depends upon it.

Making changes to checkin/checkout times of events will not be possible on an
individual event basis without Keymaster

Copy all files from the "custom_components/rental-control" directory to your
home-assistant config directory under custom_components/rental-control.

### Setup

The integration is set up using the GUI.

-   Go to Configuration -> Integrations and click on the "+"-button.
-   Search for "Rental Control"
-   Enter a name for the calendar, and the URL
-   By default it will set up 5 sensors for the 5 nex upcoming events
    (sensor.rental_control\_<calendar_name>\_event_1 ~ 5). You can adjust this
    to add more or fewer sensors
-   The integration will only consider events with a start time 365 days into
    the future by default. This can also be adjusted when adding a new calendar
