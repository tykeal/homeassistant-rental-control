# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
"""Import-hygiene tests for the coordinator_helpers package."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

import custom_components.rental_control.coordinator_helpers as helpers_pkg

HELPER_MODULES = [
    "calendar_parsing",
    "checkin_protection",
    "codegen",
    "config_update",
    "diagnostics",
    "keymaster_bootstrap",
    "keymaster_observation",
    "models",
    "reservations",
    "slot_matching",
    "store_sync",
]

# Modules that must remain free of Home Assistant / Store / coordinator imports.
PURE_MODEL_MODULES = ["models"]

FORBIDDEN_IMPORT_ROOTS = (
    "homeassistant",
    "custom_components.rental_control.coordinator",
)


@pytest.mark.parametrize("name", HELPER_MODULES)
def test_helper_module_imports(name: str) -> None:
    """Every helper module imports cleanly."""
    module = importlib.import_module(
        f"custom_components.rental_control.coordinator_helpers.{name}"
    )
    assert module is not None


def test_package_exposes_public_dataclasses() -> None:
    """The package re-exports the key public dataclasses."""
    for symbol in (
        "ObservedSlotQuery",
        "EventOverrideUpdate",
        "CalendarParseContext",
        "ReservationBuildContext",
        "StoreSyncPlan",
    ):
        assert hasattr(helpers_pkg, symbol)


def _imported_roots(source: str) -> set[str]:
    """Return the set of top-level dotted import roots used by *source*."""
    tree = ast.parse(source)
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module)
    return roots


@pytest.mark.parametrize("name", PURE_MODEL_MODULES)
def test_pure_modules_have_no_ha_imports(name: str) -> None:
    """Pure model modules must not import Home Assistant or the coordinator."""
    path = Path(helpers_pkg.__file__).parent / f"{name}.py"
    roots = _imported_roots(path.read_text(encoding="utf-8"))
    for root in roots:
        for forbidden in FORBIDDEN_IMPORT_ROOTS:
            assert not root.startswith(forbidden), f"{name}.py must not import {root!r}"
