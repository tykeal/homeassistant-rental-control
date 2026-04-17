---
description: Development guidelines and conventions for the rental-control Home Assistant integration.
applyTo: '**'
---

<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# rental-control Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-12

## Active Technologies
- Python ≥3.13.2 + icalendar ≥6.1.0, x-wr-timezone ≥2.0.0 (002-code-health)
- Home Assistant ≥2025.8.0 (platform + test harness) (002-code-health)
- Test stack: pytest-homeassistant-custom-component, aioresponses (002-code-health)
- Storage: N/A — Home Assistant config entries (002-code-health)
- Python ≥3.13.2 + Home Assistant ≥ 2025.8.0, icalendar ≥6.1.0, x-wr-timezone ≥2.0.0 (003-coordinator-migration)
- N/A (all state in-memory, config via HA config entries) (003-coordinator-migration)
- Python ≥3.13.2 (per pyproject.toml `requires-python = ">=3.13.2"`) + homeassistant ≥ 2025.8.0, icalendar ≥ 6.1.0, x-wr-timezone ≥ 2.0.0 (004-checkin-tracking)
- Home Assistant RestoreEntity state persistence (built-in HA mechanism) (004-checkin-tracking)
- Python ≥ 3.14.2 + homeassistant (core), icalendar, voluptuous, x-wr-timezone, aiohttp (007-honor-pms-times)
- Home Assistant config entries (persisted via HA's `.storage/` JSON files) (007-honor-pms-times)

## Project Structure

```text
custom_components/rental_control/
tests/
```

## Commands

uv run pytest tests/ -x -q
uv run ruff check custom_components/ tests/

## Code Style

Python ≥3.13.2: Follow standard conventions, ruff formatting

## Recent Changes
- 007-honor-pms-times: Added Python ≥ 3.14.2 + homeassistant (core), icalendar, voluptuous, x-wr-timezone, aiohttp
- 004-checkin-tracking: Added Python ≥3.13.2 (per pyproject.toml `requires-python = ">=3.13.2"`) + homeassistant ≥ 2025.8.0, icalendar ≥ 6.1.0, x-wr-timezone ≥ 2.0.0
- 003-coordinator-migration: Added Python ≥3.13.2 + Home Assistant ≥ 2025.8.0, icalendar ≥6.1.0, x-wr-timezone ≥2.0.0

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
