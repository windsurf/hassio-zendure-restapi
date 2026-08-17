# Zendure RestAPI

Home Assistant integration for local control of Zendure devices through the
[zenSDK](https://github.com/Zendure/zenSDK) HTTP API. Supports SolarFlow batteries and the
Zendure P1 meter.

No cloud account. No MQTT broker. No Bluetooth pairing. The device runs an HTTP server on
your LAN and this integration talks to it directly.

---

## Before you start

Under EN 18031 the local HTTP API is **disabled by default**. Enable it first:

> Zendure app → add **HEMS** → exit the app to apply.

Verify from a terminal before configuring the integration:

```bash
curl -X GET "http://<device-ip>/properties/report"
```

If that returns JSON, the integration will work. If it returns nothing, the local API is
still disabled and no amount of configuration will help.

---

## Installation

### HACS (recommended)

1. HACS → Integrations → three-dot menu → Custom repositories
2. Add `https://github.com/windsurf/hassio-zendure-restapi`, category **Integration**
3. Install **Zendure RestAPI**, then restart Home Assistant

### Manual

Copy `custom_components/zendure_restapi/` into your Home Assistant `config/custom_components/`
directory and restart.

---

## Configuration

Devices broadcast over mDNS as `_zendure._tcp`, so Home Assistant usually discovers them by
itself and offers them under Settings → Devices & Services. If discovery does not fire, add
the integration manually and enter the device IP address.

The polling interval is configurable from 1 to 60 seconds under the integration's
**Configure** button. The default is 10 seconds.

---

## Device support

Entity creation is driven by the properties the device actually reports, not by a hard-coded
model list. Every SolarFlow device shares one generic property set, so an unlisted or
brand-new model still gets full entity coverage. It is simply labelled
`SolarFlow (generic)` until its name is added to `registry.py`.

Model naming prefers the `product` field in the report payload, which is authoritative;
the mDNS service name is only a fallback.

Verified on hardware: **SolarFlow 3000 Mix AC+** (`solarFlow3000MixAC+`, firmware 3) and the
**P1 meter** (meterType 3, three-phase). Listed in the registry but not yet hardware-verified:
SolarFlow 800, 800 Plus, 800 Pro, 1600 AC+, 2400 AC, 2400 AC+, 2400 Pro, Smart Meter 3CT.
Other models are accepted and will work if they answer on `/properties/report`.

### The P1 meter

The meter reports a flat payload with a `deviceId` and **no serial number at all**. Identity
therefore falls back to `deviceId`, and no writable entities are created because writes
require a serial. It is read-only by nature. Add it as a second device with its own IP.

---

## Operation modes

A battery entry runs a controller that turns a strategy into device writes. The strategy is a
single `select` entity, `Operation mode`:

| Mode | Behaviour |
|---|---|
| `standby` | Both limits at zero; after the standby delay, storage drops back to flash |
| `manual` | Follows the `Manual power` number; its sign picks charge or discharge |
| `smart_matching` | Tracks the grid meter towards zero exchange, both directions |
| `smart_charge_only` | Absorbs surplus only; never discharges |
| `smart_discharge_only` | Covers demand only; never charges |
| `quick_charge` | Charges at the configured maximum, ignoring the meter |
| `quick_discharge` | Discharges at the configured maximum, ignoring the meter |

The default is `standby`, so nothing moves until a mode is chosen deliberately.

### How the controller runs

Once per coordinator poll, not on its own timer. That matters: a separate timer would sooner
or later apply a correction based on readings from the previous cycle, correcting twice for
one deviation. That is how charge/discharge oscillation gets built.

The polling interval is therefore also the control interval.

### The smart control loop

```
target = clamp( (grid_error - battery_power - buffer) * factor , 0 , max_power )
```

Three details do the real work:

**The first step is undershot** (factor 0.75 rather than 1.00). The device's actual response
is not yet known, so committing the full correction invites overshoot. Once the direction
holds, the controller balances at 1.00.

**A new direction only starts from idle**, and only after a two-cycle hold following a
direction change. Without that, a reading that still contains the old direction gets treated
as a fresh deviation.

**Start thresholds gate starting, not continuing.** A direction that is already running keeps
being balanced whatever the grid does, so it winds down to zero when the load disappears. If
the thresholds also gated continuation, a load that vanished would leave the battery
discharging into the grid while the controller waited for an idle state that nothing could
produce.

### Smart modes need a meter

The grid reading comes from a Zendure meter configured as its own entry. The controller links
to it automatically when exactly one is present. If the meter stops reporting, both limits go
to zero: a closed loop without feedback is worse than no loop.

If import and export come out inverted, flip `Invert meter sign`.

### SOC protection

Below the lower SOC bound the battery is charged back regardless of strategy, except in
`manual` and `quick_discharge` where the operator's intent is explicit. Switchable off.

### Flash wear

The controller sets `smartMode` to 1 before writing limits, so frequent adjustments stay in
RAM. Only on entering standby does it drop back to flash. At a 10-second interval that is
tens of thousands of limit writes a year, which is exactly why they must not reach flash.

---

## Entities

| Platform | Examples |
|---|---|
| `sensor` | Battery level, charge/discharge power, output to home, grid input, solar input and per-channel PV power, remaining discharge time, battery voltage, enclosure temperature, pack/DC/AC/PV state, SOC limit state, fault level |
| `sensor` (per pack) | Level, power, state, temperature, voltage, current, min/max cell voltage, serial, firmware |
| `binary_sensor` | Grid connected, error, pass-through, reverse flow, heating, fan, lamp, dry contact, data ready |
| `number` | AC charge limit, output limit, maximum inverter output, target SOC, minimum SOC, calibration interval |
| `select` | AC mode (charge/discharge), off-grid mode, reverse flow control, grid standard |
| `switch` | Skip flash write, forced fan, SOC protection, invert meter sign |
| `select` (controller) | Operation mode |
| `number` (controller) | Manual power, max charge/discharge power, start thresholds, buffers, standby delay |
| `sensor` (controller) | Controller status, with the reasoning as attributes |
| `sensor` (P1 meter) | Total power, per-phase apparent power, meter type, protocol type |

---

## Notes on correctness

The zenSDK document is wrong or incomplete in several places. Each correction below was
found by comparing it against a live SolarFlow 3000 Mix AC+ and is cross-checked, not guessed.

**Temperatures** are stored in tenths of a Kelvin. The integration applies
`(raw - 2731) / 10` before publishing degrees Celsius.

**Pack voltage** (`totalVol`) is in centivolts despite the document listing whole volts. A
raw 2688 is 26.88 V, not 2688 V.

**SOC setpoints** (`socSet`, `minSoc`) are in tenths of a percent, not whole percent. Live
values of 1000 and 100 mean 100.0% and 10.0%.

**The document's `minSoc` range (0-50) is also wrong.** A second sample reported 800, meaning
80.0%, so the range is 0-100. `socSet` keeps its documented 70-100 lower bound because
nothing observed contradicts it — but treat that bound as unverified, since the document has
already proven wrong about the neighbouring field.

**Power ceilings** are read from the device rather than hard-coded: `chargeMaxLimit` bounds
the charge limit and `inverseMaxPower` bounds the output limit. On the 3000 Mix AC+ both are
800 W.

**Pack current** (`batcur`) is a 16-bit two's complement value in deciampere. Read as an
unsigned integer it turns every discharge into roughly 6500 A. The integration converts to
signed, so the sign correctly distinguishes charging from discharging.

Three independent checks confirm the voltage and current scales, across two samples taken at
very different power levels:

| Check | Sample 1 | Sample 2 |
|---|---|---|
| `totalVol` x `batcur` vs reported pack power | 26.88 V x 22.8 A = 613 W vs 612 W | 26.80 V x 1.7 A = 45.6 W vs 46 W |
| Cell voltage x 8 vs `totalVol` | 3.36 V x 8 = 26.88 V | 3.35 V x 8 = 26.80 V |

The cell-count agreement also identifies the pack as 8S LFP.

**Smart mode** controls whether written parameters are persisted to flash. With
`smartMode = 1` they are not, and the device restores its previous flash values after a
reboot. Keep it enabled whenever an automation writes charge or discharge limits on a short
cycle; otherwise every write hits the flash.

**One property per write.** The local API caps its receive buffer at 512 bytes. The
integration issues one property per POST and verifies the encoded body length beforehand,
because a body that truncates silently is effectively undebuggable.

---

## Reporting an unsupported property

The integration logs every reported key it has no entity for, once each, at `INFO` level.
For a complete picture, download diagnostics from the device page: the file contains the
untouched device report plus the full list of unrecognised keys. Attach it to a GitHub issue
and the properties can be mapped in the next release.

---

## Changelog

### v0.4.0

- **Operation-mode controller inside the integration.** Seven modes (`standby`, `manual`,
  `smart_matching`, `smart_charge_only`, `smart_discharge_only`, `quick_charge`,
  `quick_discharge`) exposed as one select, executed once per poll.
- Closed-loop tracking of a linked Zendure meter, with undershot first step, idle-gated
  direction changes, and a two-cycle hold.
- Start thresholds gate starting a direction, not continuing one — a running direction always
  winds down, so a disappearing load cannot strand the battery discharging into the grid.
- Everything stops if the meter goes quiet.
- SOC protection charges back to the lower bound, overridden only by explicit operator intent.
- `smartMode` is forced to RAM before limit writes and released to flash on standby.
- New controller entities: operation mode select, eight setting numbers, two setting switches,
  and a status sensor carrying the reasoning as attributes.
- Settings persist in the config entry options, so a restart resumes the same strategy.
- **Corrected `gridReverse`.** The community zenSDK automation writes 2 to forbid PV export and
  1 to allow it; the previous map labelled 2 as `auto`, which read as permissive on a device
  that was actually blocking export. Now `off` / `allowed` / `forbidden`, with 0 unconfirmed.
- Renamed `Operating mode` (the raw `acMode` property) to `Converter mode`, freeing the name
  for the strategy select.

### v0.3.0

- **Clearer entity names throughout.** Several v0.2.x names described the wire protocol rather
  than the function: `Output limit` is a discharge limit, `Target SOC` and `Minimum SOC` are an
  upper and a lower bound on the same quantity, and `Pack` is a battery module. Renamed
  accordingly, including `Pack N` to `Battery N`.
- Notable changes: `Battery level` to `State of charge`, `Output limit` to `Discharge limit`,
  `AC charge limit` to `Charge limit (AC)`, `Target SOC` to `Upper SOC limit`, `Minimum SOC` to
  `Lower SOC limit`, `Maximum inverter output` to `Inverter power limit`, `AC mode` to
  `Operating mode`, `Off-grid mode` to `Backup mode`, `Reverse flow control` to
  `Grid feed-in control`, `Solar input power` to `PV input power`, `Solar power N` to
  `PV string N`, `Battery voltage` to `DC bus voltage`, `Pack state` to `Battery state`,
  `Smart mode (skip flash write)` to `Skip flash write`.
- **Existing installations keep their entity IDs.** Home Assistant fixes an entity ID at
  creation, so upgrading only changes the displayed names. Dashboards and automations that
  reference the old IDs keep working. A fresh install on v0.3.0 generates IDs from the new
  names.

### v0.2.1

- **Corrected `minSoc` range** from the documented 0-50 to 0-100. A second live sample
  reported 800 (80.0%), which the previous range rejected.
- Conversions re-verified against a second sample at a very different power level, plus a
  third independent check via cell voltage times cell count.
- `socLimit` enum independently validated: with SOC at 54% and `minSoc` at 80%, the device
  reported `lower_limit`, exactly as expected.

### v0.2.0

- **P1 meter support.** Flat payloads with `deviceId` and no serial number are now handled;
  identity falls back to `deviceId` instead of aborting setup. Total power and per-phase
  apparent power are exposed.
- **Corrected `socSet` and `minSoc` scaling** to tenths of a percent. Previously both sat
  outside their entity range and could not be set.
- **Corrected `totalVol`** to centivolts. Previously reported pack voltage as 2688 V.
- **Corrected key `OldMode` to `oldMode`**, matching what hardware actually sends.
- **Dynamic power ceilings** from `chargeMaxLimit` and `inverseMaxPower` instead of a fixed
  3600 W.
- **Undocumented `acCouplingState` bits** are surfaced by number rather than dropped; live
  hardware sets bit 15, which the document does not describe.
- Model naming now prefers the payload's `product` field over the mDNS token.
- Firmware version from `version` shown in the device registry.
- Added entities for 15 keys the document omits: `gridOffPower2`, `gridHdStatus`,
  `offGridState`, `hvBatVolt`, `writeRsp`, `Fanspeed`, `pvacSwitch`, `slaveAddr`,
  `powerhubStatus`, `socCompSwitch`, `hvBatCtrlSwitch`, `ts`, `tsZone`, `timestamp`,
  `messageId`.
- Full coverage of every key observed on live hardware: 63/63 battery properties, 11/11 pack
  keys, 8/8 meter keys.

### v0.1.0

- Initial release
- Local HTTP client for `/properties/report`, `/properties/write` and `/rpc`
- mDNS discovery through `_zendure._tcp`, with manual host entry as fallback
- Configurable polling interval, 1 to 60 seconds, default 10
- Dynamic entity creation driven by reported keys, including per-pack entities
- Sensor, binary sensor, number, select and switch platforms
- Kelvin, centivolt and signed deciampere conversions
- Diagnostics download with raw report and unknown-key list
- Write body size verified against the 512 byte receive limit

---

## Licence

MIT
