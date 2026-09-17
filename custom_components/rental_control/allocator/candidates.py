# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Deterministic candidate generation for allocator collision resolution."""

from __future__ import annotations

from collections.abc import Iterator
import hashlib


def _seed_for_identity(identity_key: str) -> int:
    """Return the stable integer seed for an allocation identity."""
    if not identity_key:
        raise ValueError("identity_key must be non-empty")
    digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
    return int(digest, 16)


def _step_for_seed(seed: int, space_size: int) -> int:
    """Return a step coprime to ``space_size`` for decimal code spaces."""
    step = ((seed // space_size) % space_size) or 1
    if step % 2 == 0:
        step += 1
    while step % 5 == 0:
        step += 2
    return step % space_size or 1


def candidate_codes(identity_key: str, code_length: int) -> Iterator[str]:
    """Yield every code in a deterministic full-cycle affine walk.

    The decimal code space has size ``10 ** code_length``.  The step is
    forced odd and non-divisible by five, making it coprime to that space
    so every zero-padded code is produced exactly once.
    """
    if code_length < 1:
        raise ValueError("code_length must be positive")
    space_size = 10**code_length
    seed = _seed_for_identity(identity_key)
    start = seed % space_size
    step = _step_for_seed(seed, space_size)
    for offset in range(space_size):
        yield f"{(start + offset * step) % space_size:0{code_length}d}"
