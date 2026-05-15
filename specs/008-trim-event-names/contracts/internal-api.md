<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Internal API Contracts: Trim Event Names

This feature is purely internal to the Rental Control integration — there are no
external REST/GraphQL APIs. The contracts below define the internal function
signatures and configuration schema additions.

## Contract 1: `trim_name()` Function

**Location**: `custom_components/rental_control/util.py`

```python
def trim_name(name: str, max_length: int) -> str:
    """Trim a combined slot name to max_length on word boundaries.

    Splits the input on whitespace and accumulates words left-to-right
    until adding the next word would exceed max_length. If the very
    first word exceeds max_length, it is hard-truncated.

    The returned string never has trailing whitespace and is guaranteed
    to be <= max_length characters.

    Args:
        name: The full combined name (prefix + slot name).
        max_length: Maximum allowed character length (must be >= 4).

    Returns:
        The trimmed name string.
    """
```

### Input/Output Contract

| Input | Output | Rule |
|-------|--------|------|
| `("Rental Christopher Montgomery", 16)` | `"Rental"` | "Rental Christopher" = 21 chars > 16, so only "Rental" (6) fits |
| `("Rental Chris", 16)` | `"Rental Chris"` | 12 chars ≤ 16, unchanged |
| `("Rental Christopher Montgomery", 28)` | `"Rental Christopher Montgomery"` | Exactly 28 ≤ 28, unchanged |
| `("Superlongname", 8)` | `"Superlon"` | Single word > max, hard-truncate |
| `("", 16)` | `""` | Empty input → empty output |
| `("Hi", 16)` | `"Hi"` | Under limit, unchanged |
| `("VacationHome Christopher", 12)` | `"VacationHome"` | First word = 12, exactly fits; next word would exceed |
| `("  spaced  name  ", 16)` | `"spaced name"` | Whitespace-split normalizes spacing |

### Preconditions
- `max_length >= 4` (enforced by config validation, not by this function)
- `name` is a string (may be empty)

### Postconditions
- `len(result) <= max_length`
- `result == result.rstrip()` (no trailing whitespace)

## Contract 2: Configuration Schema Additions

**Location**: `custom_components/rental_control/config_flow.py` → `_get_schema()`

### New Schema Fields

```python
vol.Optional(
    CONF_TRIM_NAMES,
    default=_get_default(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES),
): cv.boolean,
vol.Optional(
    CONF_MAX_NAME_LENGTH,
    default=_get_default(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH),
): vol.All(vol.Coerce(int), vol.Range(min=MIN_NAME_LENGTH)),
```

### Prefix Warning Validation

**Location**: `_start_config_flow()`, after existing validations

```python
# FR-007: Warn if prefix is too long relative to max name length
if (
    user_input.get(CONF_TRIM_NAMES, False)
    and user_input.get(CONF_EVENT_PREFIX, "")
):
    # +1 accounts for the space separator the integration appends
    # between the configured prefix and the parsed slot name.
    prefix_len = len(user_input[CONF_EVENT_PREFIX]) + 1
    max_len = user_input.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH)
    if prefix_len > (max_len - MIN_NAME_LENGTH):
        errors["base"] = "prefix_too_long_for_trim"
```

The threshold uses strict greater-than against
`(max_len - MIN_NAME_LENGTH)` so the configuration is still accepted
when the prefix exactly leaves room for `MIN_NAME_LENGTH` (4)
characters of guest name. `MIN_NAME_LENGTH` is defined in `const.py`.

## Contract 3: Config Migration v8 → v9

**Location**: `custom_components/rental_control/__init__.py` → `async_migrate_entry()`

```python
# 8 -> 9: Add trim_names and max_name_length to configuration
if version == 8:
    _LOGGER.debug("Migrating from version %s", version)
    data = config_entry.data.copy()
    data[CONF_TRIM_NAMES] = False
    data[CONF_MAX_NAME_LENGTH] = DEFAULT_MAX_NAME_LENGTH
    hass.config_entries.async_update_entry(
        entry=config_entry,
        unique_id=config_entry.unique_id,
        data=data,
        version=9,
    )
    version = 9
    _LOGGER.debug("Migration to version %s complete", config_entry.version)
```

### Migration Guarantees
- Existing entries get `trim_names=False` (no behavior change)
- Existing entries get `max_name_length=16` (spec default)
- Migration is idempotent (checks `if version == 8`)

## Contract 4: Coordinator Attributes

**Location**: `custom_components/rental_control/coordinator.py`

### `__init__()` additions (after line ~121):
```python
self.trim_names: bool = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
self.max_name_length: int = int(
    str(config.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH))
)
```

### `update_config()` additions (after `self.honor_event_times` update):
```python
self.trim_names = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
self.max_name_length = int(
    str(config.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH))
)
```

## Contract 5: UI String Keys

### `strings.json` / `translations/en.json` additions

**Config step (`user`) and options step (`init`) data labels**:
```json
"trim_names": "Trim slot names to maximum length",
"max_name_length": "Maximum slot name length"
```

**Config and options error keys**:
```json
"prefix_too_long_for_trim": "Event prefix too long for max name length"
```
