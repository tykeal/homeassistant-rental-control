# SPDX-License-Identifier: Apache-2.0
##############################################################################
# COPYRIGHT 2022 Andrew Grimberg
#
# All rights reserved. This program and the accompanying materials
# are made available under the terms of the Apache 2.0 License
# which accompanies this distribution, and is available at
# https://www.apache.org/licenses/LICENSE-2.0
#
# Contributors:
#   Andrew Grimberg - Initial implementation
##############################################################################
"""Rental Control utils."""
from __future__ import annotations

import hashlib
import logging
import re
import uuid

from .const import NAME

_LOGGER = logging.getLogger(__name__)


def gen_uuid(created: str) -> str:
    """Generation a UUID from the NAME and creation time."""
    m = hashlib.md5(f"{NAME} {created}".encode("utf-8"))
    return str(uuid.UUID(m.hexdigest()))


def get_slot_name(summary: str, description: str, prefix: str) -> str | None:
    """Determine the name for a given slot / event."""

    # strip off any prefix if it's being used
    if prefix is not None:
        p = re.compile(f"{prefix} (.*)")
        name = p.findall(summary)[0]
    else:
        name = summary

    # Blocked and Unavailable should not have anything
    p = re.compile("Not available|Blocked")
    if p.search(name):
        return None

    # Airbnb and VRBO
    if re.search("Reserved", name):
        # Airbnb
        if name == "Reserved":
            p = re.compile("([A-Z][A-Z0-9]{9})")
            return p.search(description)[0]
        else:
            p = re.compile(" - (.*)$")
            return p.findall(name)[0]

    # Tripadvisor
    if re.search("Tripadvisor", name):
        p = re.compile("Tripadvisor.*: (.*)")
        return p.findall(name)[0]

    # Guesty
    p = re.compile("-(.*)-.*-")
    return p.findall(name)[0]
