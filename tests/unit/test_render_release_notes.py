# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Tests for the reno-to-Markdown release note renderer (issue #819)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_renderer() -> Any:
    """Import scripts/render_release_notes.py by path.

    scripts/ is deliberately not a package: a top-level ``scripts``
    package in the workspace shadows the ``scripts`` package that some
    GitHub Actions run with ``python -m``.
    """
    path = REPO_ROOT / "scripts" / "render_release_notes.py"
    spec = importlib.util.spec_from_file_location("render_release_notes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rrn = _load_renderer()

SECTIONS = [
    ("upgrade", "Upgrade Notes", 1),
    ("features", "New Features", 1),
    ("fixes", "Bug Fixes", 1),
]
SECTION_NAMES = [name for name, _, _ in SECTIONS]


class FakeLoader:
    """In-memory stand-in for ``reno.loader.Loader``."""

    def __init__(self, notes: dict[str, dict[str, dict[str, Any]]]) -> None:
        """Store ``{version: {filename: parsed content}}``."""
        self._notes = notes

    @property
    def versions(self) -> list[str]:
        """Return versions newest first, as reno does."""
        return list(self._notes)

    def __getitem__(self, version: str) -> list[tuple[str, str | None]]:
        """Return the note files of one version."""
        return [(name, "sha") for name in self._notes[version]]

    def parse_note_file(self, filename: str, sha: str | None) -> dict[str, Any]:
        """Return the parsed content of one note file."""
        for files in self._notes.values():
            if filename in files:
                return files[filename]
        raise KeyError(filename)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (None, ["*working-copy*", "v3.9.0-2"]),
        ("v3.9.0", ["v3.9.0"]),
        ("v3.7.0", []),
    ],
)
def test_select_versions(version: str | None, expected: list[str]) -> None:
    """Unreleased notes stop at the newest tag; a named tag stands alone."""
    versions = ["*working-copy*", "v3.9.0-2", "v3.9.0", "v3.8.0"]
    tags = {"v3.9.0", "v3.8.0", "v3.7.0"}

    assert rrn.select_versions(versions, tags, version) == expected


def test_select_versions_head_on_tag_has_no_unreleased_notes() -> None:
    """Right after a release, nothing is unreleased."""
    assert rrn.select_versions(["v3.9.0", "v3.8.0"], {"v3.9.0", "v3.8.0"}, None) == []


def test_render_orders_sections_and_formats_multiline_items() -> None:
    """Sections follow config order; continuation lines stay in the bullet."""
    loader = FakeLoader(
        {
            "v3.9.0-1": {
                "a.yaml": {
                    "fixes": ["Fixed a thing.\n"],
                    "upgrade": [
                        "First line\nsecond line.\n\n```yaml\nkey: value\n```\n"
                    ],
                },
                "b.yaml": {
                    "prelude": "A summary\nparagraph.\n",
                    "fixes": "A single string item.\n",
                },
            }
        }
    )

    notes = rrn.collect_notes(loader, ["v3.9.0-1"], SECTION_NAMES, "prelude")

    assert rrn.render_markdown(notes, SECTIONS) == (
        "A summary\nparagraph.\n\n"
        "### Upgrade Notes\n\n"
        "- First line\n  second line.\n\n  ```yaml\n  key: value\n  ```\n\n"
        "### Bug Fixes\n\n"
        "- Fixed a thing.\n- A single string item."
    )


def test_render_without_notes_is_empty() -> None:
    """Empty or blank items produce no output at all."""
    loader = FakeLoader({"v3.9.0-1": {"a.yaml": {"fixes": ["  \n"]}}})

    notes = rrn.collect_notes(loader, ["v3.9.0-1"], SECTION_NAMES, "prelude")

    assert rrn.render_markdown(notes, SECTIONS) == ""


def test_drafter_config_unchanged_without_notes() -> None:
    """With no notes the committed config is used as is."""
    base = "---\nheader: |+\n  badge\n\ntemplate: |\n  $CHANGES\n"

    assert rrn.build_drafter_config(base, "") == base


def test_drafter_config_places_notes_between_badge_and_changes() -> None:
    """Notes follow the badge in the header; everything else is preserved."""
    base_text = (REPO_ROOT / ".github" / "release-drafter.yml").read_text()
    base = yaml.safe_load(base_text)

    generated = yaml.safe_load(
        rrn.build_drafter_config(base_text, "### Bug Fixes\n\n- Fixed.")
    )

    body = generated["header"] + generated["template"]
    assert body.startswith(base["header"] + "### Bug Fixes\n\n- Fixed.\n\n$CHANGES")
    assert {k: v for k, v in generated.items() if k != "header"} == {
        k: v for k, v in base.items() if k != "header"
    }


def test_main_rejects_unknown_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An explicit version must be an existing tag."""
    monkeypatch.setattr(rrn, "git_tags", lambda _root: {"v3.9.0"})

    assert rrn.main(["--version", "v9.9.9"]) == 2
    assert "not a git tag" in capsys.readouterr().err


def test_main_writes_drafter_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The CLI merges rendered notes into the given config file."""
    monkeypatch.setattr(rrn, "load_release_notes", lambda _root, _v: "- Note.")
    base = tmp_path / "base.yml"
    base.write_text("header: |\n  badge\ntemplate: $CHANGES\n")
    out = tmp_path / "out.yml"

    assert rrn.main(["--release-drafter-config", str(base), "--output", str(out)]) == 0
    assert yaml.safe_load(out.read_text())["header"] == "badge\n\n- Note.\n\n"
