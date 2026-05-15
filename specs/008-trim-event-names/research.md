<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Research: Trim Event Names

## R-001: Word-Boundary Trimming Algorithm

**Decision**: Split on whitespace, accumulate words left-to-right until the next word would exceed `max_length`, then join accumulated words. If the very first word exceeds `max_length`, hard-truncate it to `max_length`.

**Rationale**: This is the simplest algorithm that produces human-readable results. The spec explicitly defines word boundaries as whitespace-only (no hyphens/underscores). Left-to-right accumulation preserves the prefix (which comes first in the concatenated string), matching the user's mental model of "prefix + name".

**Alternatives considered**:
- **textwrap.shorten()**: Python stdlib, but adds an ellipsis suffix by default and uses `textwrap.wrap()` internally which handles hyphenation — more complex than needed and the ellipsis wastes characters.
- **Right-side word search (rfind)**: Find the last space before `max_length` and slice. Simpler one-liner but doesn't handle the edge case where the first word exceeds max (would return empty string). Also less explicit about the accumulation logic.
- **Regex-based**: `re.match(r'(\S+(\s+\S+)*)', text[:max_length])` — clever but harder to read, doesn't handle hard-truncation fallback cleanly.

## R-002: Home Assistant Config Flow Pattern for Boolean + Dependent Integer

**Decision**: Add both fields (`trim_names` as `cv.boolean`, `max_name_length` as `vol.All(vol.Coerce(int), vol.Range(min=4))`) to the existing single-step schema in `_get_schema()`. The `max_name_length` field is always visible regardless of trim toggle state (HA config flows don't support conditional visibility without multi-step flows).

**Rationale**: The existing config flow uses a single-step schema for all fields. Adding a second step just for trim settings would be inconsistent with the current UX. HA's frontend doesn't support conditionally showing/hiding fields within a single step — this is a known platform limitation. Keeping `max_name_length` always visible is harmless (it's ignored when trimming is off) and avoids complexity.

**Alternatives considered**:
- **Multi-step flow**: Split into step 1 (existing) + step 2 (trim settings). Rejected because no other field uses this pattern in this integration, and it makes reconfiguration harder.
- **Conditional schema**: Build the schema dynamically based on `trim_names` value. Not possible in HA's single-form-submission model — the form is rendered once with all fields.

## R-003: Config Migration Pattern (v8 → v9)

**Decision**: Follow the existing sequential migration pattern in `__init__.py`. Add a `if version == 8:` block that copies `config_entry.data`, adds `CONF_TRIM_NAMES: False` and `CONF_MAX_NAME_LENGTH: 16`, calls `async_update_entry()` with `version=9`, then sets `version = 9`.

**Rationale**: This exactly matches the pattern used for all prior migrations (v3→4 through v7→8). The migration is additive-only (new keys with defaults), so existing behavior is preserved. Setting `trim_names` to `False` ensures zero disruption per SC-005.

**Alternatives considered**:
- **Schema migration via `async_migrate_minor_version`**: HA supports minor version migrations, but this integration doesn't use them — all prior migrations use major version bumps. Switching patterns mid-project would be inconsistent.

## R-004: Prefix Length Validation Warning

**Decision**: In `_start_config_flow()`, after existing validations and before the `if not errors:` block, check: if `trim_names` is enabled and `len(event_prefix) + 1 > (max_name_length - MIN_NAME_LENGTH)` (the `+ 1` accounts for the space separator the integration appends between the prefix and the parsed slot name, and `MIN_NAME_LENGTH` is `4`), set `errors["base"] = "prefix_too_long_for_trim"` so the form re-renders with the warning.

**Rationale**: FR-007 calls for a *warning*. The initial intent was to use HA's `description_placeholders` so the form could be submitted with an advisory message, but further investigation (see the Update note below) showed that HA config flows don't reliably surface a non-blocking warning that persists across re-renders. The pragmatic compromise is to use `errors["base"]` as a *soft* validation error: it re-renders the form with the message, but the user can still proceed by adjusting the prefix or max length (or by disabling trimming). This is the closest the HA UI offers to a warning short of a persistent notification.

**Alternatives considered**:
- **Hard validation error on a specific field**: Would prevent saving the config and tie the message to one input. Rejected because the condition is cross-field (prefix ↔ max length) and there are legitimate cases where the user wants to proceed anyway.
- **Persistent notification**: Too intrusive and not tied to the config flow context. Rejected.
- **`description_placeholders`-only warning**: Initially preferred, but doesn't reliably persist across form re-renders. Rejected.

**Update after deeper investigation**: HA config flows don't natively support non-blocking warnings in the form UI via `description_placeholders` in a way that persists after re-render. Using `errors["base"]` re-shows the form with a form-level message; this is the pattern other HA integrations use for soft warnings. The user can adjust and resubmit, so the warning is effectively advisory rather than a true block.

**Final decision**: Use `errors["base"] = "prefix_too_long_for_trim"` as a soft/advisory validation error. The form re-renders with the warning, the user adjusts prefix/max_length (or disables trimming) and resubmits. This is acknowledged as a compromise — it does briefly block the immediate save — but it is the cleanest HA-native way to satisfy FR-007's "display a warning" intent without inventing a custom UI surface.

## R-005: Coordinator Integration Points

**Decision**: Add `self.trim_names: bool` and `self.max_name_length: int` to both `__init__()` (line ~93 area) and `update_config()` (line ~516 area) in `coordinator.py`. These are read from config using the new `CONF_TRIM_NAMES` and `CONF_MAX_NAME_LENGTH` constants.

**Rationale**: The coordinator is the data bridge between config entries and runtime behavior. All other config values follow this exact pattern — read in `__init__`, updated in `update_config`. The trim values are consumed in `util.py::async_fire_set_code()` via `coordinator.trim_names` and `coordinator.max_name_length`.

**Alternatives considered**:
- **Pass config directly to trim function**: Would bypass the coordinator pattern and create inconsistency. Rejected.
- **Store on the sensor entity**: Trim is applied during code-setting, not during sensor state updates. The coordinator is the correct owner.

## R-006: Where to Apply Trimming

**Decision**: Apply trimming in `util.py::async_fire_set_code()` after `slot_name` is initially constructed. When `coordinator.trim_names` is true, re-build `slot_name` by preserving the prefix verbatim and trimming only the guest portion with a remaining budget:

```python
if coordinator.trim_names:
    guest = event.extra_state_attributes["slot_name"]
    guest_max = coordinator.max_name_length - len(prefix)
    slot_name = f"{prefix}{trim_name(guest, guest_max)}"
```

**Rationale**: This is the single point where the combined name is assembled before being sent to Keymaster. Trimming the guest portion (rather than the combined string) here ensures:
1. The prefix is preserved verbatim so the user's branding/identification is never truncated.
2. The total `len(slot_name)` is still bounded by `max_name_length` because the remaining budget already subtracts `len(prefix)`.
3. It happens after prefix concatenation but before any Keymaster API calls.
4. It doesn't affect other uses of the slot name (e.g., sensor display).

**Alternatives considered**:
- **Trim in coordinator during event processing**: Would affect sensor display names, which should show the full name. Rejected.
- **Trim in the Keymaster text entity call only**: Would require passing max_length deep into the call chain. The current location is cleaner.
