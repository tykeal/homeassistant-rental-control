<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Test Fixture Contracts

**Feature**: 001-test-coverage
**Purpose**: Define the interface contracts for test fixtures used throughout the test suite

This document defines the schemas and contracts for all test fixtures to ensure consistency and type safety across the test suite.

---

## 1. ICS Calendar Fixture Contract

### Interface

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class ICSCalendarFixture:
    """Contract for ICS calendar test fixtures.

    Provides sample ICS calendar content for testing calendar parsing
    and event extraction functionality.
    """

    name: str
    """Unique identifier for this fixture."""

    source_platform: str
    """Platform this ICS format represents (airbnb, vrbo, generic, custom)."""

    ics_content: str
    """Complete ICS calendar string including BEGIN:VCALENDAR and END:VCALENDAR."""

    expected_event_count: int
    """Number of VEVENT entries expected in this calendar."""

    contains_timezone: bool
    """Whether this ICS includes X-WR-TIMEZONE or VTIMEZONE."""

    timezone_name: Optional[str]
    """Timezone identifier if contains_timezone is True."""

    event_summaries: list[str]
    """List of expected event summary/title strings."""

    event_start_dates: list[str]
    """List of expected event start dates (ISO format)."""

    tests_edge_case: Optional[str]
    """Description of edge case this fixture tests (None if standard case)."""

    is_valid: bool
    """Whether this ICS should parse successfully (False for error testing)."""

    expected_error: Optional[str]
    """Expected error message if is_valid is False."""
```

### Usage Example

```python
airbnb_fixture = ICSCalendarFixture(
    name="airbnb_two_events",
    source_platform="airbnb",
    ics_content="BEGIN:VCALENDAR\n...",
    expected_event_count=2,
    contains_timezone=True,
    timezone_name="America/New_York",
    event_summaries=["Reserved: John Doe", "Reserved: Jane Smith"],
    event_start_dates=["2025-01-15", "2025-02-10"],
    tests_edge_case=None,
    is_valid=True,
    expected_error=None
)
```

---

## 2. Event Description Fixture Contract

### Interface

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class EventDescriptionFixture:
    """Contract for event description test fixtures.

    Provides sample event descriptions for testing guest information
    extraction and attribute parsing.
    """

    name: str
    """Unique identifier for this fixture."""

    description_text: str
    """Raw event description string as it appears in ICS."""

    contains_email: bool
    """Whether description contains a parseable email address."""

    expected_email: Optional[str]
    """Email address that should be extracted (None if not present)."""

    contains_phone: bool
    """Whether description contains a parseable phone number."""

    expected_phone: Optional[str]
    """Phone number that should be extracted (None if not present)."""

    contains_guest_count: bool
    """Whether description contains guest count information."""

    expected_guest_count: Optional[int]
    """Number of guests that should be extracted (None if not present)."""

    contains_reservation_url: bool
    """Whether description contains a reservation/booking URL."""

    expected_reservation_url: Optional[str]
    """URL that should be extracted (None if not present)."""

    event_type: str
    """Type of event (reserved, blocked, not_available, other)."""

    should_be_filtered: bool
    """Whether this event should be filtered out based on type."""

    tests_edge_case: Optional[str]
    """Description of edge case this fixture tests (None if standard case)."""
```

### Usage Example

```python
full_info_fixture = EventDescriptionFixture(
    name="complete_guest_info",
    description_text="Guest: John Doe\nEmail: john@example.com\nPhone: 555-1234\nGuests: 4\nBooking: https://airbnb.com/r/12345",
    contains_email=True,
    expected_email="john@example.com",
    contains_phone=True,
    expected_phone="555-1234",
    contains_guest_count=True,
    expected_guest_count=4,
    contains_reservation_url=True,
    expected_reservation_url="https://airbnb.com/r/12345",
    event_type="reserved",
    should_be_filtered=False,
    tests_edge_case=None
)
```

---

## 3. Configuration Entry Fixture Contract

### Interface

```python
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ConfigEntryFixture:
    """Contract for configuration entry test fixtures.

    Provides sample Home Assistant config entries for testing
    configuration flow and integration setup.
    """

    name: str
    """Unique identifier for this fixture."""

    entry_id: str
    """Config entry ID (UUID format)."""

    title: str
    """User-visible title for this config entry."""

    domain: str = "rental_control"
    """Integration domain (always rental_control)."""

    data: dict[str, Any] = field(default_factory=dict)
    """Configuration data (from config flow)."""

    options: dict[str, Any] = field(default_factory=dict)
    """Options data (from options flow)."""

    is_valid: bool = True
    """Whether this configuration is valid."""

    validation_errors: list[str] = field(default_factory=list)
    """Expected validation error messages if is_valid is False."""

    required_fields: list[str] = field(default_factory=lambda: ["name", "url"])
    """Fields that must be present."""

    tests_scenario: str = "standard"
    """What scenario this config tests (standard, minimal, maximal, invalid, etc)."""


# Required Configuration Fields
REQUIRED_CONFIG_FIELDS = {
    "name": str,              # Calendar name
    "url": str,               # ICS calendar URL
}

# Optional Configuration Fields with Defaults
OPTIONAL_CONFIG_FIELDS = {
    "verify_ssl": (bool, True),
    "refresh_frequency": (int, 2),           # minutes
    "max_events": (int, 5),                  # number of sensors
    "days": (int, 365),                      # days ahead
    "checkin": (str, "16:00"),              # HH:MM format
    "checkout": (str, "11:00"),             # HH:MM format
    "code_generation": (str, "date_based"), # method
    "code_length": (int, 4),                # digits
    "start_slot": (int, 10),                # keymaster slot
    "event_prefix": (str, ""),              # prefix for events
    "timezone": (str, "UTC"),               # timezone name
    "ignore_non_reserved": (bool, False),   # filter events
    "keymaster_entry_id": (str, None),      # optional UUID
    "generate_package": (bool, True),       # auto-generate
    "packages_path": (str, "packages/rental_control"),
    "should_update_code": (bool, True),     # update on changes
}
```

### Usage Example

```python
minimal_config = ConfigEntryFixture(
    name="minimal_valid",
    entry_id="12345678-1234-1234-1234-123456789012",
    title="Test Calendar",
    data={
        "name": "Test Calendar",
        "url": "https://example.com/calendar.ics",
    },
    options={},
    is_valid=True,
    tests_scenario="minimal"
)

invalid_config = ConfigEntryFixture(
    name="missing_url",
    entry_id="12345678-1234-1234-1234-123456789013",
    title="Invalid Calendar",
    data={
        "name": "Invalid Calendar",
        # Missing required 'url' field
    },
    options={},
    is_valid=False,
    validation_errors=["url is required"],
    tests_scenario="invalid"
)
```

---

## 4. Mock Coordinator Data Contract

### Interface

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class MockEventData:
    """Contract for mock event data provided by coordinator.

    Represents a single rental event as provided to sensors.
    """

    summary: str
    """Event title/summary."""

    start: datetime
    """Event start datetime (timezone-aware)."""

    end: datetime
    """Event end datetime (timezone-aware)."""

    description: str
    """Event description (may contain guest info)."""

    location: Optional[str] = None
    """Event location if specified."""

    all_day: bool = False
    """Whether this is an all-day event."""

    # Extracted attributes (populated by coordinator)
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    guest_phone: Optional[str] = None
    guest_count: Optional[int] = None
    reservation_url: Optional[str] = None
    door_code: Optional[str] = None
    code_slot: Optional[int] = None


@dataclass
class MockCoordinatorData:
    """Contract for mock coordinator state.

    Represents the full state maintained by RentalControlCoordinator.
    """

    events: list[MockEventData]
    """List of events (current and upcoming)."""

    last_update: datetime
    """Timestamp of last successful update."""

    update_interval: int
    """Update interval in minutes."""

    calendar_name: str
    """Name of the calendar."""

    errors: list[str] = field(default_factory=list)
    """Any errors encountered during updates."""

    is_updating: bool = False
    """Whether an update is currently in progress."""
```

### Usage Example

```python
mock_data = MockCoordinatorData(
    events=[
        MockEventData(
            summary="Reserved: John Doe",
            start=datetime(2025, 1, 15, 16, 0, tzinfo=UTC),
            end=datetime(2025, 1, 20, 11, 0, tzinfo=UTC),
            description="Guest info here",
            guest_email="john@example.com",
            door_code="1234",
            code_slot=10
        )
    ],
    last_update=datetime.now(UTC),
    update_interval=2,
    calendar_name="Test Calendar",
    errors=[]
)
```

---

## 5. HTTP Mock Response Contract

### Interface

```python
from dataclasses import dataclass
from typing import Optional


@dataclass
class HTTPMockResponse:
    """Contract for mocked HTTP responses.

    Defines how calendar URL requests should be mocked during tests.
    """

    url: str
    """URL pattern to match for this mock."""

    status: int
    """HTTP status code to return."""

    body: Optional[str]
    """Response body content (ICS calendar or error message)."""

    headers: dict[str, str]
    """HTTP headers to include in response."""

    exception: Optional[Exception]
    """Exception to raise instead of returning response (for error testing)."""

    delay: float = 0.0
    """Simulated network delay in seconds."""


# Common HTTP mock scenarios
MOCK_SUCCESS = HTTPMockResponse(
    url="https://example.com/calendar.ics",
    status=200,
    body="BEGIN:VCALENDAR\n...\nEND:VCALENDAR",
    headers={"Content-Type": "text/calendar"},
    exception=None
)

MOCK_NOT_FOUND = HTTPMockResponse(
    url="https://example.com/notfound.ics",
    status=404,
    body="Not Found",
    headers={"Content-Type": "text/plain"},
    exception=None
)

MOCK_TIMEOUT = HTTPMockResponse(
    url="https://example.com/slow.ics",
    status=0,  # Never reached
    body=None,
    headers={},
    exception=asyncio.TimeoutError("Request timeout")
)
```

---

## 6. Test Assertion Helper Contract

### Interface

```python
from typing import Any, Protocol
from homeassistant.core import HomeAssistant


class EntityStateAssertion(Protocol):
    """Contract for entity state assertion helpers."""

    async def assert_entity_exists(
        self,
        hass: HomeAssistant,
        entity_id: str
    ) -> None:
        """Assert that entity exists in registry and state machine."""
        ...

    async def assert_entity_state(
        self,
        hass: HomeAssistant,
        entity_id: str,
        expected_state: str
    ) -> None:
        """Assert entity has expected state value."""
        ...

    async def assert_entity_attribute(
        self,
        hass: HomeAssistant,
        entity_id: str,
        attribute: str,
        expected_value: Any
    ) -> None:
        """Assert entity attribute has expected value."""
        ...

    async def assert_entity_count(
        self,
        hass: HomeAssistant,
        entity_prefix: str,
        expected_count: int
    ) -> None:
        """Assert number of entities with given prefix."""
        ...
```

---

## 7. Pytest Fixture Contract

### Standard Fixture Signatures

All pytest fixtures in conftest.py should follow these contracts:

```python
import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def valid_ics_calendar() -> str:
    """Provide a valid ICS calendar string.

    Returns:
        Complete ICS calendar with multiple events for testing.
    """
    ...


@pytest.fixture
def mock_calendar_url(aiohttp_client) -> None:
    """Mock HTTP requests to calendar URLs.

    Sets up aioresponses to intercept calendar fetch requests.
    Provides common success/error scenarios.
    """
    ...


@pytest.fixture
async def setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the rental_control integration for testing.

    Args:
        hass: Home Assistant instance from pytest-homeassistant-custom-component

    Returns:
        Configured and loaded MockConfigEntry for the integration
    """
    ...


@pytest.fixture
def mock_coordinator_data() -> MockCoordinatorData:
    """Provide mock coordinator data with sample events.

    Returns:
        MockCoordinatorData with typical event set for testing sensors
    """
    ...
```

---

## Contract Validation

All test fixtures must:

1. **Have type hints**: All parameters and return values typed
2. **Have docstrings**: Clear description of fixture purpose
3. **Be deterministic**: Same inputs produce same outputs
4. **Be isolated**: No shared mutable state between tests
5. **Be efficient**: Use appropriate pytest scope (function/module/session)
6. **Follow naming**: Snake_case, descriptive names

---

## Usage in Tests

### Example Test Using Contracts

```python
async def test_calendar_parsing(
    hass: HomeAssistant,
    valid_ics_calendar: str,
    mock_calendar_url: None
) -> None:
    """Test that valid ICS calendar is parsed correctly.

    This test verifies:
    - ICS calendar is fetched via HTTP
    - Events are extracted correctly
    - Event attributes are parsed

    Args:
        hass: Home Assistant test instance
        valid_ics_calendar: Fixture providing valid ICS content
        mock_calendar_url: Fixture that mocks HTTP responses
    """
    # Test implementation using the fixtures per their contracts
    ...
```

---

## Contract Evolution

As tests are implemented, these contracts may be refined. Any changes must:

1. Update this document
2. Update all fixtures implementing the contract
3. Update all tests using the fixtures
4. Maintain backward compatibility where possible

---

**Contracts Complete**: All fixture interfaces defined and documented.
