# SPDX-License-Identifier: GPL-3.0-or-later
"""Coordinator for APsystems OpenAPI.

Keeps the original polling strategy (solar hours only, summary once per day near
the end of the solar day, per-inverter telemetry once a day after 12:30, batch
power once a day at 23:00) but as a proper DataUpdateCoordinator.

Optionally (2.0.1) it also polls the storage ECU(s) of an APsystems battery
system (summary + latest status) on every poll during solar hours.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    APsystemsAPIError,
    APsystemsAuthError,
    APsystemsClient,
    APsystemsError,
    APsystemsRateLimitError,
)
from .const import (
    CONF_AUTO_SCAN_INTERVAL,
    CONF_BATCH_POWER,
    CONF_INVERTER_TELEMETRY,
    CONF_SCAN_INTERVAL,
    CONF_SID,
    CONF_STORAGE,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    DEFAULT_BATCH_POWER,
    DEFAULT_INVERTER_TELEMETRY,
    DEFAULT_OFFSET,
    DEFAULT_STORAGE,
    DOMAIN,
    FALLBACK_SCAN_INTERVAL,
    INVERTER_ENERGY_HOUR,
    INVERTER_ENERGY_MINUTE,
    MONTHLY_API_QUOTA,
    STORAGE_ECU_TYPE,
    estimate_monthly_calls,
    recommended_scan_interval,
)

_LOGGER = logging.getLogger(__name__)

type ApsystemsConfigEntry = ConfigEntry[ApsystemsCoordinator]

# "Data exception" / "No data": nothing to report right now, not a failure
_NO_DATA_CODES = {1000, 1001}
_COMM_ERRORS = (aiohttp.ClientError, TimeoutError)


class ApsystemsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch and cache data for one APsystems system."""

    config_entry: ApsystemsConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ApsystemsConfigEntry, client: APsystemsClient
    ) -> None:
        self.sid: str = entry.data[CONF_SID]
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{self.sid}",
            update_interval=timedelta(
                seconds=int(self._option(entry, CONF_SCAN_INTERVAL, FALLBACK_SCAN_INTERVAL))
            ),
        )
        self.client = client
        # Discovered inverters: [{"eid", "uid", "type"}]; None = not discovered yet
        self.inverters: list[dict[str, str]] | None = None
        self.inverter_energy: dict[str, dict[str, Any]] = {}
        self.inverter_energy_date: str | None = None
        self.batch_power: dict[str, Any] = {}
        self.batch_power_date: str | None = None
        # Discovered storage ECUs: [{"eid", "model", "capacity"}]
        self.storage_ecus: list[dict[str, Any]] = []
        # Latest storage data per ECU id: {"summary": {...}, "latest": {...}, "fetched": iso}
        self.storage: dict[str, dict[str, Any]] = {}
        self.solar_active = False
        self._window_end: datetime | None = None
        self._summary: dict[str, Any] | None = None
        self._summary_date: str | None = None
        self._hourly: list[Any] | None = None
        self._date: str | None = None

    # ------------------------------------------------------------------ #
    # Options (new entries use entry.options, legacy ones kept them in .data)
    # ------------------------------------------------------------------ #
    @staticmethod
    def _option(entry: ConfigEntry, key: str, default: Any) -> Any:
        return entry.options.get(key, entry.data.get(key, default))

    def _opt(self, key: str, default: Any) -> Any:
        return self._option(self.config_entry, key, default)

    # ------------------------------------------------------------------ #
    # Solar window
    # ------------------------------------------------------------------ #
    def _update_solar_state(self) -> None:
        local_now = dt_util.now()
        today = local_now.date()
        sunrise = get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, today)
        sunset = get_astral_event_date(self.hass, SUN_EVENT_SUNSET, today)
        if sunrise and sunset:
            start = sunrise + timedelta(
                minutes=int(self._opt(CONF_SUNRISE_OFFSET, DEFAULT_OFFSET))
            )
            end = sunset + timedelta(minutes=int(self._opt(CONF_SUNSET_OFFSET, DEFAULT_OFFSET)))
            self.solar_active = start <= local_now <= end
            self._window_end = end
        else:  # polar day/night or astral failure
            self.solar_active = 7 <= local_now.hour <= 20
            self._window_end = None

    # ------------------------------------------------------------------ #
    # Optional features (each one spends additional API calls)
    # ------------------------------------------------------------------ #
    @property
    def telemetry_enabled(self) -> bool:
        """Per-inverter telemetry: one call per inverter per day."""
        return bool(self._opt(CONF_INVERTER_TELEMETRY, DEFAULT_INVERTER_TELEMETRY))

    @property
    def batch_power_enabled(self) -> bool:
        """Daily batch power fetch: one call per ECU per day."""
        return bool(self._opt(CONF_BATCH_POWER, DEFAULT_BATCH_POWER))

    @property
    def storage_enabled(self) -> bool:
        """Battery/storage data: two calls per storage ECU on every poll."""
        return bool(self._opt(CONF_STORAGE, DEFAULT_STORAGE))

    @property
    def _storage_calls_per_poll(self) -> int:
        """Extra calls per poll (summary + latest for each storage ECU)."""
        return 2 * len(self.storage_ecus) if self.storage_enabled else 0

    # ------------------------------------------------------------------ #
    # API budget
    # ------------------------------------------------------------------ #
    def _budget_counts(self) -> tuple[int, int]:
        """(inverters polled daily, ECUs polled daily) given the enabled features."""
        inverters = self.inverters or []
        n_inverters = len(inverters) if self.telemetry_enabled else 0
        n_ecus = len({inv["eid"] for inv in inverters}) if self.batch_power_enabled else 0
        return n_inverters, n_ecus

    def estimated_monthly_calls(self, interval_s: float | None = None) -> int | None:
        """Worst-case monthly calls for the current settings (None if unknown)."""
        latitude = self.hass.config.latitude
        if latitude is None:
            return None
        if interval_s is None:
            interval_s = (
                self.update_interval or timedelta(seconds=FALLBACK_SCAN_INTERVAL)
            ).total_seconds()
        n_inverters, n_ecus = self._budget_counts()
        return estimate_monthly_calls(
            int(interval_s),
            latitude,
            n_inverters,
            n_ecus,
            int(self._opt(CONF_SUNRISE_OFFSET, DEFAULT_OFFSET)),
            self._storage_calls_per_poll,
        )

    def apply_auto_interval(self) -> None:
        """Size the polling interval to the quota, then log the resulting estimate."""
        latitude = self.hass.config.latitude
        if self._opt(CONF_AUTO_SCAN_INTERVAL, True) and latitude is not None:
            n_inverters, n_ecus = self._budget_counts()
            recommended = recommended_scan_interval(
                latitude,
                n_inverters,
                n_ecus,
                int(self._opt(CONF_SUNRISE_OFFSET, DEFAULT_OFFSET)),
                num_storage=self._storage_calls_per_poll,
            )
            self.update_interval = timedelta(seconds=recommended)
        estimate = self.estimated_monthly_calls()
        if estimate is None:
            return
        n_inverters, n_ecus = self._budget_counts()
        _LOGGER.log(
            logging.WARNING if estimate > MONTHLY_API_QUOTA else logging.INFO,
            "APsystems %s: polling every %d s, telemetry for %d inverter(s), batch power for "
            "%d ECU(s), %d storage call(s) per poll - est. %d calls/month (quota %d)",
            self.sid,
            int(self.update_interval.total_seconds()) if self.update_interval else 0,
            n_inverters,
            n_ecus,
            self._storage_calls_per_poll,
            estimate,
            MONTHLY_API_QUOTA,
        )

    # ------------------------------------------------------------------ #
    # Repairs issue instead of a persistent notification
    # ------------------------------------------------------------------ #
    @property
    def _limit_issue_id(self) -> str:
        return f"api_limit_{self.sid}"

    def _report_limit(self, err: APsystemsRateLimitError) -> None:
        _LOGGER.error(
            "APsystems API limit reached (%s). Pausing until it resets - increase the "
            "scan interval to stay under the %d calls/month quota",
            err, MONTHLY_API_QUOTA,
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._limit_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="api_limit",
            translation_placeholders={"reason": str(err)},
        )

    def _clear_limit(self) -> None:
        ir.async_delete_issue(self.hass, DOMAIN, self._limit_issue_id)

    # ------------------------------------------------------------------ #
    # Data helpers
    # ------------------------------------------------------------------ #
    def _snapshot(self) -> dict[str, Any]:
        return {
            "summary": self._summary,
            "hourly": self._hourly,
            "date": self._date or dt_util.now().date().isoformat(),
            "solar_active": self.solar_active,
            "inverters": self.inverters or [],
            "inverter_energy": self.inverter_energy,
            "inverter_energy_date": self.inverter_energy_date,
            "batch_power": self.batch_power,
            "batch_power_date": self.batch_power_date,
            "storage": self.storage,
        }

    def _push(self) -> None:
        """Publish cache changes made outside a normal refresh (buttons, schedules)."""
        if self.data is not None:
            self.async_set_updated_data(self._snapshot())

    @staticmethod
    async def _optional(coro) -> Any:
        try:
            return await coro
        except APsystemsAPIError as err:
            if err.code in _NO_DATA_CODES:
                return None
            raise

    # ------------------------------------------------------------------ #
    # Public refresh operations (used by buttons and schedules)
    # ------------------------------------------------------------------ #
    async def async_refresh_inverter_list(self, push: bool = True) -> list[dict[str, str]]:
        """Fetch the ECU/inverter list. Raises APsystemsError on failure."""
        raw = await self.client.get_inverters(self.sid) or []
        parsed = [
            {"eid": ecu["eid"], "uid": inv["uid"], "type": inv.get("type") or ""}
            for ecu in raw
            for inv in ecu.get("inverter") or []
        ]
        self.inverters = parsed
        # An ECU with storage activated (e.g. ELT-12) has type 2 and no inverters
        self.storage_ecus = [
            {
                "eid": ecu["eid"],
                "model": ecu.get("model") or "",
                "capacity": ecu.get("capacity"),
            }
            for ecu in raw
            if ecu.get("type") == STORAGE_ECU_TYPE
        ]
        _LOGGER.info(
            "Discovered %d inverter(s) and %d storage ECU(s)",
            len(parsed),
            len(self.storage_ecus),
        )
        if push:
            self._push()
        return parsed

    async def async_refresh_inverter_energy(
        self, push: bool = True
    ) -> dict[str, dict[str, Any]]:
        """Minutely telemetry for every inverter (one call each)."""
        if not self.telemetry_enabled:
            return {}
        date_str = dt_util.now().date().isoformat()
        energy: dict[str, dict[str, Any]] = {}
        for inv in self.inverters or []:
            uid = inv["uid"]
            try:
                data = await self._optional(
                    self.client.get_inverter_energy(self.sid, uid, "minutely", date_str)
                )
            except APsystemsRateLimitError as err:
                self._report_limit(err)
                break  # stop hammering the API
            except APsystemsAuthError:
                raise
            except (APsystemsError, *_COMM_ERRORS) as err:
                _LOGGER.warning("Failed to fetch energy for inverter %s: %s", uid, err)
                continue
            if data:
                energy[uid] = data
        self.inverter_energy = energy
        self.inverter_energy_date = date_str
        _LOGGER.info("Inverter energy fetched for %s (%d inverters)", date_str, len(energy))
        if push:
            self._push()
        return energy

    async def async_refresh_batch_power(self, push: bool = True) -> dict[str, Any]:
        """Power telemetry for all inverters, one call per ECU."""
        if not self.batch_power_enabled:
            return {}
        date_str = dt_util.now().date().isoformat()
        batch: dict[str, Any] = {}
        for eid in sorted({inv["eid"] for inv in self.inverters or []}):
            try:
                data = await self._optional(
                    self.client.get_inverter_batch_energy(self.sid, eid, "power", date_str)
                )
            except APsystemsRateLimitError as err:
                self._report_limit(err)
                break
            except APsystemsAuthError:
                raise
            except (APsystemsError, *_COMM_ERRORS) as err:
                _LOGGER.warning("Failed to fetch batch power for ECU %s: %s", eid, err)
                continue
            if data:
                batch[eid] = data
        self.batch_power = batch
        self.batch_power_date = date_str
        _LOGGER.info("Batch power fetched for %s (%d ECUs)", date_str, len(batch))
        if push:
            self._push()
        return batch

    async def async_refresh_storage(self, push: bool = True) -> dict[str, dict[str, Any]]:
        """Summary and latest status of every storage ECU (two calls each)."""
        if not self.storage_enabled:
            return {}
        limited = False
        for ecu in self.storage_ecus:
            eid = ecu["eid"]
            entry = dict(self.storage.get(eid) or {})
            for key, fetch in (
                ("summary", self.client.get_storage_summary),
                ("latest", self.client.get_storage_latest),
            ):
                try:
                    data = await self._optional(fetch(self.sid, eid))
                except APsystemsRateLimitError as err:
                    self._report_limit(err)
                    limited = True
                    break
                except APsystemsAuthError:
                    raise
                except (APsystemsError, *_COMM_ERRORS) as err:
                    _LOGGER.warning("Failed to fetch storage %s for ECU %s: %s", key, eid, err)
                    continue
                if data:
                    entry[key] = data
            entry["fetched"] = dt_util.now().isoformat()
            self.storage[eid] = entry
            if limited:
                break
        _LOGGER.info("Storage data fetched (%d ECU(s))", len(self.storage))
        if push:
            self._push()
        return self.storage

    # ------------------------------------------------------------------ #
    # Regular poll
    # ------------------------------------------------------------------ #
    async def _async_update_data(self) -> dict[str, Any]:
        self._update_solar_state()
        try:
            if self.inverters is None:
                if self.telemetry_enabled or self.batch_power_enabled or self.storage_enabled:
                    await self.async_refresh_inverter_list(push=False)
                else:
                    self.inverters = []  # nothing needs it: save the call

            if not self.solar_active:
                _LOGGER.debug("Outside solar hours, returning cached data")
                # Fetch storage once if nothing is cached yet (e.g. after a restart
                # at night) so the entities have values; no repeat calls after that.
                if self.storage_enabled and self.storage_ecus and not self.storage:
                    await self.async_refresh_storage(push=False)
                return self._snapshot()

            date_str = dt_util.now().date().isoformat()
            hourly = await self._optional(
                self.client.get_system_energy(self.sid, "hourly", date_str)
            )
            self._hourly = hourly or []
            self._date = date_str

            await self._maybe_fetch_summary(date_str)

            if self.storage_enabled:
                await self.async_refresh_storage(push=False)

            now = dt_util.now()
            past_energy_time = (now.hour, now.minute) >= (
                INVERTER_ENERGY_HOUR,
                INVERTER_ENERGY_MINUTE,
            )
            if (
                self.telemetry_enabled
                and past_energy_time
                and self.inverter_energy_date != date_str
            ):
                await self.async_refresh_inverter_energy(push=False)

            self._clear_limit()
            return self._snapshot()
        except APsystemsAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except APsystemsRateLimitError as err:
            self._report_limit(err)
            if self._summary is not None:
                return self._snapshot()  # keep entities available on cached data
            raise UpdateFailed(str(err)) from err
        except APsystemsError as err:
            raise UpdateFailed(str(err)) from err
        except _COMM_ERRORS as err:
            raise UpdateFailed(f"Communication error: {err}") from err

    async def _maybe_fetch_summary(self, date_str: str) -> None:
        need = self._summary is None
        if not need and self._summary_date != date_str and self._window_end is not None:
            interval = self.update_interval or timedelta(seconds=FALLBACK_SCAN_INTERVAL)
            if dt_util.now() + interval >= self._window_end:
                need = True
                _LOGGER.debug("Near end of solar day, fetching daily summary")
        if not need:
            return
        try:
            self._summary = await self.client.get_system_summary(self.sid)
            self._summary_date = date_str
            _LOGGER.info("Daily summary fetched for %s", date_str)
        except APsystemsAPIError as err:
            if self._summary is None:
                raise
            _LOGGER.warning("APsystems summary error, keeping previous value: %s", err)
