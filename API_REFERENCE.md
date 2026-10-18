# APsystems OpenAPI â developer notes

Unofficial notes on the parts of the APsystems OpenAPI that this integration uses,
plus behaviour observed on a real system. These are summaries in our own words, not a
copy of the manual. The manual belongs to APsystems; get it from them:

- End-user manual (PDF): <https://file.apsystemsema.com:8083/apsystems/resource/openapi/Apsystems_OpenAPI_User_Manual_End_User_EN.pdf>

Manual revisions seen so far run up to **V1.8 (2025-07-18)**. V1.7 added the storage
endpoints and V1.8 added support for shared sub-user systems.

> **Correction for older copies of this file.** Earlier copies of this reference
> listed the storage endpoints under `/installer/api/v2/...`. In the current manual
> they are under `/user/api/v2/...`, like every other endpoint, and that is what
> this integration calls. Treat any `/installer/` storage path as outdated.

## 1. Access

- Base URL: `https://api.apsystemsema.com:9282`
- Credentials: an **App ID** (32 characters) and an **App Secret** (12 characters),
  issued by APsystems after you apply for an OpenAPI account. Keep them private.
- Each request carries five headers:

  | Header | Value |
  | --- | --- |
  | `X-CA-AppId` | your App ID |
  | `X-CA-Timestamp` | request time (the manual gives it as a string timestamp) |
  | `X-CA-Nonce` | a fresh UUID, 32 characters, no dashes |
  | `X-CA-Signature-Method` | `HmacSHA256` or `HmacSHA1` |
  | `X-CA-Signature` | the signature described below |

- Signature: build the string
  `Timestamp/Nonce/AppId/RequestPath/HTTPMethod/SignatureMethod`, where
  `RequestPath` is **the last segment of the URL path** (for example the system id
  for `.../systems/details/{sid}`). Compute the HMAC of that string with the App
  Secret as key, using the chosen algorithm, and send it base64-encoded.
- Access to some categories of data depends on what APsystems authorised for your
  account (manual section 2.3). A missing right shows up as response code 2004.

## 2. Endpoints

All paths are under `/user/api/v2/systems`. `{sid}` is the system id, `{eid}` an ECU
id and `{uid}` an inverter id. All calls are `GET`.

| Area | Path | Used by this integration |
| --- | --- | --- |
| System details | `/details/{sid}` | `get_system_details` (credential check) |
| ECU and inverter list | `/inverters/{sid}` | `get_inverters` (inverter and storage discovery) |
| Meter list | `/meters/{sid}` | not used |
| System summary | `/summary/{sid}` | `get_system_summary` |
| System energy | `/energy/{sid}` | `get_system_energy` (`energy_level=hourly`) |
| ECU summary / energy | `/{sid}/devices/ecu/summary/{eid}`, `.../ecu/energy/{eid}` | not used |
| Meter summary / period | `/{sid}/devices/meter/summary/{eid}`, `.../meter/period/{eid}` | not used |
| Inverter summary | `/{sid}/devices/inverter/summary/{uid}` | not used |
| Inverter energy | `/{sid}/devices/inverter/energy/{uid}` | `get_inverter_energy` (`minutely`) |
| Inverter batch energy | `/{sid}/devices/inverter/batch/energy/{eid}` | `get_inverter_batch_energy` (`power`) |
| **Storage latest** | `/{sid}/devices/storage/latest/{eid}` | `get_storage_latest` |
| **Storage summary** | `/{sid}/devices/storage/summary/{eid}` | `get_storage_summary` |
| Storage period | `/{sid}/devices/storage/period/{eid}` | `get_storage_period` (not called by any entity) |

Period-style endpoints take `energy_level` (`minutely`, `hourly`, `daily`, `monthly`,
`yearly`) and an optional `date_range`: `yyyy-MM-dd` for minutely and hourly,
`yyyy-MM` for daily, `yyyy` for monthly, and nothing for yearly. A date in the
future is rejected.

## 3. Discovery

**System details** returns `sid`, `create_date`, `capacity` (kW), `timezone`, the
list of ECU ids in `ecu`, a status `light` (1 green, 2 yellow, 3 red, 4 grey), an
`authorization_code` for embedded EMA pages, and a system `type`:
`1` PV only, `2` storage only, `3` PV and storage.

**ECU / inverter list** returns one entry per ECU: `eid`, `timezone`, `inverter`
(a list of `{uid, type}`; real responses also carried a `subType`) and an ECU `type`:

| ECU `type` | Meaning |
| --- | --- |
| 0 | plain ECU |
| 1 | ECU with a meter activated |
| 2 | **ECU with storage activated** |

A storage ECU also carries `model` (for example `ELT-12`) and `capacity` in kWh. This
is how the integration finds the battery: every ECU with `type == 2` is a storage
ECU, and its `eid` is what the storage endpoints expect.

## 4. Storage data

### Latest status â `storage/latest/{eid}`

Fields, all strings: `mode`, `soc` (battery state of charge, **%**), `time`
(`HH:mm` of the reading) and the last readings for `discharge`, `charge`, `produced`,
`consumed`, `exported`, `imported`. The manual labels the last six as "power" without
a unit.

### Summary â `storage/summary/{eid}`

Four maps, `today`, `month`, `year` and `lifetime`, each holding the cumulative
energy for `discharge`, `charge`, `produced`, `consumed`, `exported` and `imported`.
These are cumulative values in **kWh**, which is what makes them suitable for the
Home Assistant Energy dashboard:

| Field | Energy dashboard use |
| --- | --- |
| `charge` | energy going into the battery |
| `discharge` | energy coming out of the battery |
| `imported` | grid consumption |
| `exported` | return to grid |
| `consumed` / `produced` | home consumption / solar production (HA derives these itself) |

### Period â `storage/period/{eid}`

For `hourly` to `yearly` levels the data holds a `time` list plus one list per
field above. For `minutely` it holds a `today` map and `time`, `power` and `energy`
maps.

## 5. Observed on a real system

Observed on a system with six micro-inverters on one ECU (type 1) and an ELT-12
battery ECU (type 2, 5.76 kWh), system type 3. Identifiers are omitted here.

- The ELT-12 appeared in the ECU list as `type: 2`, `model: "ELT-12"`,
  `capacity: "5.76"` and an **empty** `inverter` list. The inverter ECU had
  `model: null` and `capacity: null`.
- Storage summary values were cumulative kWh strings, with four decimals for
  `today` and three for the other periods.
- The energy balance held to within 0.1 % for a day:
  `produced + imported + discharge â consumed + exported + charge`
  (25.109 against 25.124 kWh in the sample).
- For a battery installed this year, `lifetime` equalled `year` exactly.
- In `storage/latest` the six reading fields (`discharge`, `charge`, `produced`,
  `consumed`, `exported`, `imported`) were small whole numbers whose balance worked
  out in the same way (696 against 672), so they look like **watts**. The manual does
  not state the unit, so treat that as inferred.
- `mode` was a small integer string. The manual does not say what the values mean.
- Both storage calls succeeded using the same `/user/` prefix and credentials as every
  other call, with no installer-specific setup on the integration side.
- The `latest` reading carried its own `time` of day. The cloud copy can be older than
  the real battery state, so check that time before trusting a "fresh" reading.

## 6. Response codes

HTTP status is 200 even for errors; the JSON `code` field carries the result.

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1000 | data exception (the integration treats this as "no data") |
| 1001 | no data |
| 2000 â 2005 | account problems: invalid, not authorised, expired, no permission (2004), **access limit exceeded (2005)** |
| 3000 â 3004 | token problems |
| 4000, 4001 | request parameter errors (also returned for an unknown system id) |
| 5000 | internal server error |
| 6000 | communication error |
| 7000 â 7003 | server restrictions: access limit (7001), too many requests (7002), system busy (7003) |

Codes 2005 and 7001 â 7003 are the rate-limit family that raises the integration's
Repairs issue.

## 7. Call budget

The original project documents a quota of 1000 calls per month; check the terms of
your own account. Each poll during solar hours costs one hourly system call, plus two
more per storage ECU when storage data is enabled. See `const.py` for the budget
maths and the integration's options dialog for the current estimate.

