# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Guard against reintroducing the non-public ``cv.vol`` attribute access."""

from __future__ import annotations

from pathlib import Path
import re

import custom_components.rental_control as rental_control

CV_VOL_PATTERN = re.compile(r"\bcv\.vol\b")


def _package_sources() -> list[Path]:
    """Return every Python source file shipped with the integration."""
    package_root = Path(rental_control.__file__).parent
    return sorted(
        path for path in package_root.rglob("*.py") if "__pycache__" not in path.parts
    )


def test_integration_never_uses_cv_vol() -> None:
    """Fail when any shipped module reaches for ``cv.vol``."""
    sources = _package_sources()
    assert sources, "no integration sources were discovered"

    offenders = [
        f"{path}:{number}: {line.strip()}"
        for path in sources
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        )
        if CV_VOL_PATTERN.search(line)
    ]

    assert not offenders, (
        "`cv.vol` is not public Home Assistant API. It only ever resolved "
        "because `homeassistant.helpers.config_validation` happened to do "
        "`import voluptuous as vol`, and it disappeared in Home Assistant "
        "2026.9 when schema validation moved to Probatio. Import voluptuous "
        "directly with `import voluptuous as vol` and use `vol.X` instead.\n"
        + "\n".join(offenders)
    )
