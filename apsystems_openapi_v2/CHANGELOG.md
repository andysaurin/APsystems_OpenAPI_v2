# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [2.0.0] - 2026-10-15

Rewrite of the integration internals against the APsystems OpenAPI User Manual v1.8 (July 2025) and current Home Assistant conventions. Entity unique IDs and device identifiers are unchanged, so existing history, dashboards and Energy dashboard setup carry over, and existing config entries keep working without re-setup.

### Added
- **Optional features with an API-call switch.** Two new options let you choose which features spend extra API calls:
  - *Collect inverter telemetry* (on by default, as before): 1 call per inverter per day. Turning it off removes the inverter devices, their sensors and the two inverter buttons, and skips inverter discovery entirely.
  - *Collect daily batch power data* (off by default): 1 call per ECU per day. See "Changed" below.
- **Call-budget estimate** in the options dialog and in the Home Assistant log at startup, based on your latitude, interval and enabled features. The automatic interval takes the optional features into account.
- **Re-authentication flow**: if APsystems rejects the App Secret, Home Assistant prompts for a new one instead of failing silently.
- **Repairs issue** when the API reports that a call limit was exceeded; it clears itself on the next successful call.
- **Diagnostics download** with credentials and the System ID redacted.
- **HACS support**: `hacs.json`, standard `custom_components/` layout, and a GitHub Actions workflow running HACS and hassfest validation.
- **Complete API client** (`api.py`) covering every endpoint in manual v1.8, including meter, storage and batch endpoints, with request-parameter validation and typed errors. It has no Home Assistant dependencies.
- Dynamic inverter discovery: inverters found by **Scan Inverters** get sensors immediately, without a reload.
- `GPL-3.0-or-later` license file and SPDX headers, matching the upstream project.

### Changed
- **Batch power is now off by default.** Earlier versions fetched it once a day at 23:00, but no entity displays it, so it only consumed API calls. It can be re-enabled in the options and still appears in the diagnostics download.
- **Sunrise and sunset offsets are now honoured.** Previously the options were collected but ignored (sunrise was fixed at 30 minutes and polling stopped at sunset).
- Options are saved as config entry *options*. Settings stored by earlier versions in the entry data are still read until you save the options once.
- The inverter AC power sensor reads `ac_p` as named in the manual, falling back to `ac_p1`, which earlier versions used.
- API errors are classified by the manual's response-code table: quota (2005, 7001-7003), authentication (other 2xxx/3xxx) and parameter/data errors. "No data" responses (1000, 1001) are no longer treated as failures.
- A failed first inverter discovery now makes Home Assistant retry setup, instead of silently continuing with zero inverters.
- The API-limit warning is now a Repairs issue instead of a persistent notification.
- The error key `auth` in the config flow is now `invalid_auth`.
- Pressing **Refresh Inverter Data** no longer triggers an additional hourly-energy API call.
- Entities use translation keys, entity descriptions, `runtime_data` and `DeviceInfo`; the coordinator is created with its config entry.
- Minimum Home Assistant version is 2025.1.

### Fixed
- Scheduled jobs (sunrise, 23:00, midnight) were never cancelled when the integration was unloaded or reloaded, so reloads accumulated duplicate jobs. They are now tied to the config entry.
- The sunrise trigger fired only once after startup. It now recurs daily.
- The "Scan Inverters" button found new inverters but never created sensors for them.
- The options flow wrote its changes into the entry's data instead of its options.
- The sunset trigger was a no-op (polling had already stopped by then) and has been removed.

### Removed
- The unused sunset-triggered refresh.

## [1.4.0] and earlier

See the git history. Version 1.x was forked from <https://blackhole.nmrc.org/solar/apsystems-openapi>.
