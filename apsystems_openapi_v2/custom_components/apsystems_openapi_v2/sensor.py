# SPDX-License-Identifier: GPL-3.0-or-later
"""Sensors for APsystems OpenAPI (entity ids / unique ids match earlier versions)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import StateType
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import ApsystemsConfigEntry, ApsystemsCoordinator

PARALLEL_UPDATES = 0


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest(series: Any, digits: int) -> float | None:
    """Last non-null, non-NaN numeric value of a time series."""
    for v in reversed(series or []):
        f = _to_float(v)
        if f is not None and f == f:
            return round(f, digits)
    return None


def _peak(series: Any) -> float | None:
    vals = [f for f in (_to_float(x) for x in (series or [])) if f is not None]
    return round(max(vals), 1) if vals else None


def system_device_info(sid: str) -> DeviceInfo:
    """Device shared by the system sensors and the buttons."""
    return DeviceInfo(
        identifiers={(DOMAIN, sid)}, manufacturer="APsystems", name=f"APsystems {sid}"
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ApsystemsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [ApsystemsTotalEnergySensor(coordinator), ApsystemsTodayEnergySensor(coordinator)]
    )

    # Inverter sensors: created for what is known now, and for any inverter that
    # appears later (e.g. after pressing the "Scan Inverters" button).
    known: set[str] = set()

    @callback
    def _add_new_inverters() -> None:
        new: list[SensorEntity] = []
        for inv in coordinator.inverters or []:
            if inv["uid"] in known:
                continue
            known.add(inv["uid"])
            new.append(ApsystemsInverterPowerSensor(coordinator, inv))
            new.extend(ApsystemsInverterSensor(coordinator, inv, d) for d in INVERTER_SENSORS)
        if new:
            async_add_entities(new)

    if coordinator.telemetry_enabled:
        _add_new_inverters()
        entry.async_on_unload(coordinator.async_add_listener(_add_new_inverters))


class ApsystemsBaseEntity(CoordinatorEntity[ApsystemsCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ApsystemsCoordinator, unique_suffix: str) -> None:
        super().__init__(coordinator)
        self._sid = coordinator.sid
        self._attr_unique_id = f"{self._sid}_{unique_suffix}"
        self._attr_device_info = system_device_info(self._sid)

    @property
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data or {}


# --------------------------------------------------------------------------- #
# System sensors
# --------------------------------------------------------------------------- #
class ApsystemsTotalEnergySensor(ApsystemsBaseEntity, RestoreEntity):
    """Monotonic lifetime kWh for the Energy dashboard."""

    _attr_translation_key = "total_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: ApsystemsCoordinator) -> None:
        super().__init__(coordinator, "total_energy")
        self._last_valid_value: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            val = _to_float(last_state.state)
            if val is not None and val > 0:
                self._last_valid_value = val

    @property
    def native_value(self) -> StateType:
        summary = self._data.get("summary") or {}
        val = _to_float(summary.get("lifetime"))
        if val is not None and val > 0:
            self._last_valid_value = val
            return val
        return self._last_valid_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self._data.get("summary") or {}
        active = self._data.get("solar_active", True)
        return {
            "today_kwh": _to_float(summary.get("today")),
            "month_kwh": _to_float(summary.get("month")),
            "year_kwh": _to_float(summary.get("year")),
            "hourly_kwh": self._data.get("hourly"),
            "hourly_date": self._data.get("date"),
            "source": "APsystems OpenAPI",
            "solar_hours_active": active,
            "status": "Solar hours" if active else "Night hours (cached data)",
        }


class ApsystemsTodayEnergySensor(ApsystemsBaseEntity, RestoreEntity):
    """Daily energy (kWh); resets at local midnight."""

    _attr_translation_key = "today_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, coordinator: ApsystemsCoordinator) -> None:
        super().__init__(coordinator, "today_energy")
        self._last_valid_value: float | None = None
        self._last_date: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._last_valid_value = _to_float(last_state.state)
            if last_state.attributes:
                self._last_date = last_state.attributes.get("hourly_date")

    @property
    def last_reset(self) -> datetime | None:
        return dt_util.now().replace(hour=0, minute=0, second=0, microsecond=0)

    @property
    def native_value(self) -> StateType:
        today = dt_util.now().date().isoformat()
        if self._last_date and self._last_date != today:  # midnight rollover
            self._last_valid_value = 0.0
            self._last_date = today

        if self._data.get("date") == today:
            series = [_to_float(x) for x in (self._data.get("hourly") or [])]
            total = round(sum(x for x in series if x is not None), 3)
            if total > 0:
                self._last_valid_value, self._last_date = total, today
                return total
            today_val = _to_float((self._data.get("summary") or {}).get("today"))
            if today_val is not None and today_val > 0:
                self._last_valid_value, self._last_date = today_val, today
                return today_val

        if self._last_date is None:
            self._last_date = today
        return self._last_valid_value if self._last_valid_value is not None else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        active = self._data.get("solar_active", True)
        return {
            "hourly_kwh": self._data.get("hourly"),
            "hourly_date": self._last_date or self._data.get("date"),
            "solar_hours_active": active,
            "status": "Solar hours" if active else "Night hours (cached data)",
        }


# --------------------------------------------------------------------------- #
# Inverter sensors (minutely telemetry, manual 3.5.2)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, kw_only=True)
class ApsystemsInverterSensorDescription(SensorEntityDescription):
    """`field_keys` are tried in order; the first one present in the data wins."""

    field_keys: tuple[str, ...]
    digits: int = 2


def _desc(key, fields, device_class, unit) -> ApsystemsInverterSensorDescription:
    return ApsystemsInverterSensorDescription(
        key=key,
        translation_key=key,
        field_keys=(fields,) if isinstance(fields, str) else fields,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.MEASUREMENT,
    )


# `key` doubles as the unique-id suffix and must not change (keeps entity history)
INVERTER_SENSORS: tuple[ApsystemsInverterSensorDescription, ...] = (
    _desc("dc_power_ch1", "dc_p1", SensorDeviceClass.POWER, UnitOfPower.WATT),
    _desc("dc_power_ch2", "dc_p2", SensorDeviceClass.POWER, UnitOfPower.WATT),
    _desc("dc_voltage_ch1", "dc_v1", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    _desc("dc_voltage_ch2", "dc_v2", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    _desc("dc_current_ch1", "dc_i1", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
    _desc("dc_current_ch2", "dc_i2", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
    _desc("ac_voltage", "ac_v1", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    _desc("frequency", "ac_f", SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    _desc("temperature", "ac_t", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
)

# The manual (3.5.2) names the AC power series `ac_p`; the previous integration
# read `ac_p1`. Accept either so both firmware/API variants keep working.
INVERTER_POWER_DESCRIPTION = _desc(
    "power", ("ac_p", "ac_p1"), SensorDeviceClass.POWER, UnitOfPower.WATT
)


class ApsystemsInverterSensor(ApsystemsBaseEntity):
    """Latest value of one telemetry series of one inverter."""

    entity_description: ApsystemsInverterSensorDescription

    def __init__(
        self,
        coordinator: ApsystemsCoordinator,
        inverter: dict[str, str],
        description: ApsystemsInverterSensorDescription,
    ) -> None:
        self._uid = inverter["uid"]
        self._eid = inverter.get("eid", "")
        self._inv_type = inverter.get("type") or "Unknown"
        super().__init__(coordinator, f"inverter_{self._uid}_{description.key}")
        self.entity_description = description
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._uid)},
            manufacturer="APsystems",
            name=f"Inverter {self._uid}",
            model=self._inv_type,
            via_device=(DOMAIN, self._sid),
        )

    @property
    def _telemetry(self) -> dict[str, Any]:
        return (self._data.get("inverter_energy") or {}).get(self._uid) or {}

    def _series(self, *keys: str) -> Any:
        for key in keys:
            if key in self._telemetry:
                return self._telemetry[key]
        return []

    @property
    def native_value(self) -> StateType:
        desc = self.entity_description
        return _latest(self._series(*desc.field_keys), desc.digits)


class ApsystemsInverterPowerSensor(ApsystemsInverterSensor):
    """AC output power, with the daily series exposed as attributes."""

    def __init__(self, coordinator: ApsystemsCoordinator, inverter: dict[str, str]) -> None:
        super().__init__(
            coordinator,
            inverter,
            replace(INVERTER_POWER_DESCRIPTION, digits=1),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dc_p1, dc_p2 = self._series("dc_p1"), self._series("dc_p2")
        ac_p = self._series("ac_p", "ac_p1")
        return {
            "inverter_uid": self._uid,
            "inverter_type": self._inv_type,
            "ecu_id": self._eid,
            "dc_channel1_power_w": _latest(dc_p1, 1),
            "dc_channel2_power_w": _latest(dc_p2, 1),
            "dc_channel1_peak_w": _peak(dc_p1),
            "dc_channel2_peak_w": _peak(dc_p2),
            "ac_power_peak_w": _peak(ac_p),
            "hourly_ac_power": ac_p,
            "hourly_dc_p1": dc_p1,
            "hourly_dc_p2": dc_p2,
            "hourly_times": self._series("t"),
            "data_date": self._data.get("inverter_energy_date"),
        }
