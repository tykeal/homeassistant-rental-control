<!--
SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# Implementation Plan: Explicit Entry Data Guards

**Feature**: `010-explicit-entry-guards` | **Planning Branch**: `010-explicit-entry-guards-plan` | **Date**: 2026-06-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/010-explicit-entry-guards/spec.md`

## Summary

Replace the six issue-reported `hass.data.get(DOMAIN, {}).get(...)` entry-data
access paths with an explicit domain-and-entry guard. Research selects a shared
`get_entry_data(hass, entry_id) -> dict[str, Any] | None` helper in
`custom_components/rental_control/util.py` so all six sites distinguish missing
integration domain data from missing entry data without creating throwaway empty
state. Loaded-entry behavior remains unchanged; missing entry data short-circuits
or follows the existing component-absence fallback for that operation.

## Technical Context

**Language/Version**: Python >=3.14.2
**Primary Dependencies**: Home Assistant core, pytest-homeassistant-custom-component
**Storage**: HA runtime storage in `hass.data[DOMAIN][entry_id]`; no persisted data changes
**Testing**: pytest via `uv run pytest tests/`; ruff via `uv run ruff check ...`
**Target Platform**: Home Assistant custom integration on Linux/Docker/HA OS
**Project Type**: Single project — Home Assistant custom integration (HACS)
**Performance Goals**: O(1) dictionary lookups only; no measurable refresh-cycle impact
**Constraints**: Must not block the HA event loop; must not create phantom entry state
**Scale/Scope**: Six issue-reported access paths across three source files, plus tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Helper and changed paths will be type hinted, documented, and covered by targeted tests for loaded and missing data |
| II: Atomic Commit Discipline | PASS | Future implementation commits must remain atomic and include their required targeted tests with the code they validate |
| III: Licensing & Attribution | PASS | Modified files already have SPDX headers; new spec artifacts include SPDX headers |
| IV: Pre-Commit Integrity | PASS | Plan requires pytest and ruff before implementation merge; hooks must not be bypassed |
| V: Agent Co-Authorship & DCO | PASS | Commits use `git commit -s` with agent co-authorship trailers |
| VI: User Experience Consistency | PASS | No new options, entities, services, migrations, or normal-path behavior changes |
| VII: Performance Requirements | PASS | Helper centralizes the same O(1) lookups and adds no I/O or async work |

**Gate result: PASS** — No violations. Proceeding to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/010-explicit-entry-guards/
├── plan.md                    # This file
├── research.md                # Phase 0 decision: helper vs inline guards
├── quickstart.md              # Phase 1 maintainer verification guide
├── checklists/
│   └── requirements.md        # Existing SPEC-stage checklist
└── tasks.md                   # Phase 2 output only; not created in PLAN stage
```

`data-model.md` is intentionally omitted because this internal refactor adds no
entities, fields, relationships, validation rules, or state transitions.
`contracts/` is intentionally omitted because no external API, service,
configuration, entity, or integration contract changes are introduced.

### Source Code (repository root)

```text
custom_components/rental_control/
├── util.py                    # Add get_entry_data helper with docstring/type hints
├── __init__.py                # Use helper in update listener and event listener paths
├── sensors/
│   └── checkinsensor.py       # Use helper for monitoring and early-expiry checks
└── switch.py                  # Use helper before registering monitoring switch

tests/
├── unit/
│   ├── test_init.py           # Update listener missing-entry/domain regression coverage
│   ├── test_keymaster_event_diagnostics.py  # Event forwarding missing-data coverage
│   ├── test_checkin_sensor.py # Monitoring fallback and early-expiry missing-data coverage
│   └── test_switch.py         # Switch registration missing-data coverage
└── integration/
    └── test_full_setup.py     # Loaded-entry smoke/regression coverage if needed
```

**Structure Decision**: Single-project Home Assistant custom integration. The
implementation stage should only touch the helper, the six reported call sites,
and targeted tests required to prove both loaded-entry and missing-data behavior.

## Phase 0 Research

Research is complete in [research.md](research.md). It resolves the only central
design decision: use a shared helper rather than six inline guard blocks.

### Live Call-Site Findings

| Site | Current expression | Present-data behavior | Missing-data behavior to preserve or make explicit |
|------|--------------------|-----------------------|----------------------------------------------------|
| `__init__.py:update_listener` first lookup, line ~309 | `hass.data.get(DOMAIN, {}).get(config_entry.entry_id)` | Copy options, update entry data, call `coordinator.update_config()` | Return before config mutation/update work when domain or entry is absent |
| `__init__.py:update_listener` second lookup, line ~331 | `hass.data.get(DOMAIN, {}).get(config_entry.entry_id)` | Unsubscribe old listeners, clear list, re-register keymaster listeners when configured | Return before listener refresh if data vanished after the config update |
| `__init__.py:_handle_keymaster_event`, line ~484 | `hass.data.get(DOMAIN, {}).get(config_entry.entry_id, {})` | Fetch check-in sensor and monitoring switch, then forward accepted unlock events | Reject and return when domain or entry data is absent; do not report accepted forwarding |
| `sensors/checkinsensor.py:_is_keymaster_monitoring_enabled`, line ~464 | `self._hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})` | Use switch state when the monitoring switch exists | Missing domain/entry follows the existing safe fallback: `self.coordinator.lockname is not None` |
| `sensors/checkinsensor.py:async_checkout`, line ~1200 | `self._hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})` | If early-expiry switch is on, shorten lock-code expiry before checkout transition | Missing domain/entry skips early expiry and continues checkout as existing switch-absence behavior does |
| `switch.py:KeymasterMonitoringSwitch.async_added_to_hass`, line ~132 | `self.hass.data.get(DOMAIN, {}).get(self._config_entry.entry_id, {})` | Store the switch reference in entry data for event forwarding and monitoring checks | Return after state restore/logging when domain or entry is absent; do not mutate a throwaway dict |

The event listener also contains a nearby diagnostic sensor lookup using chained
`get` calls inside `_record()`. That lookup is not one of the six issue-reported
entry-data assignments, so the implementation stage should only change it if the
same helper is needed to keep the line ~484 event-path diagnostics internally
consistent without broadening scope.

## Phase 1 Design Artifacts

- [research.md](research.md): required and complete.
- [quickstart.md](quickstart.md): required and complete.
- `data-model.md`: N/A for this internal refactor; intentionally omitted.
- `contracts/`: N/A because no API or user-facing contracts change;
  intentionally omitted.
- `update-agent-context.sh`: intentionally not run. The design adds no new
  technology, dependency, platform, or agent-relevant context.

## Post-Design Constitution Check

| Principle | Status | Notes |
|-----------|--------|-------|
| I: Code Quality & Testing | PASS | Quickstart defines targeted pytest and ruff validation; helper must include docstring/type hints |
| II: Atomic Commit Discipline | PASS | PLAN artifacts are one docs-only commit; implementation work remains future-stage scope |
| III: Licensing & Attribution | PASS | `plan.md`, `research.md`, and `quickstart.md` include SPDX headers |
| IV: Pre-Commit Integrity | PASS | No bypasses planned; docs PR still runs repository hooks and CI |
| V: Agent Co-Authorship & DCO | PASS | PLAN commit will be signed off and co-authored |
| VI: User Experience Consistency | PASS | Design preserves loaded-entry behavior and introduces no UI/API/entity changes |
| VII: Performance Requirements | PASS | Shared helper adds no blocking work and preserves O(1) access |

**Gate result: PASS** — No complexity violations.

## Complexity Tracking

> No violations to justify — all gates pass.

## Phase Notes

- PLAN stage owns only `plan.md`, `research.md`, and `quickstart.md`.
- TASKS and IMPLEMENT stages must not be performed in this PR.
- Future tasks should include tests for both missing `hass.data[DOMAIN]` and
  missing `hass.data[DOMAIN][entry_id]` for each affected operation.
- Future implementation should keep supporting component absence separate from
  missing entry data: component lookups inside present `entry_data` may continue
  to use `.get(...)` with the existing fallback behavior.
