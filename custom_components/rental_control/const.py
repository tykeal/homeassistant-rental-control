"""Constants for Rental Control."""
# Base component constants
NAME = "Rental Control"
DOMAIN = "rental_control"
DOMAIN_DATA = f"{DOMAIN}_DATA"
VERSION = "0.0.1"
LOCK_MANAGER = "keymaster"

ISSUE_URL = "https://github.com/tykeal/homeassistant-rental-control/issues"

# In seconds; argument to asyncio.timeout
REQUEST_TIMEOUT = 60

# Icons
ICON = "mdi:account-key"
MAP_ICON = "mdi:map-search"

# hass.data attributes
COORDINATOR = "coordinator"
UNSUB_LISTENERS = "unsub_listeners"

# Platforms
CALENDAR = "calendar"
SENSOR = "sensor"
PLATFORMS = [CALENDAR, SENSOR]

# Events
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
CONF_CODE_GENERATION = "code_generation"
CONF_CODE_LENGTH = "code_length"
CONF_DAYS = "days"
CONF_EVENT_PREFIX = "event_prefix"
CONF_GENERATE = "generate_package"
CONF_IGNORE_NON_RESERVED = "ignore_non_reserved"
CONF_LOCK_ENTRY = "keymaster_entry_id"
CONF_MAX_EVENTS = "max_events"
CONF_PATH = "packages_path"
CONF_REFRESH_FREQUENCY = "refresh_frequency"
CONF_START_SLOT = "start_slot"
CONF_TIMEZONE = "timezone"
CONF_CREATION_DATETIME = "creation_datetime"

# Defaults
DEFAULT_CHECKIN = "16:00"
DEFAULT_CHECKOUT = "11:00"
DEFAULT_CODE_GENERATION = "date_based"
DEFAULT_CODE_LENGTH = 4
DEFAULT_DAYS = 365
DEFAULT_EVENT_PREFIX = ""
DEFAULT_GENERATE = True
DEFAULT_MAX_EVENTS = 5
DEFAULT_NAME = DOMAIN
DEFAULT_PATH = "packages/rental_control"
DEFAULT_REFRESH_FREQUENCY = 2
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
