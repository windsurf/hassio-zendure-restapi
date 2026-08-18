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

## Brand images

The integration ships its own icon and logo in `custom_components/zendure_restapi/brand/`.
Home Assistant 2026.3 and later picks these up directly, taking priority over the brands CDN;
older versions ignore the folder and fall back to the default placeholder. No submission to the
`home-assistant/brands` repository is needed.

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
| `standby` | Hands off. Reads and reports, writes nothing |
| `manual` | Follows the `Manual power` number; its sign picks charge or discharge |
| `smart_matching` | Tracks the grid meter towards zero exchange, both directions |
| `smart_charge_only` | Absorbs surplus only; never discharges |
| `smart_discharge_only` | Covers demand only; never charges |
| `quick_charge` | Charges at the configured maximum, ignoring the meter |
| `quick_discharge` | Discharges at the configured maximum, ignoring the meter |

The default is `standby`, which reads the device and writes nothing.

### Why standby writes nothing

A zero is still a command. Earlier versions had `standby` write zeros to both limits, and on a
device running its own energy manager — Zendure's HEMS in self-consumption mode, for instance
— that produces a fight: the manager sets a discharge limit to cover the house, the
integration overwrites it with zero on the next poll, the manager sets it back. Measured on
hardware as a square wave in grid power at the polling interval, with `smartMode` flipping
between RAM and flash along with it.

`standby` now issues no writes at all. It is checked before SOC protection, because a
protection that still writes would make "hands off" a lie, and the device guards its own floor
regardless.

The single exception is on entering `standby`: a limit is cleared once, and only if this
controller set it. Leaving it behind would not be restraint, it would be abandoning a command
mid-flight. A limit set by the device's own manager is never touched.

The controller status carries a `writes_enabled` attribute so this is visible at a glance.

### How the controller runs

Once per coordinator poll, not on its own timer. That matters: a separate timer would sooner
or later apply a correction based on readings from the previous cycle, correcting twice for
one deviation. That is how charge/discharge oscillation gets built.

The polling interval is therefore also the control interval.

### The smart control loop

```
charging:    target = clamp( (-grid - battery + charge_buffer)    * factor , 0 , max_power )
discharging: target = clamp( ( grid + battery - discharge_buffer) * factor , 0 , max_power )
```

Both buffers mean the same thing: how many watts of grid **import** to aim for. The default is
5 W on each side, so the loop settles just on the consuming side of zero rather than exactly on
it. That is deliberate — the two ways of being wrong do not cost the same. Overshooting into
import wastes the difference between the import tariff and the value of the stored energy;
undershooting into export gives away energy worth the import tariff for the feed-in rate,
roughly a third of it. Erring towards a little consumption is about three times cheaper.

Keep each start threshold further from zero than its buffer, or the direction never starts.

### Minimum power

`Min charge power` and `Min discharge power` raise the target above what the meter asks for,
in every smart mode. Both default to 0, which disables the floor.

Note what that means for `smart_matching`: with a discharge floor of 200 W and a 90 W house,
the battery supplies 200 W and the remaining 110 W goes to the grid. Zero-on-the-meter
therefore only holds while both floors are at 0.

The one-directional modes exist because a price layer has already decided that this is a moment
to charge or to discharge. The floor decides how hard. With a 400 W charge floor and 100 W of
surplus the device charges 400 W: 100 from the surplus, 300 bought from the grid. That is
deliberate — the energy is cheap now and worth more later.

The two directions are not symmetric in value. Overcharging is compared against the future
import tariff, which is the full retail price. Overdischarging exports at the feed-in rate,
roughly a third of it, so it only pays at an unusually wide spread. Setting the charge floor is
usually worthwhile; setting the discharge floor rarely is.

Three details do the real work:

**The first step is undershot** (factor 0.75 rather than 1.00). The device's actual response
is not yet known, so committing the full correction invites overshoot. Once the direction
holds, the controller balances at 1.00.

**Whether a step is a direction change is read from the device, not from memory.** The
controller's own record of the last direction is empty after a restart, a reload or a mode
switch, while the device carries on with the limit it was last given. Treating that as "no
direction" makes the next cycle look like a fresh start, which then waits for an idle state
the device cannot reach while it is still executing that very limit. The direction is
therefore derived from which limit is currently non-zero.

**A new direction only starts from idle**, and only after a settling period following a
direction change. That period is ten seconds, converted to whole polls at the current
interval — what matters is how long the device has had to respond, which does not change when
the polling interval does.

The settling period is abandoned the moment grid power crosses zero against the running
direction. It exists so a reading that still contains the old direction is not mistaken for a
fresh deviation; it is not a reason to keep pushing power the wrong way across the meter while
the load that justified it has gone. Without that, a reading that still contains the old direction gets treated
as a fresh deviation.

"Idle" is decided primarily by whether the controller is commanding anything, not by measured
power. An inverter draws its own standby power continuously, so the pack never reads zero: a
3000 Mix AC+ idles at roughly 41 W of discharge. Testing measured power alone against a small
band reads that as "still running" forever, and no direction can ever start. When both limits
are at zero the controller is commanding nothing, so a direction change is safe whatever the
pack is doing on its own.

**Start thresholds gate starting, not continuing.** A direction that is already running keeps
being balanced whatever the grid does, so it winds down to zero when the load disappears. If
the thresholds also gated continuation, a load that vanished would leave the battery
discharging into the grid while the controller waited for an idle state that nothing could
produce.

### Smart modes need a meter

The grid reading comes from a Zendure meter configured as its own entry. The controller links
to it automatically when exactly one is present. If the meter stops reporting, both limits go
to zero: a closed loop without feedback is worse than no loop.

### SOC protection

Below the lower SOC bound the battery is charged back regardless of strategy, except in
`manual` and `quick_discharge` where the operator's intent is explicit. Switchable off.

### Flash wear

The controller sets `smartMode` to 1 before writing limits, so frequent adjustments stay in
RAM. Only on entering standby does it drop back to flash. At a 10-second interval that is
tens of thousands of limit writes a year, which is exactly why they must not reach flash.

---

## Entities

| Platform | Entities |
|---|---|
| `sensor` | State of charge, Battery state, Battery charge power, Battery discharge power, Home output power, Grid input power, PV input power, PV string 1-6, Backup output power, Backup output power 2, Remaining discharge time, DC bus voltage, Enclosure temperature, Signal strength, AC state, DC state, PV state, SOC limit status, SOC calibration status, AC coupling status, Fault level, Battery module count, Charge power limit, Fan speed step, Fan level |
| `sensor` (per battery) | State of charge, Power, State, Temperature, Voltage, Current, Max cell voltage, Min cell voltage, Serial number, Firmware version, Pack type |
| `sensor` (energy) | Energy charged, Energy discharged, PV energy; on a meter entry Energy imported and Energy exported |
| `sensor` (efficiency) | Charge efficiency, Discharge efficiency |
| `sensor` (controller) | Controller status, carrying the reasoning as attributes |
| `sensor` (meter entry) | Grid power total, Phase A/B/C apparent power, Meter type, Protocol type |
| `sensor` (diagnostic) | IoT connection state, OTA state, LCN state, Bind state, Factory mode state, Voltage wake-up, Legacy mode, Phase switch, Grid HD status, Off-grid state, Powerhub status, Slave address, Write response, Device timestamp, Report timestamp, Message id, Timezone, Timezone offset, HV battery voltage (raw), Activation voltage |
| `binary_sensor` | Grid connected, Error, Data ready, Pass-through, Reverse flow, Heating, Fan, Lamp, Dry contact, PV-AC coupling, SOC compensation, HV battery control |
| `number` (device) | Charge limit (AC), Discharge limit, Charge power limit, Inverter power limit, Upper SOC limit, Lower SOC limit, Calibration interval |
| `number` (controller) | Manual power, Max charge power, Max discharge power, Min charge power, Min discharge power, Start discharging above, Start charging below, Charge buffer, Discharge buffer |
| `select` (device) | Converter mode, Backup mode, PV export, Grid standard, Fan speed mode |
| `select` (controller) | Operation mode |
| `switch` (device) | Skip flash write, Fan forced on |
| `switch` (controller) | SOC protection |

---

## The Energy dashboard

The device reports instantaneous power only. Every payload observed on hardware carries watts
and no cumulative counter, so the kilowatt-hour totals the Energy dashboard needs are
integrated here.

| Entity | Source | Feeds |
|---|---|---|
| Energy charged | `outputPackPower` | Home battery storage, energy in |
| Energy discharged | `packInputPower` | Home battery storage, energy out |
| PV energy | `solarInputPower` | Solar production, if PV is wired to the device |
| Energy imported / exported | meter `total_power` | Grid consumption and return |

Integration is trapezoidal: each interval uses the average of the previous and current
reading. With a ten second poll and a load that steps, a left-hand rectangle would attribute
the whole interval to the old value and a right-hand one to the new; the average splits the
difference and is exact for any linear ramp. Gaps longer than five minutes are skipped rather
than bridged, since interpolating across a restart would invent energy that was never
measured.

All sources are **AC-side** fields — the side the meter shares a connection point with, and
the side the Energy dashboard reasons about. DC pack readings are deliberately not used here:
they differ from the AC side by whatever the converter spends on itself, so DC-based counters
would quietly absorb the conversion loss instead of leaving it visible as the gap between the
grid figures and the battery figures.

### Conversion efficiency

Two diagnostic sensors surface that loss directly rather than letting it disappear between
counters. `Charge efficiency` is DC over AC — what reached the cells against what came off the
grid. `Discharge efficiency` is AC over DC — what reached the house against what left the
cells. Only packs in the matching state contribute, so a mixed bank does not dilute the ratio
with cells that are idle.

Expect roughly 94–95% at meaningful power. The figure drops sharply at low power, because the
converter's own consumption is close to constant: at 122 W of output it costs around 41 W, so
efficiency falls to about 75%. That is not an error in the measurement, it is the reason
trickle-discharging is expensive.

Totals persist across restarts and are never reset on reload — the Energy dashboard reads a
drop as a meter replacement and would double-count the difference.

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

**Power ceilings** come in two layers, and they are not duplicates.

| | Where it lives | What it bounds |
|---|---|---|
| `Charge power limit`, `Inverter power limit` | on the device, same values the Zendure app sets | everything, including whatever the app or an on-device manager does |
| `Max charge power`, `Max discharge power` | controller settings in the config entry | only what this integration writes |

The controller uses the lower of the two. Both device ceilings are writable, so the app is not
needed to change them.

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

### v0.9.8

- Added brand images in `brand/`: `icon.png` and `logo.png` at 256px, with `@2x` variants at
  512px. Home Assistant 2026.3 and later reads these from the integration directory; older
  versions ignore them.

### v0.9.7

- **Removed `Invert meter sign`.** The meter's sign convention was verified against a second
  meter on the same connection — 2088 W on the Zendure against 2059 W on the YouLess, both
  positive while importing — so the setting had nothing left to correct. It will be left
  orphaned in the entity registry after upgrading.

### v0.9.6

- README rebuilt. The changelog had silently stopped at v0.5.4 — a version that was never
  released — because each edit searched for the previous heading and failed quietly when it
  had moved. Everything from v0.6.0 onward was missing.
- The entity table is now generated from the source rather than maintained by hand, so it
  cannot drift again. It still listed pre-v0.3.0 names such as `AC charge limit` and
  `Target SOC`.
- `hacs.json` minimum Home Assistant version raised to 2024.10.0, matching the selectors and
  `RestoreEntity` usage actually relied on.

### v0.9.5

- Fixed: a mode could keep running a direction it forbids. Adopting whatever the device was
  already doing allows a running direction to be wound down, but it also kept that direction
  alive in a mode that ruled it out — observed as `smart_charge_only` discharging at 200 W. A
  forbidden direction is now cleared and released.
- The minimum power floors apply in every smart mode, including `smart_matching`.

### v0.9.4

- `Min charge power` and `Min discharge power` became floors instead of gates: they raise the
  target above what the meter asks for rather than declining to act below it.
- The half-floor hysteresis was removed along with the gate.

### v0.9.3

- Added `Min charge power` and `Min discharge power` as gates, reworked in v0.9.4.

### v0.9.2

- Energy counters integrate AC-side fields rather than the DC pack reading, matching the side
  the meter is on and the reference implementation.
- Added `PV energy`, `Charge efficiency` and `Discharge efficiency`. Efficiency runs 94-95% at
  meaningful power and falls to roughly 75% at 122 W of output, because the converter's own
  consumption is nearly constant.

### v0.9.1

- Fixed a permanent offset: battery power was read on the DC side, which is larger than the AC
  side by the converter's own consumption. The loop settled with the grid exporting by roughly
  that loss instead of importing by the buffer.

### v0.9.0

- Fixed the sign of `Charge buffer`. The two buffers carried opposite meanings under the same
  name: discharge aimed at import, charge aimed at export.
- Both now mean watts of grid import to aim for, range 0 to 200 W.

### v0.8.0

- `chargeMaxLimit` is writable as `Charge power limit`, matching `Inverter power limit`.

### v0.7.1

- Fixed: changing the polling interval reset every controller setting, because an options flow
  replaces the entire options dict rather than merging into it.

### v0.7.0

- Energy counters for the Energy dashboard, integrated trapezoidally from power since the
  device publishes no cumulative counters. Totals restore across a restart.
- Gaps beyond five minutes are skipped rather than interpolated across, and readings that are
  negative or above 20 kW are ignored rather than accumulated.

### v0.6.0

- Consolidation release, aligning the integration, release script, dashboard and session
  reports on one version number.

### v0.5.3

- Fixed a contradictory status message, and made the settling period after a direction change
  a number of seconds rather than a number of polling cycles.

### v0.5.2

- Removed the `passive` mode. With `standby` no longer writing anything the two were nearly
  identical, and the difference favoured `standby`.

### v0.5.1

- Fixed: smart modes deadlocked after any restart. Direction is now read from the device rather
  than from the controller's own memory, which is empty after a restart.
- `smartMode` is set at the top of every smart cycle; the device reverts it across a reboot.

### v0.5.0

- `standby` is genuinely passive: zero writes. It previously forced both limits to zero on
  every poll, which on a device running its own energy manager meant overruling that manager
  continuously, producing a square wave at the polling period.
- SOC protection no longer acts in `standby`.

### v0.4.2

- Fixed stale readings outside the smart modes, and made the power ceiling the lower of the
  configured setting and what the device publishes.

### v0.4.1

- Fixed: discharging never started. The idle test compared measured pack power against a 30 W
  band, but the inverter's own standby draw is around 41 W.

### v0.4.0

- Operation-mode controller inside the integration: seven modes executed once per poll, so the
  controller always acts on readings from that same cycle.
- Corrected `gridReverse`: 2 forbids PV export, it is not `auto`.

### v0.3.0

- Clearer entity names throughout. Existing installations keep their entity IDs.

### v0.2.1

- Corrected the `minSoc` range from the documented 0-50 to 0-100.

### v0.2.0

- P1 meter support: flat payloads with `deviceId` and no serial number.
- Corrected `socSet` and `minSoc` scaling to tenths of a percent, `totalVol` to centivolts, and
  the key `OldMode` to `oldMode`.

### v0.1.0

- Initial release. Local HTTP client, mDNS discovery, dynamic entity creation driven by the
  keys the device actually reports.

---

## Licence

MIT
