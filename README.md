# APsystems OpenAPI for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![License: GPL v3+](https://img.shields.io/badge/License-GPLv3%2B-blue.svg)](LICENSE)

A custom [Home Assistant](https://www.home-assistant.io/) integration that reads solar production data from the APsystems OpenAPI cloud service (the same data you see in the EMA app and website).

It exposes your system's lifetime and daily energy for the Energy dashboard, plus optional per-inverter telemetry (AC/DC power, voltage, current, frequency and temperature).

> Forked from <https://blackhole.nmrc.org/solar/apsystems-openapi>. Version 2 is a rewrite of the integration internals against the APsystems OpenAPI User Manual v1.8 (July 2025) and current Home Assistant conventions. Entity unique IDs are unchanged from earlier versions, so existing history and dashboards carry over. See the [changelog](CHANGELOG.md).

## Features

- **Energy dashboard ready**: a lifetime `total_increasing` energy sensor and a daily energy sensor.
- **Optional per-inverter sensors**: AC power, DC power/voltage/current for channels 1 and 2, AC voltage, grid frequency and temperature, each inverter as its own device.
- **You choose what spends API calls**: inverter telemetry and batch power collection can each be switched on or off, and the options dialog shows the estimated monthly call usage.
- **Solar-hours-only polling** with configurable sunrise/sunset offsets.
- **API budget awareness**: the polling interval can be sized automatically to stay under the monthly call quota, and a Home Assistant *Repairs* issue is raised if the API reports that a limit was exceeded.
- **Manual buttons** to rescan inverters or refresh inverter telemetry on demand.
- **Automatic stale-device cleanup** for inverters the API no longer reports.
- **Standard Home Assistant plumbing**: config flow, options flow, re-authentication, diagnostics and translations.
- **Standalone API client** (`api.py`) covering every endpoint in the v1.8 manual.

## Requirements

- Home Assistant **2025.1** or newer.
- An APsystems **OpenAPI account**, which is separate from your normal EMA login. See [Getting API credentials](#getting-api-credentials).
- Your **System ID (SID)** from EMA.

## Getting API credentials

1. Email APsystems support and request an OpenAPI account. Their manual asks you to say who you are, why you want access, and what you will do with the data.
2. When approved you receive an **App ID** (a 32-character string) and an **App Secret** (a 12-character string). Keep these private.
3. Find your **System ID (SID)** in EMA. It looks like `AZ12649A3DFF`.

APsystems limits the number of API calls per account. The original project documents an allowance of **1000 calls per month**, and the manual notes that access count and data range can affect payment, so check the terms of your own account. See [API call budget](#api-call-budget).

## Installation

### HACS (recommended)

1. In Home Assistant open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/andysaurin/APsystems_OpenAPI_v2` with category **Integration**.
3. Search for **APsystems OpenAPI** in HACS, click **Download**, and restart Home Assistant.

### Manual

1. Download this repository (or the release zip).
2. Copy the `custom_components/apsystems_openapi` folder into your Home Assistant configuration directory so you end up with `config/custom_components/apsystems_openapi/__init__.py` and the other files next to it.
3. Restart Home Assistant.

### Upgrading from 1.x

Install over the old version and restart. If you installed manually, replace the old `apsystems_openapi` folder; the integration now lives in `custom_components/apsystems_openapi` in this repository. Your existing config entry keeps working: polling settings that older versions stored in the entry are still read, and you can change them from **Configure**. No re-setup is needed.

Two behaviour changes to be aware of when upgrading: sunrise/sunset offsets are now actually honoured, and batch power collection is now off by default because no entity displays it. See the [changelog](CHANGELOG.md).

## Setup

Go to **Settings → Devices & services → Add integration** and search for **APsystems OpenAPI**.

| Field | Description |
| --- | --- |
| **App ID** | From APsystems after your API access is approved |
| **App Secret** | From APsystems |
| **System ID (SID)** | Your system ID as shown in EMA |
| **API base URL** | Defaults to `https://api.apsystemsema.com:9282`; change only if APsystems directs you to a different region |
| **Collect inverter telemetry** | Creates the per-inverter sensors and buttons. 1 API call per inverter per day. On by default |
| **Collect daily batch power data** | Fetches the day's power series once per ECU at 23:00. 1 API call per ECU per day. Not shown by any entity (diagnostics download only). Off by default |
| **Automatically optimise polling interval** | Size the interval to stay under the monthly quota (recommended) |
| **Polling interval** | Fixed interval in seconds (1800–7200); ignored when auto-optimise is on |
| **Sunrise offset** | Minutes after sunrise before polling starts (0–120, default 30) |
| **Sunset offset** | Minutes after sunset before polling stops (0–120, default 30) |

The credentials are validated with one API call when you submit the form.

All settings except the credentials can be changed later via **Configure** on the integration, which also shows the estimated monthly API usage for the saved settings. Saving reloads the integration.

<!--
## Screenshots

Uncomment this section after adding the images to docs/images/ (see docs/images/README.md).

| Setup | Options with call estimate |
| --- | --- |
| ![Setup dialog](docs/images/setup.png) | ![Options dialog](docs/images/options.png) |

| System device | Inverter device |
| --- | --- |
| ![System device](docs/images/system-device.png) | ![Inverter device](docs/images/inverter-device.png) |

![Energy dashboard](docs/images/energy-dashboard.png)
-->

## Entities

### System device (`APsystems <SID>`)

| Entity | Description |
| --- | --- |
| **Total Energy** | Lifetime production in kWh (`total_increasing`, for the Energy dashboard). Extra attributes include today/month/year kWh and the hourly series. Keeps its last known value overnight and across restarts. |
| **Today Energy** | Production today in kWh, computed from the hourly series (falling back to the daily summary). Resets at local midnight. |
| **Scan Inverters** (button) | Re-queries the inverter list. New inverters get sensors immediately; missing ones are cleaned up. Only present while inverter telemetry is enabled. |
| **Refresh Inverter Data** (button) | Fetches inverter telemetry now instead of waiting for the daily fetch. Uses one API call per inverter. Only present while inverter telemetry is enabled. |

### Inverter devices (`Inverter <UID>`, one per micro-inverter)

Only present while **Collect inverter telemetry** is enabled.

| Entity | Unit | API field |
| --- | --- | --- |
| **Power** (AC output) | W | `ac_p` (falls back to `ac_p1`) |
| **DC Power Ch1 / Ch2** | W | `dc_p1`, `dc_p2` |
| **DC Voltage Ch1 / Ch2** | V | `dc_v1`, `dc_v2` |
| **DC Current Ch1 / Ch2** | A | `dc_i1`, `dc_i2` |
| **AC Voltage** | V | `ac_v1` |
| **Frequency** | Hz | `ac_f` |
| **Temperature** | °C | `ac_t` |

The **Power** sensor also exposes the day's AC/DC series as attributes (`hourly_ac_power`, `hourly_dc_p1`, `hourly_dc_p2`, `hourly_times`) and peak values.

> **Note on inverter values:** to save API calls, inverter telemetry is fetched **once per day, shortly after 12:30 local time**, and shows the latest reading from that fetch. It is not live. Use **Refresh Inverter Data** (or an automation that presses it) for a fresher snapshot, bearing in mind the call budget.

> **Turning telemetry off** removes the inverter devices, their sensors and the two buttons. Turning it back on recreates them. Long-term statistics for the system-level Energy sensors are not affected.

## How polling works

| Data | When it is fetched | API calls |
| --- | --- | --- |
| Hourly system energy | Every polling interval during solar hours | 1 per poll |
| Daily summary (lifetime / month / year) | On first run, then once per day on the last cycle before the end of solar hours | 1 per day |
| Inverter list | On startup/reload and when you press **Scan Inverters**; skipped entirely if both optional features are off | 1 per startup |
| Inverter telemetry *(option)* | Once per day after 12:30 local, or when you press the button | 1 per inverter per day |
| Batch power telemetry *(option)* | Once per day at 23:00 local | 1 per ECU per day |

"Solar hours" run from *sunrise + sunrise offset* to *sunset + sunset offset*, using the location configured in Home Assistant. Outside that window no API calls are made and entities keep their last values. A refresh is also triggered at sunrise (plus offset) and at midnight, so the daily sensor rolls over cleanly.

## API call budget

The two optional features are the only things that scale with the size of your system, so they are the ones you can switch off. The estimates below use the integration's own formula for the **busiest month** (longest days), a site with 6 inverters on 1 ECU, and 30-minute sunrise/sunset offsets. They exclude startup and reload calls, which are covered by the safety margin.

**At latitude 43°N (southern Europe, northern US):**

| Features enabled | 30 min | 60 min | 120 min | Auto-chosen interval |
| --- | --- | --- | --- | --- |
| Hourly + daily summary only | 974 | 518 | 290 | 35 min |
| + inverter telemetry (default) | 1160 | 704 | 476 | 50 min |
| + inverter telemetry + batch power | 1191 | 735 | 507 | 50 min |

**At latitude 52°N (UK, northern Europe):**

| Features enabled | 30 min | 60 min | 120 min | Auto-chosen interval |
| --- | --- | --- | --- | --- |
| Hourly + daily summary only | 1054 | 558 | 310 | 40 min |
| + inverter telemetry (default) | 1240 | 744 | 496 | 50 min |
| + inverter telemetry + batch power | 1271 | 775 | 527 | 55 min |

Figures above 1000 would exceed the quota that the original project documents. With **Automatically optimise polling interval** enabled, the integration picks the smallest interval (between 30 minutes and 2 hours) that keeps the busiest month at or below 85% of the quota, given your latitude and the features you have enabled. The chosen interval and estimate are written to the Home Assistant log at startup. The inverter and ECU counts grow the daily fixed cost, so larger systems benefit most from switching off features they don't use.

If the API reports that a limit was exceeded (response codes 2005, 7001, 7002 or 7003), the integration:

- logs an error,
- keeps serving the last known data so entities don't go unavailable,
- raises a **Repairs** issue, which clears itself after the next successful call.

## Troubleshooting

**Setup fails with "Authentication failed"**
Check the App ID and App Secret. If the secret is changed by APsystems later, Home Assistant will prompt you to re-authenticate.

**"This System ID was not accepted for your account"**
The SID doesn't belong to the API account, or has a typo. Copy it from EMA.

**Entities show old values at night**
Expected. No calls are made outside solar hours, and the last values are kept.

**Inverter sensors are empty or `unknown`**
Inverter telemetry is first fetched after 12:30 local time. Press **Refresh Inverter Data** to fetch it earlier. Some inverter models don't report every field (for example single-channel inverters have no channel 2 values).

**There are no inverter devices or buttons**
Check that **Collect inverter telemetry** is enabled under **Configure**.

**New inverters don't appear**
Press **Scan Inverters**.

**Shared (sub-user) systems**
The manual says shared sub-user systems use "virtual" ECU numbers of the form `203000001234-002405253708` for ECU-level queries. This integration only uses system-level and inverter-level calls, but if inverter calls fail on a shared system, please open an issue with a diagnostics download.

**Debug logging**

```yaml
logger:
  default: warning
  logs:
    custom_components.apsystems_openapi: debug
```

You can also download diagnostics from the integration's page. The App ID, App Secret and System ID are redacted automatically.

## For developers

### API client

`custom_components/apsystems_openapi/api.py` is a self-contained async client (`APsystemsClient`) with no Home Assistant imports. It implements every endpoint in the OpenAPI User Manual v1.8:

| Area | Methods |
| --- | --- |
| System details | `get_system_details`, `get_inverters`, `get_meters` |
| System-level | `get_system_summary`, `get_system_energy` |
| ECU-level | `get_ecu_summary`, `get_ecu_energy` |
| Meter-level | `get_meter_summary`, `get_meter_period` |
| Inverter-level | `get_inverter_summary`, `get_inverter_energy`, `get_inverter_batch_energy` |
| Storage-level | `get_storage_latest`, `get_storage_summary`, `get_storage_period` |

Only the system-level and inverter-level calls are used by the integration today. The rest are available if you want to extend it with meter or storage sensors.

**Authentication.** Every request carries these headers: `X-CA-AppId`, `X-CA-Timestamp` (milliseconds), `X-CA-Nonce` (32-character UUID), `X-CA-Signature-Method` (`HmacSHA256` or `HmacSHA1`) and `X-CA-Signature`. The signature is the Base64 of the HMAC of:

```
timestamp/nonce/appId/RequestPath/HTTPMethod/signatureMethod
```

keyed with the App Secret. `RequestPath` is **only the last segment** of the URL path (the SID, ECU ID or inverter UID), and query parameters are not signed.

**Errors.** Responses return HTTP 200 with a `code` field. The client raises `APsystemsRateLimitError` (2005, 7001–7003, HTTP 429), `APsystemsAuthError` (other 2xxx and 3xxx codes, HTTP 401/403) or `APsystemsAPIError` (everything else, including 1001 "No data"). Parameter rules from the manual are enforced locally, for example `minutely` is not valid for system-level energy and `yearly` takes no `date_range`.

### Project layout

| Path | Purpose |
| --- | --- |
| `custom_components/apsystems_openapi/api.py` | Standalone OpenAPI client |
| `.../coordinator.py` | `DataUpdateCoordinator`: solar window, caching, daily jobs, budget estimate, rate-limit handling |
| `.../__init__.py` | Setup, scheduled jobs (sunrise, 23:00, midnight), stale-device cleanup |
| `.../config_flow.py` | Setup, re-authentication and options flows |
| `.../sensor.py`, `.../button.py` | Entities |
| `.../const.py` | Constants and the API budget calculations |
| `.../diagnostics.py` | Redacted diagnostics download |
| `.../translations/en.json` | UI strings, entity names and Repairs text |
| `hacs.json`, `.github/workflows/validate.yml` | HACS metadata and CI validation (HACS action + hassfest) |

### Releasing

HACS offers the latest GitHub release. Tag a release (for example `v2.0.0`) whose version matches `version` in `manifest.json` and add the changelog entry as the release notes.

## License

Licensed under the **GNU General Public License v3.0 or later**, the same license as the upstream project. See [LICENSE](LICENSE).

## Credits

- Original integration: <https://blackhole.nmrc.org/solar/apsystems-openapi>
- API documentation: *APsystems OpenAPI User Manual (End User)*, published by APsystems and © APsystems.

This is an unofficial project and is not affiliated with or endorsed by APsystems.
