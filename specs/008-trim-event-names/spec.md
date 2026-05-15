<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Feature Specification: Trim Event Names for Keymaster

**Feature Branch**: `008-trim-event-names`
**Created**: 2025-07-14
**Status**: Shipped (retroactive spec — implementation merged in PR #524)
**Input**: User description: "Add an option to trim event names to a specific maximum character length before sending them to Keymaster. Some locks that Keymaster supports take the actual slot name defined in Keymaster, but the lock provider itself only supports a maximum character length. Since Rental Control is the gateway for codes entering Keymaster, it's the logical place to trim names. Trimming should be on word boundaries to produce readable names."

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Enable Name Trimming for a Lock With Character Limits (Priority: P1)

A property manager uses a lock whose provider only supports slot names of 16 characters or fewer. They enable the "Trim Names" option in Rental Control's configuration and set the maximum length to 16. When a calendar event with a long guest name (e.g., "Christopher Montgomery") is synced, the prefix is preserved and the guest portion is trimmed on a word boundary using the remaining character budget (max length minus the prefix length, including the appended space separator) so the lock receives a readable, compliant name instead of a truncated or rejected one.

**Why this priority**: This is the core value of the feature — without trimming, locks with character limits receive names that are too long, which may cause silent failures or garbled display on the lock itself.

**Independent Test**: Can be fully tested by configuring trimming with a max length, creating a calendar event with a long name, and verifying the slot name sent to Keymaster respects the character limit while remaining readable.

**Acceptance Scenarios**:

1. **Given** trimming is enabled with a max length of 16 and an event prefix of "Rental" (7 chars including the appended space), **When** a calendar event has a guest name of "Christopher Montgomery" (so the raw combined name would be "Rental Christopher Montgomery", 29 chars), **Then** the prefix is preserved and the guest portion is word-boundary trimmed against the remaining 9-character budget, producing `"Rental Christoph"` (the first guest word is hard-truncated to exactly 9 characters because it alone exceeds the remaining budget).
2. **Given** trimming is enabled with a max length of 16, **When** a combined name is already 16 characters or fewer, **Then** the name is sent unchanged (aside from internal whitespace normalization on the guest portion).
3. **Given** trimming is disabled (default), **When** any calendar event is synced, **Then** the full combined name is sent to Keymaster with no modification.

---

### User Story 2 — Configure Trimming Settings During Initial Setup (Priority: P2)

A property manager sets up a new Rental Control integration entry. During the configuration flow, they see options to enable name trimming and set the maximum character length. They enable trimming and set the max to 20 characters to match their lock's limitation.

**Why this priority**: The configuration options must exist before trimming can be used. Exposing them during setup ensures new users can configure the feature from the start.

**Independent Test**: Can be fully tested by walking through the integration setup flow and verifying the trim options appear, accept valid inputs, and persist correctly.

**Acceptance Scenarios**:

1. **Given** a user is adding a new Rental Control integration, **When** they reach the configuration step, **Then** they see a "Trim Names" toggle (default: off) and a "Max Name Length" field (default: 16, minimum: 4).
2. **Given** a user enables trimming and sets max length to 12, **When** they complete setup, **Then** the configuration is saved with trimming enabled and max length of 12.
3. **Given** a user does not enable trimming, **When** they complete setup, **Then** the configuration is saved with trimming disabled and the default max length of 16 is stored.

---

### User Story 3 — Reconfigure Trimming on an Existing Entry (Priority: P2)

A property manager who previously set up Rental Control without trimming now installs a new lock with a 16-character limit. They open the integration's options flow and enable trimming without needing to remove and re-add the integration.

**Why this priority**: Users must be able to change trimming settings after initial setup as their hardware may change over time.

**Independent Test**: Can be fully tested by modifying the options of an existing integration entry and verifying the new trim settings take effect on the next calendar sync.

**Acceptance Scenarios**:

1. **Given** an existing integration entry with trimming disabled, **When** the user opens options and enables trimming with max length 16, **Then** the updated settings are saved and applied to subsequent slot name generation.
2. **Given** an existing integration entry configured before the trim feature existed, **When** the user upgrades and opens options, **Then** the trim fields appear with default values (off, max 16).

---

### User Story 4 — Prefix Length Validation Warning (Priority: P3)

A property manager has a long event prefix (e.g., "VacationHome ") and enables trimming with a max length of 16. The configuration flow warns them that the prefix consumes most of the available characters, leaving insufficient room for a meaningful guest name.

**Why this priority**: Preventing misconfiguration protects users from a confusing situation where every guest name is essentially invisible, replaced entirely by the prefix.

**Independent Test**: Can be fully tested by configuring a long prefix alongside a short max length and verifying the warning message appears.

**Acceptance Scenarios**:

1. **Given** trimming is enabled with max length 16 and the event prefix is "VacationHome " (13 chars), **When** the user submits the configuration, **Then** a warning is displayed indicating the prefix is too long relative to the max name length.
2. **Given** trimming is enabled with max length 16 and the event prefix is "R " (2 chars), **When** the user submits the configuration, **Then** no warning is displayed.
3. **Given** trimming is disabled, **When** the user sets any prefix length, **Then** no prefix-length warning related to trimming is displayed regardless of prefix or max name length values.

---

### Edge Cases

- What happens when a single word in the guest name exceeds the remaining budget? The word is hard-truncated to fit within the remaining budget (max length minus the prefix length including the appended space).
- What happens when the prefix alone equals or exceeds the max length? This case is blocked by the FR-007 config-flow validation, which uses `errors["base"]` to reject configurations whose prefix doesn't leave at least `MIN_NAME_LENGTH` characters for the guest portion. It is only reachable via manual edits to the underlying config entry; in that pathological case the prefix is preserved verbatim and the guest portion becomes empty.
- What happens when the combined name is exactly the max length? It is sent as-is.
- What happens when the slot name is empty (e.g., unparsed event)? An empty guest portion remains empty after trimming — only the prefix is sent.
- What happens when max length is set to the minimum value of 4? Trimming still applies; most guest names will be hard-truncated but the system remains functional.
- What happens when whitespace-only content remains after prefix? The result is the prefix (trimmed of trailing whitespace if needed).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a boolean configuration option "Trim Names" that controls whether name trimming is applied to slot names before sending them to Keymaster. Default value MUST be off (disabled).
- **FR-002**: System MUST provide an integer configuration option "Max Name Length" that defines the maximum total character length of the combined slot name (prefix plus appended space plus guest portion). Default value MUST be 16. Minimum allowed value MUST be 4.
- **FR-003**: When trimming is enabled, the system MUST preserve the configured event prefix verbatim and trim only the guest/slot portion using a remaining budget equal to `max_name_length - len(prefix_with_separator)`. Trimming uses word-boundary logic on the guest portion: split on whitespace, accumulate words until the next word would exceed the remaining budget.
- **FR-004**: When trimming is enabled and a single word in the guest portion exceeds the remaining budget, the system MUST hard-truncate that word to the remaining budget.
- **FR-005**: When trimming is disabled, the system MUST send the full combined name to Keymaster with no modification.
- **FR-006**: The "Trim Names" and "Max Name Length" options MUST be available in both the initial configuration flow and the reconfiguration (options) flow.
- **FR-007**: During configuration, when trimming is enabled and an event prefix is set, the system MUST display a warning if `len(event_prefix) + 1` (to account for the space separator the integration appends between the prefix and the slot name) is greater than (max name length minus `MIN_NAME_LENGTH`, where `MIN_NAME_LENGTH` is 4). The warning is surfaced via `errors["base"]` with the key `prefix_too_long_for_trim`.
- **FR-008**: The configuration version MUST be migrated from version 8 to version 9. The migration MUST add default values for both new options to all existing configuration entries (trimming disabled, max length 16).
- **FR-009**: The "Max Name Length" field MUST reject values below the minimum of 4 with a validation error.
- **FR-010**: Trimmed names MUST NOT have trailing whitespace.

### Key Entities

- **Trim Configuration**: Two new settings (trim enabled flag, max name length) associated with each Rental Control integration entry. These settings govern whether and how combined slot names are shortened before being sent to Keymaster.
- **Combined Slot Name**: The string sent to Keymaster, formed by concatenating the event prefix (plus an appended space separator when a prefix is configured) with the guest/slot portion. When trimming is enabled, only the guest/slot portion is trimmed using a remaining budget; the prefix is preserved verbatim.

## Assumptions

- The integration appends a single space separator when concatenating the prefix and the parsed slot name, so the configured `event_prefix` is the bare prefix string and typically does not include trailing whitespace.
- Prefix-length validation accounts for that appended space by using `len(event_prefix) + 1` when comparing against the max name length.
- Word boundaries are defined solely by whitespace characters (spaces, tabs). No special handling for hyphens, underscores, or other punctuation as word separators.
- The minimum max name length of 4 is sufficient to display at least a meaningful fragment of a name or prefix in all reasonable scenarios.
- Existing users upgrading from config version 8 to 9 will receive default values (trimming off, max length 16) and experience no change in behavior until they explicitly enable trimming.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When trimming is enabled, 100% of slot names sent to Keymaster are at or below the configured max character length.
- **SC-002**: When trimming is disabled, 100% of slot names are sent unchanged compared to current behavior (no regression).
- **SC-003**: Trimmed names remain human-readable — the prefix is preserved verbatim and trimming occurs on word boundaries in the guest portion in all cases except when the first guest word exceeds the remaining budget.
- **SC-004**: Users can enable, disable, and adjust trimming settings in under 30 seconds through the configuration or options flow.
- **SC-005**: Users with existing configurations experience zero disruption when upgrading — default values are applied automatically and prior behavior is preserved.
- **SC-006**: When a prefix is too long relative to the max name length, 100% of such configurations trigger a visible warning during setup or reconfiguration.
