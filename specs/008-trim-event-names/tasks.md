<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Tasks: Trim Event Names

**Input**: Design documents from `/specs/008-trim-event-names/`
**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: Unit and integration tests are required for `trim_name()`,
config validation, and any migration logic; see quickstart.md for the
testing strategy.

**Status**: This is a **retroactive** task list — the feature has
already shipped in PR #524. All task checkboxes are marked complete
to reflect the merged implementation.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3, US4)
- Include exact file paths in descriptions

## Path Conventions

- **Source**: `custom_components/rental_control/`
- **Tests**: `tests/unit/`, `tests/integration/`

---

## Phase 1: Setup (Constants & Shared Infrastructure)

**Purpose**: Add new configuration constants and defaults that all subsequent phases depend on

- [x] T001 Add `CONF_TRIM_NAMES`, `CONF_MAX_NAME_LENGTH`, `DEFAULT_TRIM_NAMES`, `DEFAULT_MAX_NAME_LENGTH`, and `MIN_NAME_LENGTH` constants to `custom_components/rental_control/const.py`

  **Details**: Add after the existing `DEFAULT_START_SLOT` line (~line 99):
  ```python
  CONF_TRIM_NAMES = "trim_names"
  CONF_MAX_NAME_LENGTH = "max_name_length"
  DEFAULT_TRIM_NAMES = False
  DEFAULT_MAX_NAME_LENGTH = 16
  MIN_NAME_LENGTH = 4
  ```
  Follow existing naming conventions (CONF_ for keys, DEFAULT_ for defaults).

---

## Phase 2: Foundational (Core Trim Logic)

**Purpose**: Implement the pure `trim_name()` function that all user stories depend on

**⚠️ CRITICAL**: The trim function must exist before any integration work can proceed

- [x] T002 Implement `trim_name(name: str, max_length: int) -> str` pure function in `custom_components/rental_control/util.py`

  **Details**: Add the function (before `async_fire_set_code`). Algorithm per research.md R-001:
  1. Normalize whitespace first: `name = " ".join(name.split())` (collapses internal runs and strips edges)
  2. If `len(name) <= max_length`: return the normalized `name`
  3. Split the normalized `name` on whitespace into words
  4. If first word length > `max_length`: return `first_word[:max_length]` (hard-truncate)
  5. Accumulate words left-to-right: add word if `current_length + 1 (separator) + word_length <= max_length`
  6. Return the space-joined accumulated words (no trailing whitespace by construction)

  **Contract** (from `contracts/internal-api.md`): `trim_name()` is called with the guest/slot portion only and a remaining budget (`max_name_length - len(prefix_with_separator)`). Examples:
  | Input (guest, budget) | Output | Combined result with prefix `"Rental "` |
  |-------|--------|------|
  | `("Christopher Montgomery", 9)` | `"Christophe"` | `"Rental Christophe"` (16) |
  | `("Chris", 9)` | `"Chris"` | `"Rental Chris"` (12) |
  | `("Christopher Montgomery", 21)` | `"Christopher"` | `"Rental Christopher"` (18) |
  | `("Superlongname", 8)` | `"Superlon"` | first-word hard-truncate |
  | `("", 16)` | `""` | prefix only |
  | `("Hi", 16)` | `"Hi"` | under limit, unchanged |
  | `("VacationHome Christopher", 12)` | `"VacationHome"` | second word would exceed remaining budget |
  | `("  spaced  name  ", 16)` | `"spaced name"` | whitespace normalized |

  **Postconditions**: `len(result) <= max_length` and `result == result.rstrip()`

  Must include SPDX header comment if adding to existing file (already present), full type hints, and a docstring matching the contract in `contracts/internal-api.md`.

**Checkpoint**: Core trim logic ready — user story implementation can now begin

---

## Phase 3: User Story 1 — Enable Name Trimming for a Lock With Character Limits (Priority: P1) 🎯 MVP

**Goal**: When trimming is enabled on a Rental Control integration entry, slot names sent to Keymaster are trimmed to the configured max length using word-boundary logic. When disabled, behavior is unchanged.

**Independent Test**: Configure an integration entry with `trim_names=True` and `max_name_length=16`, create a calendar event with a long guest name, and verify the slot name passed to Keymaster in `async_fire_set_code()` respects the 16-char limit while remaining readable. Also verify that with `trim_names=False`, the full name passes through unchanged.

### Implementation for User Story 1

- [x] T003 [P] [US1] Add `trim_names` and `max_name_length` attributes to `RentalControlCoordinator.__init__()` in `custom_components/rental_control/coordinator.py`

  **Details**: Add after the existing `self.honor_event_times` line (~line 121):
  ```python
  self.trim_names: bool = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
  self.max_name_length: int = int(
      str(config.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH))
  )
  ```
  Import `CONF_TRIM_NAMES`, `CONF_MAX_NAME_LENGTH`, `DEFAULT_TRIM_NAMES`, `DEFAULT_MAX_NAME_LENGTH` from `const.py`. Follow the exact `int(str(config.get(...)))` pattern used by existing attributes like `self.start_slot`.

- [x] T004 [P] [US1] Add `trim_names` and `max_name_length` to `RentalControlCoordinator.update_config()` in `custom_components/rental_control/coordinator.py`

  **Details**: Add after the existing `self.honor_event_times` update in `update_config()` (~line 508+):
  ```python
  self.trim_names = bool(config.get(CONF_TRIM_NAMES, DEFAULT_TRIM_NAMES))
  self.max_name_length = int(
      str(config.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH))
  )
  ```
  This ensures config changes via the options flow take effect at runtime.

- [x] T005 [US1] Integrate `trim_name()` call into `async_fire_set_code()` in `custom_components/rental_control/util.py`

  **Details**: After `slot_name` is initially constructed (`slot_name = f"{prefix}{event.extra_state_attributes['slot_name']}"`), preserve the prefix and trim only the guest portion against a remaining budget:
  ```python
  if coordinator.trim_names:
      guest = event.extra_state_attributes["slot_name"]
      guest_max = coordinator.max_name_length - len(prefix)
      slot_name = f"{prefix}{trim_name(guest, guest_max)}"
  ```
  This is the single integration point per research.md R-006. The trim only affects the name sent to Keymaster — sensor display names remain full-length. The prefix (including the appended space separator) is preserved verbatim. Import `trim_name` if not already in scope (it's in the same file).

**Checkpoint**: User Story 1 is now functional — trimming works at runtime when config has `trim_names=True`. Remaining stories add UI to configure it.

---

## Phase 4: User Story 2 — Configure Trimming Settings During Initial Setup (Priority: P2)

**Goal**: New users see "Trim Names" toggle and "Max Name Length" field during the initial configuration flow, can set values, and the settings persist correctly.

**Independent Test**: Walk through the integration setup flow, verify the trim fields appear with correct defaults (off, 16), set custom values (on, 20), complete setup, and confirm the stored config entry contains the correct values.

### Implementation for User Story 2

- [x] T006 [US2] Add `trim_names` and `max_name_length` fields to `_get_schema()` in `custom_components/rental_control/config_flow.py`

  **Details**: Add to the schema dict in `_get_schema()` following the existing field pattern (after the last `vol.Optional` entry):
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
  Import `CONF_TRIM_NAMES`, `CONF_MAX_NAME_LENGTH`, `DEFAULT_TRIM_NAMES`, `DEFAULT_MAX_NAME_LENGTH`, `MIN_NAME_LENGTH` from `const`. Both fields always visible per research.md R-002 (HA doesn't support conditional field visibility in single-step flows). The `max_name_length` field uses `vol.Range(min=MIN_NAME_LENGTH)` to enforce the minimum of 4 (FR-009).

- [x] T007 [US2] Bump config flow `VERSION` to `9` in `custom_components/rental_control/config_flow.py`

  **Details**: In the `RentalControlFlowHandler` class, update `VERSION = 8` to `VERSION = 9`. This aligns the config flow version with the new migration target.

- [x] T008 [P] [US2] Add UI labels for new fields in `custom_components/rental_control/strings.json`

  **Details**: Add to both `config.step.user.data` and `options.step.init.data` sections:
  ```json
  "trim_names": "Trim slot names to maximum length",
  "max_name_length": "Maximum slot name length"
  ```
  Follow the existing key naming pattern (matches `CONF_TRIM_NAMES` and `CONF_MAX_NAME_LENGTH` config keys).

- [x] T009 [P] [US2] Add English translations for new fields in `custom_components/rental_control/translations/en.json`

  **Details**: Mirror the same additions as `strings.json` — add `trim_names` and `max_name_length` labels to both `config.step.user.data` and `options.step.init.data` sections:
  ```json
  "trim_names": "Trim slot names to maximum length",
  "max_name_length": "Maximum slot name length"
  ```

- [x] T010 [US2] Add config migration v8→v9 in `custom_components/rental_control/__init__.py`

  **Details**: Add after the existing `if version == 7:` block (~line 252), following the exact same pattern:
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
  Import `CONF_TRIM_NAMES`, `CONF_MAX_NAME_LENGTH`, `DEFAULT_MAX_NAME_LENGTH` from `const`. Existing users get `trim_names=False` (no behavior change per SC-005) and `max_name_length=16`.

**Checkpoint**: New installations can configure trimming during setup. Existing installations migrate cleanly to v9 with defaults.

---

## Phase 5: User Story 3 — Reconfigure Trimming on an Existing Entry (Priority: P2)

**Goal**: Users can enable/disable trimming and change the max name length through the options flow on an existing integration entry without removing and re-adding it.

**Independent Test**: Open the options flow for an existing integration entry, enable trimming, set max length to 20, save, and verify the coordinator picks up the new values on the next `update_config()` call.

### Implementation for User Story 3

- [x] T011 [US3] Verify options flow includes trim fields via shared `_get_schema()` in `custom_components/rental_control/config_flow.py`

  **Details**: The options flow already uses the same `_get_schema()` function as the initial config flow. Since T006 added the trim fields to `_get_schema()`, the options flow automatically includes them. Verify that:
  1. The `RentalControlOptionsFlowHandler.async_step_init()` calls `_start_config_flow()` which uses `_get_schema()` — confirm the new fields appear
  2. The `_get_default()` helper correctly reads existing values from the config entry for the options flow (it uses `self.config_entry.data.get(key, default)`)
  3. When `trim_names` and `max_name_length` are changed via options, they are saved to `config_entry.data` via the existing `async_update_entry()` call in `_start_config_flow()`

  If any wiring is missing, add the necessary plumbing. The shared schema pattern means this task is primarily validation + any gap-filling.

**Checkpoint**: Existing users can reconfigure trimming settings via the options flow. Combined with US1 and US2, the full configuration lifecycle is complete.

---

## Phase 6: User Story 4 — Prefix Length Validation Warning (Priority: P3)

**Goal**: When trimming is enabled and the event prefix is too long relative to the max name length, the configuration flow displays a warning message so the user can adjust their settings.

**Independent Test**: Configure trimming with `max_name_length=16` and set event prefix to "VacationHome " (13 chars). Verify the warning `prefix_too_long_for_trim` appears. Then set prefix to "R " (2 chars) and verify no warning appears.

### Implementation for User Story 4

- [x] T012 [US4] Add prefix-length validation warning in `_start_config_flow()` in `custom_components/rental_control/config_flow.py`

  **Details**: After existing validations and before the `if not errors:` block, add per research.md R-004 (final decision):
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
  This uses `errors["base"]` which displays as a form-level message per HA convention. The user sees the warning, can adjust prefix/max_length, and resubmit. The threshold uses strict `>` against `(max_len - MIN_NAME_LENGTH)` so a prefix that exactly leaves room for `MIN_NAME_LENGTH` (4) characters of guest name is still accepted.

- [x] T013 [P] [US4] Add `prefix_too_long_for_trim` error string to `custom_components/rental_control/strings.json`

  **Details**: Add to both `config.error` and `options.error` sections:
  ```json
  "prefix_too_long_for_trim": "Event prefix too long for max name length"
  ```

- [x] T014 [P] [US4] Add `prefix_too_long_for_trim` error translation to `custom_components/rental_control/translations/en.json`

  **Details**: Mirror the same addition as `strings.json` — add the error key to both `config.error` and `options.error` sections:
  ```json
  "prefix_too_long_for_trim": "Event prefix too long for max name length"
  ```

**Checkpoint**: All user stories are now complete. The full feature lifecycle works: configure → migrate → trim at runtime → warn on misconfiguration.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: Final validation and cleanup across all modified files

- [x] T015 Run `uv run pytest tests/ -v` to verify all existing tests still pass with the new code
- [x] T016 Run `pre-commit run --all-files` to verify ruff, mypy, interrogate (100% docstrings), gitlint, and reuse (SPDX headers) compliance across all modified files
- [x] T017 Run quickstart.md validation — execute the development setup steps and verify the implementation order matches actual code

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 (needs constants from `const.py`)
- **Phase 3 (US1 — Trim at Runtime)**: Depends on Phase 2 (needs `trim_name()` function)
- **Phase 4 (US2 — Initial Config Flow)**: Depends on Phase 1 (needs constants); independent of Phase 3
- **Phase 5 (US3 — Options Flow)**: Depends on Phase 4 (needs schema additions from T006)
- **Phase 6 (US4 — Prefix Warning)**: Depends on Phase 4 (needs config flow structure from T006)
- **Phase 7 (Polish)**: Depends on all prior phases

### User Story Dependencies

- **US1 (P1)**: Depends on Phase 2 only — can run in parallel with US2
- **US2 (P2)**: Depends on Phase 1 only — can run in parallel with US1
- **US3 (P2)**: Depends on US2 (shared schema) — sequential after US2
- **US4 (P3)**: Depends on US2 (config flow structure) — sequential after US2

### Within Each User Story

- Coordinator attributes (T003, T004) can be done in parallel (different methods in same file)
- UI strings (T008, T009) can be done in parallel with schema changes (T006)
- Error strings (T013, T014) can be done in parallel with validation logic (T012)

### Parallel Opportunities

```text
After Phase 1 completes:
  ├── Phase 2 (T002 — trim_name function)
  │     └── Phase 3 (US1: T003 ∥ T004, then T005)
  └── Phase 4 (US2: T006, T007, T008 ∥ T009, T010)
        ├── Phase 5 (US3: T011)
        └── Phase 6 (US4: T012, T013 ∥ T014)
```

---

## Parallel Example: After Phase 1

```text
# These two tracks can run in parallel after T001 completes:

# Track A: Core trim logic → runtime integration
Task T002: Implement trim_name() in util.py
Task T003 ∥ T004: Add coordinator attributes (parallel — different methods)
Task T005: Wire trim_name() into async_fire_set_code()

# Track B: Config flow → UI → migration
Task T006: Add schema fields to _get_schema()
Task T007: Bump VERSION to 9
Task T008 ∥ T009: UI strings (parallel — different files)
Task T010: Add v8→v9 migration
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Phase 1: Constants (T001)
2. Complete Phase 2: `trim_name()` function (T002)
3. Complete Phase 3: Coordinator + integration wiring (T003–T005)
4. **STOP and VALIDATE**: Test trimming manually with hardcoded config values
5. Trimming works end-to-end — MVP proven

### Incremental Delivery

1. T001 → T002 → Foundation ready
2. T003–T005 → US1 complete: trimming works at runtime (**MVP!**)
3. T006–T010 → US2 complete: users can configure trimming via UI
4. T011 → US3 complete: existing users can reconfigure trimming
5. T012–T014 → US4 complete: prefix-length warnings protect users
6. T015–T017 → Polish: all tests pass, pre-commit clean

### Single Developer Strategy

Execute sequentially: T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012 → T013 → T014 → T015 → T016 → T017

Commit after each logical group:
1. Commit: T001 (constants)
2. Commit: T002 (trim function)
3. Commit: T003–T005 (coordinator + integration)
4. Commit: T006–T010 (config flow + migration + UI strings)
5. Commit: T011 (options flow verification)
6. Commit: T012–T014 (prefix warning)
7. Commit: T015–T017 (validation)

---

## Notes

- [P] tasks = different files or different methods, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All new code must have type hints, docstrings, and SPDX headers
- All commits must pass pre-commit: ruff, mypy, interrogate 100%, gitlint, reuse
