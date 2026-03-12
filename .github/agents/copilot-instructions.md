---
description: Development guidelines and conventions for the rental-control Home Assistant integration.
applyTo: '**'
---

<!--
SPDX-FileCopyrightText: 2025 Andrew Grimberg <tykeal@bardicgrove.org>
SPDX-License-Identifier: Apache-2.0
-->

# rental-control Development Guidelines

Auto-generated from all feature plans. Last updated: 2026-03-10

## Active Technologies
- Python ≥3.13.2 + icalendar ≥6.1.0, x-wr-timezone ≥2.0.0 (002-code-health)
- Home Assistant ≥2025.8.0 (platform + test harness) (002-code-health)
- Test stack: pytest-homeassistant-custom-component, aioresponses (002-code-health)
- Storage: N/A — Home Assistant config entries (002-code-health)
- Python ≥3.13.2 + Home Assistant ≥ 2025.8.0, icalendar ≥6.1.0, x-wr-timezone ≥2.0.0 (003-coordinator-migration)
- N/A (all state in-memory, config via HA config entries) (003-coordinator-migration)

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
- 003-coordinator-migration: Added Python ≥3.13.2 + Home Assistant ≥ 2025.8.0, icalendar ≥6.1.0, x-wr-timezone ≥2.0.0
- 002-code-health: Added Python ≥3.13.2 + icalendar ≥6.1.0, x-wr-timezone ≥2.0.0

<!-- MANUAL ADDITIONS START -->
<!-- MANUAL ADDITIONS END -->
