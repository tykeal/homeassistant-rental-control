# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Shared entity lifecycle helpers for Rental Control entities.

Home Assistant 2026.11 changes an entity's ``entity_id`` in place and
calls ``async_entity_id_changed`` instead of removing and re-adding the
entity. Custom classes that implement ``async_added_to_hass`` or
``async_will_remove_from_hass`` only opt in when that same class, or a
subclass of it, defines ``async_entity_id_changed``. A mixin placed
earlier in the MRO does not count, so each such class defines the hook
itself and delegates to :func:`async_chain_entity_id_changed`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import callback


@callback
def async_chain_entity_id_changed(parent: Any, old_entity_id: str) -> None:
    """Call the parent ``async_entity_id_changed`` hook when it exists.

    Home Assistant releases before 2026.11 lack the hook and never call
    it, so the lookup is guarded to stay compatible with them.

    Args:
        parent: The ``super()`` proxy of the calling entity.
        old_entity_id: The entity_id before the registry change.
    """
    hook = getattr(parent, "async_entity_id_changed", None)
    if hook is not None:
        hook(old_entity_id)
