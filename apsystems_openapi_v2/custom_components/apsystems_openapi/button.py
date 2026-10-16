# SPDX-License-Identifier: GPL-3.0-or-later
"""Buttons for APsystems OpenAPI."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import aiohttp
from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import APsystemsAuthError, APsystemsError
from .coordinator import ApsystemsConfigEntry, ApsystemsCoordinator
from .sensor import system_device_info

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class ApsystemsButtonDescription(ButtonEntityDescription):
    press_fn: Callable[[ApsystemsCoordinator], Awaitable[object]]


# `key` is the unique-id suffix and matches earlier versions
BUTTONS: tuple[ApsystemsButtonDescription, ...] = (
    ApsystemsButtonDescription(
        key="scan_inverters",
        translation_key="scan_inverters",
        icon="mdi:magnify-scan",
        press_fn=lambda c: c.async_refresh_inverter_list(),
    ),
    ApsystemsButtonDescription(
        key="refresh_inverter_energy",
        translation_key="refresh_inverter_energy",
        icon="mdi:solar-panel",
        press_fn=lambda c: c.async_refresh_inverter_energy(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ApsystemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    if coordinator.telemetry_enabled:  # both buttons act on inverter telemetry
        async_add_entities(ApsystemsButton(coordinator, d) for d in BUTTONS)


class ApsystemsButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: ApsystemsButtonDescription

    def __init__(
        self, coordinator: ApsystemsCoordinator, description: ApsystemsButtonDescription
    ) -> None:
        self.coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.sid}_{description.key}"
        self._attr_device_info = system_device_info(coordinator.sid)

    async def async_press(self) -> None:
        try:
            await self.entity_description.press_fn(self.coordinator)
        except APsystemsAuthError as err:
            self.coordinator.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Authentication failed: {err}") from err
        except (APsystemsError, aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"APsystems request failed: {err}") from err
