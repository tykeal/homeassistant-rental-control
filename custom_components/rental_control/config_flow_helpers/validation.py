# SPDX-FileCopyrightText: 2026 Andrew Grimberg <tykeal@bardicgrove.org>
# SPDX-License-Identifier: Apache-2.0

"""Validation helpers for Rental Control config and options flows."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.const import CONF_URL
from homeassistant.const import CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from ..const import CONF_CHECKIN
from ..const import CONF_CHECKOUT
from ..const import CONF_CODE_GENERATION
from ..const import CONF_CODE_LENGTH
from ..const import CONF_CREATION_DATETIME
from ..const import CONF_DAYS
from ..const import CONF_EVENT_PREFIX
from ..const import CONF_GENERATE
from ..const import CONF_LOCK_ENTRY
from ..const import CONF_MAX_EVENTS
from ..const import CONF_MAX_NAME_LENGTH
from ..const import CONF_REFRESH_FREQUENCY
from ..const import CONF_TRIM_NAMES
from ..const import DEFAULT_CODE_LENGTH
from ..const import DEFAULT_GENERATE
from ..const import DEFAULT_MAX_NAME_LENGTH
from ..const import MIN_NAME_LENGTH
from ..const import REQUEST_TIMEOUT
from .models import FlowValidationResult
from .models import URLValidationResult
from .schemas import generator_convert
from .schemas import lock_entry_convert

_LOGGER = logging.getLogger("custom_components.rental_control.config_flow")


def normalize_lock_entry(value: Any) -> str:
    """Normalize cleared lock entry values to '(none)'."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "(none)"
    return str(value)


async def validate_submitted_data(
    flow: Any, user_input: dict[str, Any]
) -> FlowValidationResult:
    """Validate submitted user input while preserving mutation timing."""
    errors: dict[str, str] = {}
    description_placeholders: dict[str, str] = {}

    if CONF_LOCK_ENTRY in user_input:
        user_input[CONF_LOCK_ENTRY] = normalize_lock_entry(user_input[CONF_LOCK_ENTRY])

    if hasattr(flow, "_get_unique_id"):
        errors.update(await flow._get_unique_id(user_input))

    await validate_url(flow, user_input, errors)
    validate_scalar_fields(user_input, errors)
    convert_code_generator(user_input)
    validate_name_length(user_input, errors)
    validate_name_trimming(user_input, errors)

    return FlowValidationResult(
        user_input=user_input,
        errors=errors,
        description_placeholders=description_placeholders,
        can_create_entry=not errors,
    )


async def validate_url(
    flow: Any, user_input: dict[str, Any], errors: dict[str, str]
) -> URLValidationResult:
    """Validate the submitted calendar URL and update errors."""
    try:
        cv.url(user_input[CONF_URL])
        url_lower = user_input[CONF_URL].lower()
        is_https = url_lower.startswith("https://")

        if not is_https and user_input[CONF_VERIFY_SSL]:
            errors[CONF_URL] = "https_required"
            return URLValidationResult(error="https_required")

        session = async_get_clientsession(
            flow.hass, verify_ssl=user_input[CONF_VERIFY_SSL]
        )
        async with asyncio.timeout(REQUEST_TIMEOUT):
            resp = await session.get(user_input[CONF_URL])
        if resp.status != 200:
            _LOGGER.error(
                "%s returned %s - %s",
                user_input[CONF_URL],
                resp.status,
                resp.reason,
            )
            errors[CONF_URL] = "unknown"
            return URLValidationResult(
                error="unknown", status=resp.status, reason=resp.reason
            )
        if "text/calendar" not in resp.content_type:
            errors[CONF_URL] = "bad_ics"
            return URLValidationResult(error="bad_ics", content_type=resp.content_type)
    except cv.vol.Invalid as err:
        _LOGGER.exception(err.msg)
        errors[CONF_URL] = "invalid_url"
        return URLValidationResult(error="invalid_url")
    return URLValidationResult()


def validate_scalar_fields(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate scalar fields and preserve existing error keys."""
    if (
        user_input[CONF_REFRESH_FREQUENCY] < 0
        or user_input[CONF_REFRESH_FREQUENCY] > 1440
    ):
        errors[CONF_REFRESH_FREQUENCY] = "bad_refresh"

    try:
        cv.time(user_input[CONF_CHECKIN])
    except cv.vol.Invalid as err:
        _LOGGER.exception(err.msg)
        errors[CONF_CHECKIN] = "bad_time"

    try:
        cv.time(user_input[CONF_CHECKOUT])
    except cv.vol.Invalid as err:
        _LOGGER.exception(err.msg)
        errors[CONF_CHECKOUT] = "bad_time"

    if user_input[CONF_DAYS] < 1:
        errors[CONF_DAYS] = "bad_minimum"

    if user_input[CONF_MAX_EVENTS] < 1:
        errors[CONF_MAX_EVENTS] = "bad_minimum"

    if (
        user_input[CONF_CODE_LENGTH] < DEFAULT_CODE_LENGTH
        or (user_input[CONF_CODE_LENGTH] % 2) != 0
    ):
        errors[CONF_CODE_LENGTH] = "bad_code_length"


def convert_code_generator(user_input: dict[str, Any]) -> None:
    """Convert code-generator description to its stored type."""
    user_input[CONF_CODE_GENERATION] = generator_convert(
        ident=user_input[CONF_CODE_GENERATION], to_type=True
    )


def validate_name_length(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate max-name length after code-generator conversion."""
    if user_input.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH) < MIN_NAME_LENGTH:
        errors[CONF_MAX_NAME_LENGTH] = "bad_max_name_length"


def validate_name_trimming(user_input: dict[str, Any], errors: dict[str, str]) -> None:
    """Validate trim-name prefix boundary behavior."""
    if user_input.get(CONF_TRIM_NAMES, False) and user_input.get(CONF_EVENT_PREFIX, ""):
        prefix_len = len(user_input[CONF_EVENT_PREFIX]) + 1
        max_len = user_input.get(CONF_MAX_NAME_LENGTH, DEFAULT_MAX_NAME_LENGTH)
        if prefix_len > (max_len - MIN_NAME_LENGTH):
            errors["base"] = "prefix_too_long_for_trim"


def apply_successful_conversions(flow: Any, user_input: dict[str, Any]) -> None:
    """Apply conversions that only occur when validation has no errors."""
    if user_input[CONF_LOCK_ENTRY] == "(none)":
        user_input[CONF_LOCK_ENTRY] = None

    if user_input[CONF_LOCK_ENTRY] is not None:
        user_input[CONF_LOCK_ENTRY] = lock_entry_convert(
            flow.hass, user_input[CONF_LOCK_ENTRY], True
        )

    if hasattr(flow, "created"):
        user_input[CONF_CREATION_DATETIME] = flow.created

    if user_input[CONF_LOCK_ENTRY]:
        user_input[CONF_GENERATE] = DEFAULT_GENERATE
