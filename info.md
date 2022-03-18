# Rental Control

{% if prerelease %}

## NB!: This is a Beta version

{% endif %}

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

## Planned features

-   Integration with [Keymaster](https://github.com/FutureTense/keymaster) to
    control door codes matched to the number of events being tracked

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

## Reconfiguration

This integration supports reconfiguration after initial setup

-   Go to Configuration -> Integrations and find the calendar you wish to modify
-   Select the calendar and then select `Configure`
-   Reconfigure as if you were setting it up for the first time

**NOTE:** Changes may not be picked up right away. The update cycle of the
calendar is to check for updates every 2 minutes and events are refreshed around
every 30 seconds. If you want to force a full update right away, select the
`...` menu next to `Configure` and select `Reload`
