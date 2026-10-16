# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostics for APsystems OpenAPI."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import ApsystemsConfigEntry

TO_REDACT = {"app_id", "app_secret", "sid", "authorization_code"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ApsystemsConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "update_interval_s": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
        "inverters": coordinator.inverters,
        "data": async_redact_data(coordinator.data or {}, TO_REDACT),
    }
