# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guard the integration's schema validation imports.

Shipped code must import Probatio directly: neither the non-public
``cv.vol`` attribute nor the ``voluptuous`` compatibility shim.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

import custom_components.rental_control as rental_control

CV_VOL_PATTERN = re.compile(r"\bcv\.vol\b")
VOLUPTUOUS_IMPORT_PATTERN = re.compile(r"^\s*(?:import|from)\s+voluptuous\b")


def _package_sources() -> list[Path]:
    """Return every Python source file shipped with the integration."""
    package_root = Path(rental_control.__file__).parent
    return sorted(
        path for path in package_root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _offenders(pattern: re.Pattern[str]) -> list[str]:
    """Return ``path:line: source`` for every shipped line matching pattern."""
    sources = _package_sources()
    assert sources, "no integration sources were discovered"
    return [
        f"{path}:{number}: {line.strip()}"
        for path in sources
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if pattern.search(line)
    ]


def test_integration_never_uses_cv_vol() -> None:
    """Fail when any shipped module reaches for ``cv.vol``."""
    offenders = _offenders(CV_VOL_PATTERN)

    assert not offenders, (
        "`cv.vol` is not public Home Assistant API. It only ever resolved "
        "because `homeassistant.helpers.config_validation` happened to do "
        "`import voluptuous as vol`, and it disappeared in Home Assistant "
        "2026.9 when schema validation moved to Probatio. Use `import "
        "probatio` and `probatio.X` instead.\n" + "\n".join(offenders)
    )


def test_integration_never_imports_voluptuous() -> None:
    """Fail when any shipped module imports the voluptuous shim."""
    offenders = _offenders(VOLUPTUOUS_IMPORT_PATTERN)

    assert not offenders, (
        "Home Assistant validates schemas with Probatio; `voluptuous` is "
        "only a compatibility shim. Use `import probatio` and `probatio.X` "
        "instead.\n" + "\n".join(offenders)
    )


def test_probatio_invalid_catches_config_validation_errors() -> None:
    """``except probatio.Invalid`` must catch Home Assistant cv errors.

    validation.py relies on this when it wraps ``cv.url`` and friends,
    and integrations that still import voluptuous must get the same
    classes through Home Assistant's compatibility shim.
    """
    from homeassistant.helpers import config_validation as cv
    import probatio
    import voluptuous

    assert voluptuous.Invalid is probatio.Invalid
    for validator, value in ((cv.url, "not a url"), (cv.positive_int, -1)):
        with pytest.raises(probatio.Invalid):
            validator(value)
