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

# Platforms
CALENDAR = "calendar"
SENSOR = "sensor"
PLATFORMS = [CALENDAR, SENSOR]

# Config
CONF_CHECKIN = "checkin"
CONF_CHECKOUT = "checkout"
CONF_DAYS = "days"
CONF_EVENT_PREFIX = "event_prefix"
CONF_IGNORE_NON_RESERVED = "ignore_non_reserved"
CONF_LOCK_ENTRY = "keymaster_entry_id"
CONF_MAX_EVENTS = "max_events"
CONF_REFRESH_FREQUENCY = "refresh_frequency"
CONF_START_SLOT = "start_slot"
CONF_TIMEZONE = "timezone"

# Defaults
DEFAULT_CHECKIN = "16:00"
DEFAULT_CHECKOUT = "11:00"
DEFAULT_DAYS = 365
DEFAULT_EVENT_PREFIX = ""
DEFAULT_MAX_EVENTS = 5
DEFAULT_NAME = DOMAIN
DEFAULT_REFRESH_FREQUENCY = 2
DEFAULT_START_SLOT = 10

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
