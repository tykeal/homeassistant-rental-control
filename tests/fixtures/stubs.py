# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Type-safe helpers for replacing methods on objects under test.

Assigning a mock straight onto a method (``obj.method = MagicMock()``)
is what mypy's ``method-assign`` check rejects, and the result is still
typed as the original method, so assertions on it fail type checking
too. These helpers perform the same instance-level replacement and hand
back the mock with its real type.
"""

from __future__ import annotations

from unittest.mock import NonCallableMock


def stub_method[M: NonCallableMock](obj: object, name: str, stub: M) -> M:
    """Replace ``obj.<name>`` with ``stub`` and return the stub.

    Raises ``AttributeError`` when ``obj`` has no such attribute, so a
    typo cannot silently install a mock nothing will ever call.
    """
    if not hasattr(obj, name):
        raise AttributeError(f"{type(obj).__name__} has no attribute {name!r}")
    setattr(obj, name, stub)
    return stub


def as_mock(value: object) -> NonCallableMock:
    """Return ``value`` typed as a mock, asserting that it is one."""
    assert isinstance(value, NonCallableMock), f"{value!r} is not a mock"
    return value
