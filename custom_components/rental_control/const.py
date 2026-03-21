# SPDX-FileCopyrightText: 2021 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Constants for Rental Control."""

# Base component constants
NAME = "Rental Control"
DOMAIN = "rental_control"
DOMAIN_DATA = f"{DOMAIN}_DATA"
VERSION = "0.0.0"
LOCK_MANAGER = "keymaster"

ISSUE_URL = "https://github.com/tykeal/homeassistant-rental-control/issues"

# In seconds; argument to asyncio.timeout
REQUEST_TIMEOUT = 60

# Seconds to wait before first calendar refresh on startup when
# refresh_frequency is 0 (avoids overlapping refresh calls)
STARTUP_REFRESH_DELAY = 10

# Number of days in the past to keep calendar events before filtering
EVENT_AGE_THRESHOLD_DAYS = 30

# Time conversion constants for ETA calculations
SECONDS_PER_HOUR = 3600
SECONDS_PER_MINUTE = 60

# Icons
ICON = "mdi:account-key"
MAP_ICON = "mdi:map-search"

# hass.data attributes
COORDINATOR = "coordinator"
UNSUB_LISTENERS = "unsub_listeners"
CHECKIN_SENSOR = "checkin_sensor"
KEYMASTER_MONITORING_ENTITY_ID = "keymaster_monitoring_entity_id"

# Platforms
CALENDAR = "calendar"
SENSOR = "sensor"
SWITCH = "switch"
PLATFORMS = [CALENDAR, SENSOR, SWITCH]

# Events
EVENT_RENTAL_CONTROL_CHECKIN = "rental_control_checkin"
EVENT_RENTAL_CONTROL_CHECKOUT = "rental_control_checkout"
EVENT_RENTAL_CONTROL_CLEAR_CODE = "rental_control_clear_code"
EVENT_RENTAL_CONTROL_REFRESH = "rental_control_refresh"
EVENT_RENTAL_CONTROL_SET_CODE = "rental_control_set_code"

# Event data constants
ATTR_NOTIFICATION_SOURCE = "notification_source"

# Attributes
ATTR_CODE_SLOT = "code_slot"
ATTR_NAME = "rental_control_name"
ATTR_SLOT_NAME = "slot_name"

# Config
CONF_CHECKIN = "checkin"
CONF_CHECKOUT = "checkout"
CONF_CLEANING_WINDOW = "cleaning_window"
CONF_CODE_GENERATION = "code_generation"
CONF_CODE_LENGTH = "code_length"
CONF_CREATION_DATETIME = "creation_datetime"
CONF_DAYS = "days"
CONF_EVENT_PREFIX = "event_prefix"
CONF_GENERATE = "generate_package"
CONF_IGNORE_NON_RESERVED = "ignore_non_reserved"
CONF_LOCK_ENTRY = "keymaster_entry_id"
CONF_MAX_EVENTS = "max_events"
CONF_MAX_MISSES = "max_misses"
CONF_PATH = "packages_path"
CONF_REFRESH_FREQUENCY = "refresh_frequency"
CONF_SHOULD_UPDATE_CODE = "should_update_code"
CONF_START_SLOT = "start_slot"
CONF_TIMEZONE = "timezone"

# Defaults
DEFAULT_CHECKIN = "16:00"
DEFAULT_CHECKOUT = "11:00"
DEFAULT_CLEANING_WINDOW = 6.0
DEFAULT_CODE_GENERATION = "date_based"
DEFAULT_CODE_LENGTH = 4
DEFAULT_DAYS = 365
DEFAULT_EVENT_PREFIX = ""
DEFAULT_GENERATE = True
DEFAULT_MAX_EVENTS = 5
DEFAULT_MAX_MISSES = 2
DEFAULT_NAME = DOMAIN
DEFAULT_PATH = "packages/rental_control"
DEFAULT_REFRESH_FREQUENCY = 2
DEFAULT_SHOULD_UPDATE_CODE = True
DEFAULT_START_SLOT = 10

CODE_GENERATORS = [
    {"type": "date_based", "description": "Start/End Date"},
    {"type": "static_random", "description": "Static Random"},
    {"type": "last_four", "description": "Last 4 Phone Digits"},
]

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""

# Check-in tracking state constants
CHECKIN_STATE_NO_RESERVATION = "no_reservation"
CHECKIN_STATE_AWAITING = "awaiting_checkin"
CHECKIN_STATE_CHECKED_IN = "checked_in"
CHECKIN_STATE_CHECKED_OUT = "checked_out"

# Early checkout grace period in minutes
EARLY_CHECKOUT_GRACE_MINUTES = 15
