# SPDX-License-Identifier: GPL-3.0-or-later
"""The APsystems OpenAPI integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_sunrise, async_track_time_change

from .api import APsystemsClient
from .const import (
    BATCH_POWER_HOUR,
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_BASE_URL,
    CONF_SID,
    CONF_SUNRISE_OFFSET,
    DEFAULT_BASE_URL,
    DEFAULT_OFFSET,
    DOMAIN,
)
from .coordinator import ApsystemsConfigEntry, ApsystemsCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ApsystemsConfigEntry) -> bool:
    """Set up APsystems OpenAPI from a config entry."""
    client = APsystemsClient(
        async_get_clientsession(hass),
        entry.data[CONF_APP_ID],
        entry.data[CONF_APP_SECRET],
        entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL,
    )
    coordinator = ApsystemsCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    coordinator.apply_auto_interval()
    entry.runtime_data = coordinator

    # -- Scheduled jobs (all unsubscribed automatically on unload) ---------- #
    sunrise_offset = int(
        entry.options.get(CONF_SUNRISE_OFFSET, entry.data.get(CONF_SUNRISE_OFFSET, DEFAULT_OFFSET))
    )

    async def _refresh(_now: datetime | None = None) -> None:
        await coordinator.async_request_refresh()

    async def _batch_power(_now: datetime) -> None:
        await coordinator.async_refresh_batch_power()

    # Start polling as soon as the solar window opens
    entry.async_on_unload(
        async_track_sunrise(hass, _refresh, timedelta(minutes=sunrise_offset))
    )
    # Daily batch power fetch at 23:00 local time (optional, costs 1 call per ECU)
    if coordinator.batch_power_enabled:
        entry.async_on_unload(
            async_track_time_change(
                hass, _batch_power, hour=BATCH_POWER_HOUR, minute=0, second=0
            )
        )
    # Midnight refresh so daily sensors roll over (no API call outside solar hours)
    entry.async_on_unload(
        async_track_time_change(hass, _refresh, hour=0, minute=0, second=0)
    )

    # -- Remove devices for inverters the API no longer reports -------------- #
    @callback
    def _prune_stale_inverter_devices() -> None:
        if coordinator.telemetry_enabled:
            inverters = coordinator.inverters
            if not inverters:  # never prune on a transient empty list
                return
            live_ids = {entry.data[CONF_SID]} | {inv["uid"] for inv in inverters}
        else:
            # Telemetry switched off: inverter devices and their entities are removed
            live_ids = {entry.data[CONF_SID]}
        device_reg = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(device_reg, entry.entry_id):
            ids = {ident for (domain, ident) in device.identifiers if domain == DOMAIN}
            if ids and ids.isdisjoint(live_ids):
                _LOGGER.info("Removing stale APsystems inverter device %s", ", ".join(sorted(ids)))
                device_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    _remove_disabled_buttons(hass, entry, coordinator)
    _prune_stale_inverter_devices()
    entry.async_on_unload(coordinator.async_add_listener(_prune_stale_inverter_devices))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


@callback
def _remove_disabled_buttons(
    hass: HomeAssistant, entry: ApsystemsConfigEntry, coordinator: ApsystemsCoordinator
) -> None:
    """The inverter buttons only make sense while inverter telemetry is enabled."""
    if coordinator.telemetry_enabled:
        return
    ent_reg = er.async_get(hass)
    for key in ("scan_inverters", "refresh_inverter_energy"):
        entity_id = ent_reg.async_get_entity_id(
            Platform.BUTTON, DOMAIN, f"{entry.data[CONF_SID]}_{key}"
        )
        if entity_id:
            ent_reg.async_remove(entity_id)


async def _async_options_updated(hass: HomeAssistant, entry: ApsystemsConfigEntry) -> None:
    """Reload so new polling options take effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ApsystemsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ApsystemsConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting an inverter device from the UI once it is no longer reported."""
    ids = {ident for (domain, ident) in device.identifiers if domain == DOMAIN}
    if entry.data[CONF_SID] in ids:
        return False  # the system device goes away by deleting the integration
    live = {inv["uid"] for inv in (entry.runtime_data.inverters or [])}
    return ids.isdisjoint(live)
