# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Step transition helpers for Rental Control config and options flows."""

from __future__ import annotations

from typing import Any

from .models import ConfigFormContext
from .models import FlowTransitionRequest
from .validation import apply_successful_conversions
from .validation import validate_submitted_data


async def start_config_flow(request: FlowTransitionRequest) -> Any:
    """Drive one config or options flow step."""
    errors: dict[str, str] = {}
    description_placeholders: dict[str, str] = {}

    if request.user_input is None:
        return _show_form(request, request.user_input, errors, description_placeholders)

    result = await validate_submitted_data(request.flow, request.user_input)
    if result.can_create_entry:
        apply_successful_conversions(request.flow, result.user_input)
        return request.flow.async_create_entry(
            title=request.title,
            data=result.user_input,
        )

    return _show_form(
        request,
        result.user_input,
        result.errors,
        result.description_placeholders,
    )


def _show_form(
    request: FlowTransitionRequest,
    user_input: dict[str, Any] | None,
    errors: dict[str, str],
    placeholders: dict[str, str],
) -> Any:
    """Render a config or options form from grouped context."""
    return request.form_renderer(
        request.flow,
        ConfigFormContext(
            step_id=request.step_id,
            user_input=user_input,
            errors=errors,
            description_placeholders=placeholders,
            defaults=request.defaults,
            entry_id=request.entry_id,
        ),
    )
