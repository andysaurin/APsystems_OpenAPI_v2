# SPDX-License-Identifier: GPL-3.0-or-later
"""APsystems OpenAPI client, written against User Manual v1.8 (2025/07/18).

Deliberately free of Home Assistant imports so it can be tested standalone.
Base URL: https://api.apsystemsema.com:9282
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.apsystemsema.com:9282"
API_PREFIX = "/user/api/v2/systems"

# Manual section 4.1 - Response Code Definition
RESPONSE_CODES: dict[int, str] = {
    0: "Succeed to request",
    1000: "Data exception",
    1001: "No data",
    2000: "Application account exception",
    2001: "Invalid application account",
    2002: "The application account is not authorized",
    2003: "Application account authorization expires",
    2004: "The application account has no permission",
    2005: "The access limit of the application account was exceeded",
    3000: "Access token exception",
    3001: "Missing Access token",
    3002: "Unable to verify Access token",
    3003: "Access token timeout",
    3004: "Refresh token timeout",
    4000: "Request parameter exception",
    4001: "Invalid request parameter",
    5000: "Internal server exception",
    6000: "Communication exception",
    7000: "Server access restriction exception",
    7001: "Server access limit exceeded",
    7002: "Too many requests, please request later",
    7003: "The system is busy, please request later",
}
# Codes meaning "quota exceeded / throttled" rather than "bad request"
RATE_LIMIT_CODES = {2005, 7001, 7002, 7003}

ENERGY_LEVELS = ("minutely", "hourly", "daily", "monthly", "yearly")
# date_range format each energy_level expects (manual 3.2.2 onwards)
DATE_FORMATS = {
    "minutely": "yyyy-MM-dd",
    "hourly": "yyyy-MM-dd",
    "daily": "yyyy-MM",
    "monthly": "yyyy",
    "yearly": None,  # date_range not required
}


class APsystemsError(Exception):
    """Base error."""


class APsystemsAuthError(APsystemsError):
    """Authentication / account problem (codes 2xxx except 2005, 3xxx, HTTP 401/403)."""


class APsystemsRateLimitError(APsystemsError):
    """Quota exceeded or server throttling (codes 2005, 7001-7003, HTTP 429)."""

    def __init__(self, message: str, code: int | None = None) -> None:
        self.code = code
        super().__init__(message)


class APsystemsAPIError(APsystemsError):
    """Any other non-zero response code."""

    def __init__(self, code: int, message: str | None = None) -> None:
        self.code = code
        super().__init__(f"API error {code}: {message or RESPONSE_CODES.get(code, 'Unknown')}")


class APsystemsClient:
    """Async client implementing every endpoint in the v1.8 manual."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        app_id: str,
        app_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        signature_method: str = "HmacSHA256",
    ) -> None:
        if signature_method not in ("HmacSHA256", "HmacSHA1"):
            raise ValueError("signature_method must be HmacSHA256 or HmacSHA1")
        self._session = session
        self._app_id = app_id
        self._app_secret = app_secret
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._sig_method = signature_method

    # -- Authentication (manual 2.2) ------------------------------------ #
    def _sign(self, timestamp: str, nonce: str, request_path: str, http_method: str) -> str:
        """Base64(HMAC(secret, ts/nonce/appId/RequestPath/HTTPMethod/SignatureMethod)).

        RequestPath is the LAST SEGMENT of the URL path (the sid/eid/uid), and
        query-string parameters are not part of the signature.
        """
        string_to_sign = "/".join(
            (timestamp, nonce, self._app_id, request_path, http_method, self._sig_method)
        )
        digest = hashlib.sha256 if self._sig_method == "HmacSHA256" else hashlib.sha1
        raw = hmac.new(
            self._app_secret.encode("utf-8"), string_to_sign.encode("utf-8"), digest
        ).digest()
        return base64.b64encode(raw).decode("ascii")

    def _headers(self, request_path: str, http_method: str = "GET") -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))  # milliseconds
        nonce = uuid.uuid4().hex  # 32-char string, no dashes
        return {
            "X-CA-AppId": self._app_id,
            "X-CA-Timestamp": timestamp,
            "X-CA-Nonce": nonce,
            "X-CA-Signature-Method": self._sig_method,
            "X-CA-Signature": self._sign(timestamp, nonce, request_path, http_method),
        }

    # -- Transport ------------------------------------------------------- #
    async def _get(self, path: str, params: dict[str, str] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        request_path = path.rstrip("/").rsplit("/", 1)[-1]
        async with self._session.get(
            url,
            params=params,
            headers=self._headers(request_path, "GET"),
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status in (401, 403):
                raise APsystemsAuthError(f"HTTP {resp.status}")
            if resp.status == 429:
                raise APsystemsRateLimitError("HTTP 429 Too Many Requests")
            resp.raise_for_status()
            # The API sometimes sends valid JSON with an odd content-type
            body = await resp.json(content_type=None)

        code = int(body.get("code", -1)) if isinstance(body, dict) else -1
        _LOGGER.debug("APsystems GET %s params=%s -> code %s", path, params, code)
        if code == 0:
            return body.get("data")
        msg = RESPONSE_CODES.get(code)
        if code in RATE_LIMIT_CODES:
            raise APsystemsRateLimitError(f"{msg} (code {code})", code)
        if 2000 <= code < 4000:
            raise APsystemsAuthError(f"{code}: {msg}")
        raise APsystemsAPIError(code, msg)

    @staticmethod
    def _period_params(energy_level: str, date_range: str | None, allowed=ENERGY_LEVELS):
        if energy_level not in allowed:
            raise ValueError(f"energy_level must be one of {allowed}")
        params = {"energy_level": energy_level}
        if date_range and DATE_FORMATS.get(energy_level):
            params["date_range"] = date_range
        return params

    # -- 3.1 System Details ---------------------------------------------- #
    async def get_system_details(self, sid: str) -> dict:
        """sid, create_date, capacity, type, timezone, ecu[], light, authorization_code."""
        return await self._get(f"{API_PREFIX}/details/{sid}")

    async def get_inverters(self, sid: str) -> list[dict]:
        """[{eid, type, timezone, inverter:[{uid,type}], model?, capacity?}]"""
        return await self._get(f"{API_PREFIX}/inverters/{sid}")

    async def get_meters(self, sid: str) -> list[str]:
        return await self._get(f"{API_PREFIX}/meters/{sid}")

    # -- 3.2 System-level ------------------------------------------------- #
    async def get_system_summary(self, sid: str) -> dict:
        """{today, month, year, lifetime} in kWh (strings)."""
        return await self._get(f"{API_PREFIX}/summary/{sid}")

    async def get_system_energy(
        self, sid: str, energy_level: str, date_range: str | None = None
    ) -> list[str]:
        """hourly/daily/monthly/yearly only (no 'minutely' at system level)."""
        params = self._period_params(
            energy_level, date_range, ("hourly", "daily", "monthly", "yearly")
        )
        return await self._get(f"{API_PREFIX}/energy/{sid}", params)

    # -- 3.3 ECU-level ----------------------------------------------------- #
    async def get_ecu_summary(self, sid: str, eid: str) -> dict:
        return await self._get(f"{API_PREFIX}/{sid}/devices/ecu/summary/{eid}")

    async def get_ecu_energy(
        self, sid: str, eid: str, energy_level: str, date_range: str | None = None
    ) -> list[str] | dict:
        params = self._period_params(energy_level, date_range)
        return await self._get(f"{API_PREFIX}/{sid}/devices/ecu/energy/{eid}", params)

    # -- 3.4 Meter-level ---------------------------------------------------- #
    async def get_meter_summary(self, sid: str, eid: str) -> dict:
        return await self._get(f"{API_PREFIX}/{sid}/devices/meter/summary/{eid}")

    async def get_meter_period(
        self, sid: str, eid: str, energy_level: str, date_range: str | None = None
    ) -> dict:
        params = self._period_params(energy_level, date_range)
        return await self._get(f"{API_PREFIX}/{sid}/devices/meter/period/{eid}", params)

    # -- 3.5 Inverter-level -------------------------------------------------- #
    async def get_inverter_summary(self, sid: str, uid: str) -> dict:
        """Per channel: d1..d4, m1..m4, y1..y4, t1..t4."""
        return await self._get(f"{API_PREFIX}/{sid}/devices/inverter/summary/{uid}")

    async def get_inverter_energy(
        self, sid: str, uid: str, energy_level: str, date_range: str | None = None
    ) -> dict:
        """hourly+: {e1..e4: [...]}; minutely: {t[], dc_p1.., dc_i1.., dc_v1.., dc_e1..,
        ac_v1..3, ac_t, ac_p, ac_f}"""
        params = self._period_params(energy_level, date_range)
        return await self._get(f"{API_PREFIX}/{sid}/devices/inverter/energy/{uid}", params)

    async def get_inverter_batch_energy(
        self, sid: str, eid: str, energy_level: str, date: str
    ) -> dict:
        """All inverters under an ECU for one day (both params required).

        energy_level 'energy' -> {energy: ["uid-channel-kWh", ...]}
        energy_level 'power'  -> {time: [...], power: {"uid-channel": [...]}}
        """
        if energy_level not in ("power", "energy"):
            raise ValueError("energy_level must be 'power' or 'energy'")
        params = {"energy_level": energy_level, "date_range": date}
        return await self._get(
            f"{API_PREFIX}/{sid}/devices/inverter/batch/energy/{eid}", params
        )

    # -- 3.6 Storage-level (manual v1.7+) ------------------------------------ #
    async def get_storage_latest(self, sid: str, eid: str) -> dict:
        return await self._get(f"{API_PREFIX}/{sid}/devices/storage/latest/{eid}")

    async def get_storage_summary(self, sid: str, eid: str) -> dict:
        return await self._get(f"{API_PREFIX}/{sid}/devices/storage/summary/{eid}")

    async def get_storage_period(
        self, sid: str, eid: str, energy_level: str, date_range: str | None = None
    ) -> dict:
        params = self._period_params(energy_level, date_range)
        return await self._get(f"{API_PREFIX}/{sid}/devices/storage/period/{eid}", params)
