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
| `number` (controller) | Manual power, max charge/discharge power, start thresholds, buffers |
| `sensor` (controller) | Controller status, with the reasoning as attributes |
| `sensor` (energy) | Energy charged and discharged in kWh; on a meter, imported and exported |
| `sensor` (P1 meter) | Total power, per-phase apparent power, meter type, protocol type |

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

### v0.5.4

- **The settling period no longer blocks winding down.** Observed on hardware: a 2200 W load
  switched on, the controller correctly set a matching discharge limit, the load then
  disappeared, and the battery pushed 2200 W into the grid for twenty seconds because the
  controller was holding and did nothing at all. The hold is now abandoned as soon as grid
  power crosses zero against the running direction. A hold protects against acting on a stale
  reading; it should never protect against reacting to the load disappearing.

### v0.5.3

- **Fixed a nonsensical status message.** The direction hold decremented its counter before
  reporting it, so the final waiting cycle announced "0 cycles left" while still skipping that
  cycle. It now reports before counting down, and gets the singular right.
- **The hold is now measured in seconds, not cycles.** Two cycles was inherited from a
  reference implementation polling every 5 seconds. At a 10 second interval that silently
  became 20 seconds of doing nothing after every direction change. The settling time is 10
  seconds, converted to whole polls at whatever interval is configured.

### v0.5.2

- **Removed the `passive` mode.** With `standby` no longer writing anything, the two were
  nearly identical, and the difference favoured `standby`: switching from a smart mode into
  `passive` left the controller's own limit running indefinitely, with nothing left to adjust
  it. `standby` releases that limit once and is then equally hands-off.
- A stored operation mode that no longer exists falls back to the default rather than leaving
  the select showing a value it cannot offer.

### v0.5.1

- **Fixed: smart modes deadlocked after any restart.** Whether a step counted as a direction
  change was decided from the controller's internal memory. That memory is empty after a
  Home Assistant restart, an integration reload or a mode switch, while the device carries on
  discharging. Every cycle then looked like a fresh start and waited for an idle state that
  could not arrive, because the device's own draw exceeded the deadband precisely while
  executing the limit it had been given. Direction is now derived from which limit is
  non-zero on the device.
- On adopting a running direction, the controller also claims ownership of that limit, so
  switching to `standby` still releases it.
- `smartMode` is now set to 1 at the top of every smart cycle rather than only inside the
  write path. The device reverts it to 0 across a reboot, and the deadlock above meant the
  write path was never reached, so limit writes were landing in flash.

### v0.5.0

- **New `passive` mode, and it is now the default.** Reads the device and issues no writes at
  all. It is evaluated before every other branch, including SOC protection, so nothing can
  break the read-only guarantee.
- Rationale: no previous mode left the device alone. `standby` wrote zeros to both limits and
  released `smartMode` to flash, and `manual` at 0 W wrote zeros too. A zero is a command. On
  a device running its own energy manager that produced a sustained fight — the manager
  setting a discharge limit, this integration zeroing it a poll later, repeatedly — visible as
  a square wave in grid power and as `smartMode` flipping between RAM and flash.
- `standby` keeps its meaning: an active stop that overrides whatever else is driving.
- Controller status gained a `writes_enabled` attribute.

### v0.4.2

- **Fixed: stale readings outside the smart modes.** Meter and battery power were refreshed
  only on the smart path, so `quick`, `manual` and `standby` kept showing whatever the loop
  last saw. A stale negative battery power reads as "charging" while the device is discharging,
  which is worse than showing nothing. Both are now read every cycle, in every mode.
- **The power ceiling is now the lower of the setting and the device.** With a max discharge
  setting of 3600 W against an `inverseMaxPower` of 2400 W, the reported target was a number
  the hardware could never reach. Charge is capped by `chargeMaxLimit`, discharge by
  `inverseMaxPower`.

### v0.4.1

- **Fixed: discharging never started.** The idle test compared measured pack power against a
  30 W band, but the inverter's own standby draw is around 41 W, so the battery never read as
  idle and no direction could begin. Idle is now decided primarily by whether the controller
  is commanding anything: both limits at zero means a direction change is safe regardless of
  standby draw. The measured-power band is kept as a secondary test and widened to 60 W.
- Charging appeared to work only because SOC protection bypasses the idle test entirely, so
  the visible behaviour was protection charging at maximum, not the control loop.
- The opposite limit is now cleared before `acMode` switches, so the device is never briefly
  holding a limit belonging to the direction it just left.
- Controller status gained `idle`, `commanded_limit` and `last_direction` attributes, which is
  what made this diagnosable from a single report.

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
