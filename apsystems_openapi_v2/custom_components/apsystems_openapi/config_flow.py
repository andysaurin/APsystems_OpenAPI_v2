# SPDX-License-Identifier: GPL-3.0-or-later
"""Config flow for APsystems OpenAPI."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    APsystemsAPIError,
    APsystemsAuthError,
    APsystemsClient,
    APsystemsError,
    APsystemsRateLimitError,
)
from .const import (
    CONF_APP_ID,
    CONF_APP_SECRET,
    CONF_AUTO_SCAN_INTERVAL,
    CONF_BASE_URL,
    CONF_BATCH_POWER,
    CONF_INVERTER_TELEMETRY,
    CONF_SCAN_INTERVAL,
    CONF_SID,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
    DEFAULT_BASE_URL,
    DEFAULT_BATCH_POWER,
    DEFAULT_INVERTER_TELEMETRY,
    DEFAULT_OFFSET,
    DOMAIN,
    FALLBACK_SCAN_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    MONTHLY_API_QUOTA,
)
from .coordinator import ApsystemsConfigEntry

_LOGGER = logging.getLogger(__name__)

_PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

OPTION_KEYS = (
    CONF_INVERTER_TELEMETRY,
    CONF_BATCH_POWER,
    CONF_AUTO_SCAN_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SUNRISE_OFFSET,
    CONF_SUNSET_OFFSET,
)


def _polling_schema(current: Mapping[str, Any]) -> dict:
    return {
        vol.Optional(
            CONF_INVERTER_TELEMETRY,
            default=current.get(CONF_INVERTER_TELEMETRY, DEFAULT_INVERTER_TELEMETRY),
        ): bool,
        vol.Optional(
            CONF_BATCH_POWER, default=current.get(CONF_BATCH_POWER, DEFAULT_BATCH_POWER)
        ): bool,
        vol.Optional(
            CONF_AUTO_SCAN_INTERVAL, default=current.get(CONF_AUTO_SCAN_INTERVAL, True)
        ): bool,
        vol.Optional(
            CONF_SCAN_INTERVAL, default=current.get(CONF_SCAN_INTERVAL, FALLBACK_SCAN_INTERVAL)
        ): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL)),
        vol.Optional(
            CONF_SUNRISE_OFFSET, default=current.get(CONF_SUNRISE_OFFSET, DEFAULT_OFFSET)
        ): vol.All(int, vol.Range(min=0, max=120)),
        vol.Optional(
            CONF_SUNSET_OFFSET, default=current.get(CONF_SUNSET_OFFSET, DEFAULT_OFFSET)
        ): vol.All(int, vol.Range(min=0, max=120)),
    }


async def _validate(
    hass: HomeAssistant, app_id: str, app_secret: str, sid: str, base_url: str
) -> str | None:
    """Return an error key, or None when credentials and system id are accepted."""
    client = APsystemsClient(async_get_clientsession(hass), app_id, app_secret, base_url)
    try:
        await client.get_system_details(sid)
    except APsystemsAuthError:
        return "invalid_auth"
    except APsystemsRateLimitError:
        return "rate_limited"
    except APsystemsAPIError as err:
        return "invalid_system" if err.code in (1000, 1001, 4000, 4001) else "cannot_connect"
    except (APsystemsError, aiohttp.ClientError, TimeoutError):
        return "cannot_connect"
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error validating APsystems credentials")
        return "unknown"
    return None


class ApsystemsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = {k: v.strip() if isinstance(v, str) else v for k, v in user_input.items()}
            user_input[CONF_BASE_URL] = user_input.get(CONF_BASE_URL) or DEFAULT_BASE_URL
            await self.async_set_unique_id(user_input[CONF_SID])
            self._abort_if_unique_id_configured()
            error = await _validate(
                self.hass,
                user_input[CONF_APP_ID],
                user_input[CONF_APP_SECRET],
                user_input[CONF_SID],
                user_input[CONF_BASE_URL],
            )
            if error is None:
                return self.async_create_entry(
                    title=f"APsystems {user_input[CONF_SID]}",
                    data={
                        k: user_input[k]
                        for k in (CONF_APP_ID, CONF_APP_SECRET, CONF_SID, CONF_BASE_URL)
                    },
                    options={k: user_input[k] for k in OPTION_KEYS if k in user_input},
                )
            errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_APP_ID): str,
                vol.Required(CONF_APP_SECRET): _PASSWORD,
                vol.Required(CONF_SID): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                **_polling_schema({}),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            secret = user_input[CONF_APP_SECRET].strip()
            error = await _validate(
                self.hass,
                entry.data[CONF_APP_ID],
                secret,
                entry.data[CONF_SID],
                entry.data.get(CONF_BASE_URL) or DEFAULT_BASE_URL,
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_APP_SECRET: secret}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_APP_SECRET): _PASSWORD}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ApsystemsConfigEntry) -> OptionsFlow:
        return ApsystemsOptionsFlow()


class ApsystemsOptionsFlow(OptionsFlow):
    """Polling options (stored in entry.options)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        # Legacy entries stored these in entry.data; options take precedence
        current = {**self.config_entry.data, **self.config_entry.options}
        estimate = "unknown"
        if self.config_entry.state is ConfigEntryState.LOADED:
            value = self.config_entry.runtime_data.estimated_monthly_calls()
            if value is not None:
                estimate = str(value)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_polling_schema(current)),
            description_placeholders={
                "estimate": estimate,
                "quota": str(MONTHLY_API_QUOTA),
            },
        )
