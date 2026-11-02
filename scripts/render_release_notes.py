#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Render reno release notes as Markdown for the GitHub release body.

``reno report`` only emits reStructuredText, so this script uses reno's
Python API to find the notes for a release and renders them as Markdown.

By default it renders the unreleased notes: every note added since the
most recent release tag, plus staged or modified notes in the working
copy. ``--version`` renders the notes of an existing release tag instead.

With ``--release-drafter-config`` it writes a copy of that Release
Drafter configuration with the rendered notes appended to its
``header`` key, so they sit between the downloads badge and the
changelog categories.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any
from typing import Protocol

import yaml

RELNOTES_DIR = "releasenotes"
WORKING_COPY = "*working-copy*"


class NoteLoader(Protocol):
    """The subset of ``reno.loader.Loader`` this script relies on."""

    @property
    def versions(self) -> list[str]:
        """Return the versions with notes, newest first."""

    def __getitem__(self, version: str) -> list[tuple[str, str | None]]:
        """Return ``(filename, sha)`` pairs for the notes of a version."""

    def parse_note_file(self, filename: str, sha: str | None) -> dict[str, Any]:
        """Return the parsed sections of one note file."""


@dataclass
class RenderedNotes:
    """Prelude paragraphs and section items gathered from note files."""

    prelude: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)


def select_versions(
    versions: Sequence[str], tags: set[str], version: str | None
) -> list[str]:
    """Pick the reno versions whose notes belong in the output.

    Without ``version`` this is every leading entry that is not a release
    tag: the working copy and reno's ``<latest-tag>-<N>`` development
    version. With ``version`` it is that version alone, if it has notes.
    """
    if version is not None:
        return [version] if version in versions else []
    selected = []
    for candidate in versions:
        if candidate in tags:
            break
        selected.append(candidate)
    return selected


def collect_notes(
    loader: NoteLoader,
    versions: Sequence[str],
    section_names: Sequence[str],
    prelude_name: str,
) -> RenderedNotes:
    """Gather the prelude and section items from the selected versions."""
    notes = RenderedNotes()
    for version in versions:
        for filename, sha in loader[version]:
            content = loader.parse_note_file(filename, sha)
            prelude = content.get(prelude_name)
            if isinstance(prelude, str) and prelude.strip():
                notes.prelude.append(prelude)
            for name in section_names:
                items = content.get(name) or []
                if isinstance(items, str):
                    items = [items]
                notes.sections.setdefault(name, []).extend(
                    item for item in items if isinstance(item, str) and item.strip()
                )
    return notes


def format_item(text: str) -> str:
    """Render one note item as a Markdown bullet.

    Continuation lines are indented by two spaces so that paragraphs,
    nested lists and fenced code blocks stay inside the bullet.
    """
    lines = textwrap.dedent(text).strip().splitlines()
    rest = [f"  {line}" if line.strip() else "" for line in lines[1:]]
    return "\n".join([f"- {lines[0]}", *rest])


def render_markdown(
    notes: RenderedNotes, sections: Sequence[tuple[str, str, int]]
) -> str:
    """Render collected notes as Markdown, or ``""`` when there are none.

    ``sections`` holds ``(name, title, level)`` in output order; level 1
    sections become ``###`` headings and deeper levels nest below them.
    """
    blocks = [textwrap.dedent(text).strip() for text in notes.prelude]
    for name, title, level in sections:
        items = notes.sections.get(name)
        if not items:
            continue
        blocks.append(f"{'#' * (2 + level)} {title}")
        blocks.append("\n".join(format_item(item) for item in items))
    return "\n\n".join(blocks)


def _literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    """Dump multi-line strings as YAML literal blocks for readability."""
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


class _ConfigDumper(yaml.SafeDumper):
    """YAML dumper that keeps multi-line strings readable."""


_ConfigDumper.add_representer(str, _literal_str)


def build_drafter_config(base_config: str, notes_markdown: str) -> str:
    """Return the Release Drafter config with notes appended to ``header``.

    The base config is returned unchanged when there are no notes.
    """
    if not notes_markdown:
        return base_config
    config = yaml.safe_load(base_config)
    header = config.get("header") or ""
    if header and not header.endswith("\n\n"):
        header = header.rstrip("\n") + "\n\n"
    config["header"] = f"{header}{notes_markdown.rstrip()}\n\n"
    dumped: str = yaml.dump(
        config,
        Dumper=_ConfigDumper,
        sort_keys=False,
        allow_unicode=True,
        width=1_000_000,
    )
    return (
        "---\n# Generated by scripts/render_release_notes.py; do not edit.\n" + dumped
    )


def git_tags(repo_root: Path) -> set[str]:
    """Return the names of every tag in the repository."""
    result = subprocess.run(
        ["git", "tag", "--list"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.split())


def load_release_notes(repo_root: Path, version: str | None) -> str:
    """Scan the repository with reno and render the selected notes."""
    from reno import config as reno_config
    from reno import loader as reno_loader

    conf = reno_config.Config(str(repo_root), RELNOTES_DIR)
    sections = [(s.name, s.title, s.section_level) for s in conf.sections]
    tags = git_tags(repo_root)
    with reno_loader.Loader(conf) as ldr:
        versions = select_versions(ldr.versions, tags, version)
        notes = collect_notes(
            ldr, versions, [s[0] for s in sections], conf.prelude_section_name
        )
    return render_markdown(notes, sections)


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="repository to scan (default: current directory)",
    )
    parser.add_argument(
        "--version",
        help="render the notes of this release tag instead of unreleased notes",
    )
    parser.add_argument(
        "--release-drafter-config",
        type=Path,
        help="write this Release Drafter config with the notes in its header",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="file to write instead of standard output",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the renderer; return the process exit status."""
    args = parse_args(argv)
    if args.version is not None and args.version not in git_tags(args.repo_root):
        print(f"error: {args.version!r} is not a git tag", file=sys.stderr)
        return 2
    output = load_release_notes(args.repo_root, args.version)
    if args.release_drafter_config is not None:
        output = build_drafter_config(
            args.release_drafter_config.read_text(encoding="utf-8"), output
        )
    elif output:
        output += "\n"
    if args.output is not None:
        args.output.write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
