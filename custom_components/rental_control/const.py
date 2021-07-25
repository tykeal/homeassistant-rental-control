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
SENSOR = "sensor"
CALENDAR = "calendar"
PLATFORMS = [SENSOR, CALENDAR]

# Config
CONF_MAX_EVENTS = "max_events"
CONF_DAYS = "days"

# Defaults
DEFAULT_NAME = DOMAIN
DEFAULT_MAX_EVENTS = 5

STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
Version: {VERSION}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
