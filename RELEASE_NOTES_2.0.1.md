# Release 2.0.1 – notes for the maintainer

This file is not part of the integration. It holds text to paste into `CHANGELOG.md`
and `README.md`, and the steps to publish.

## 1. Files in this release

Copy over the repo, keeping paths:

```
custom_components/apsystems_openapi/__init__.py
custom_components/apsystems_openapi/const.py
custom_components/apsystems_openapi/config_flow.py
custom_components/apsystems_openapi/coordinator.py
custom_components/apsystems_openapi/sensor.py
custom_components/apsystems_openapi/translations/en.json
API_REFERENCE.md
```

Not included: `manifest.json`, which needs a one-line hand edit (section 2). `button.py`
and `api.py` are unchanged.

## 2. Version bump

In `custom_components/apsystems_openapi/manifest.json` change the version:

```
"version": "2.0.0"   ->   "version": "2.0.1"
```

## 2b. Why `__init__.py` changed

`_prune_stale_inverter_devices` removed every device whose id was not the system id or an
inverter UID. The storage device is identified by its ECU id, so it was deleted on the next
coordinator update. It now keeps storage ECUs while the storage option is on, and removes them
when it is off. `async_remove_config_entry_device` is also storage-aware, so the UI will not let
you delete a live battery device. Nothing else in the file changed.

## 3. CHANGELOG.md entry

```markdown
## [2.0.1]

### Added
- Optional **battery/storage support** for systems with an APsystems battery (for
  example ELT-12). New option "Collect battery/storage data" (off by default).
  - The storage ECU is discovered from the ECU list (ECU `type` 2).
  - New device "Storage <ECU id>" with 20 entities, all enabled by default. The same six
    values (battery charged, battery discharged, grid import, grid export, home consumed,
    solar produced) are available three ways:
    - **Total** (kWh, `total_increasing`, lifetime): for the Energy dashboard.
    - **Today** (kWh, resets at midnight): from the storage summary. Shown as unknown from
      midnight until the first poll of the new day, rather than showing yesterday's value.
    - **Current** (W): the last reading. A snapshot from the last poll, shown as unknown once
      older than two poll intervals so it does not look current overnight.
    Plus Battery State of Charge and **Last Reading**, a timestamp showing when the cloud's
    latest reading was taken (the API gives only HH:mm; the date is inferred).
  - Costs 2 API calls per storage ECU on every poll; the estimate and the automatic
    polling interval include this.
- `API_REFERENCE.md` rewritten as notes on the endpoints this integration uses, with
  behaviour observed on a real system.

### Fixed
- Stale-device clean-up no longer removes the storage device while the storage option is on.
- Storage endpoint paths in the API reference now use `/user/api/v2/...` (older
  copies said `/installer/api/v2/...`).
```

## 4. README.md additions

Add to the options table:

```markdown
| Collect battery/storage data | Battery state of charge and lifetime battery/grid energy from an APsystems storage ECU. 2 API calls per storage ECU per poll, so the automatic interval gets longer. Off by default. |
```

Add a section:

```markdown
### Battery, grid and home values

The Storage device offers each value as a lifetime total, a "today" total and the last
reading in watts (see the changelog). The totals are cumulative kWh and are meant for the
Energy dashboard; the "today" and power sensors are for cards. The power sensors and the
Last Reading timestamp show how old the reading is.

### Battery and grid in the Energy dashboard

With "Collect battery/storage data" on, the integration adds a "Storage <ECU id>"
device. In Settings > Dashboards > Energy use:

- Battery: Battery Discharged Energy (out of battery) and Battery Charged Energy (into battery)
- Grid: Grid Import Energy (consumption) and Grid Export Energy (return to grid)

The data comes from the APsystems cloud and updates once per poll (30 minutes to
2 hours), so these totals are not live power. The power sensors (Battery Charge Power,
Grid Import Power and so on) are the last reading from the same poll, so they can be
up to two hours old. For live grid or house power you need a local meter.
```

## 5. Before you tag

1. Replace the files, bump the version, restart Home Assistant (a reload is not enough).
2. Settings > Devices & services > APsystems OpenAPI > Configure: tick the storage option, save.
3. Check the log for `Discovered N inverter(s) and 1 storage ECU(s)` and `Storage data fetched (1 ECU(s))`.
4. Confirm a "Storage <ECU id>" device with a state of charge value and non-zero lifetime energies.
5. Confirm the Energy dashboard accepts the sensors.
6. Confirm the options dialog shows a larger call estimate and the log line reports it.
7. Check the Validate workflow (hassfest and HACS) passes, then tag `v2.0.1`.

## 6. Known gaps in this release

- Only run in an offline syntax check, not in a live Home Assistant. Watch the first day's logs.
- Pruning is skipped while storage is on but no storage ECU was found, so stale inverter
  devices on a system with the option on and no battery stay until you delete them in the UI.
- There is no "Refresh Battery Data" button yet (it needs a change to `button.py`).
- Storage data is only fetched during solar hours (plus once at startup), so the state
  of charge freezes at night.
