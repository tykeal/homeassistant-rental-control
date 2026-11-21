# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0
"""Code encoding helpers for the legacy allocator registry."""

from __future__ import annotations

import base64
import hashlib


def encode_code(code: str, salt: str) -> str:
    """Obfuscate a door code with a salt prefix and base64 encoding."""
    return base64.b64encode(salt.encode("utf-8") + code.encode("utf-8")).decode("utf-8")


def decode_code(encoded_code: str, salt: str) -> str:
    """Recover an obfuscated door code using its stored salt value."""
    salt_bytes = salt.encode("utf-8")
    raw = base64.b64decode(encoded_code, validate=True)
    if not raw.startswith(salt_bytes):
        raise ValueError("encoded code does not match stored salt")
    return raw[len(salt_bytes) :].decode("utf-8")


def code_ref_for(code: str, salt: str) -> str:
    """Return the masked diagnostic reference for a plain code."""
    return hashlib.sha256(f"{salt}{code}".encode("utf-8")).hexdigest()[:8]
