"""Constants for Rental Control."""
# Base component constants
NAME = "Rental Control"
DOMAIN = "rental_control"
DOMAIN_DATA = f"{DOMAIN}_DATA"
VERSION = "0.0.1"

ISSUE_URL = "https://github.com/tykeal/homeassistant-rental-control/issues"

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
CONF_MAX_EVENTS = "max_events"

# Defaults
DEFAULT_CHECKIN = "16:00"
DEFAULT_CHECKOUT = "11:00"
DEFAULT_DAYS = 365
DEFAULT_MAX_EVENTS = 5
DEFAULT_NAME = DOMAIN

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
