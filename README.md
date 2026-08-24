# Zendure RestAPI – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)

> **Disclaimer:** This software is not affiliated with or endorsed by Zendure in any way. It is provided "as-is" without warranty or support, for the educational use of developers and enthusiasts. Use at your own risk.

Local monitoring and control of Zendure SolarFlow batteries and the Zendure P1 meter over the zenSDK HTTP API. No cloud account, no MQTT broker, no Bluetooth pairing — the device runs a plain JSON REST server on your LAN and this integration talks to it directly.

Beyond reading the device, the integration runs an **operation-mode controller**: seven strategies that turn a price decision made elsewhere into charge and discharge limits, tracking a linked meter towards zero grid exchange.

> **Actively tested with:** Zendure SolarFlow 3000 Mix AC+ (`solarFlow3000MixAC+`) and the Zendure P1 meter (three-phase, meterType 3)

![Zendure dashboard](images/dashboard.png)

*The example dashboard, showing `smart_matching` holding the grid at zero: the house draws
175 W, the battery supplies it, and the meter reads 0 W. A ready-made view is included — see
[Dashboard](#dashboard).*

---

## Before you start

Under EN 18031 the local HTTP API is **disabled by default**. Enable it first:

> Zendure app → add **HEMS** → exit the app to apply.

Verify from a terminal before configuring anything.

```bash
curl "http://<device-ip>/properties/report"
```

```powershell
Invoke-RestMethod "http://<device-ip>/properties/report" | ConvertTo-Json
```

If that returns JSON, the integration will work. If it times out or returns nothing, the local
API is still off and no amount of configuration will help.

---

## Installation via HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

1. HACS → Integrations → three-dot menu → Custom repositories
2. Add `https://github.com/windsurf/hassio-zendure-restapi`, category **Integration**
3. Install **Zendure RestAPI**, then restart Home Assistant

## Manual Installation

Copy `custom_components/zendure_restapi/` into your Home Assistant `config/custom_components/` directory and restart. A full restart is required — a reload leaves stale bytecode in `__pycache__`.

---

## Setup

Devices broadcast over mDNS as `_zendure._tcp`, so Home Assistant usually discovers them and offers them under **Settings → Devices & Services**. If discovery does not fire, add the integration manually and enter the device IP.

Add the battery and the meter as **separate entries**. The controller links to the meter automatically when exactly one is present.

The polling interval is configurable from 1 to 60 seconds under **Configure**. The default is 10 seconds, and it doubles as the control interval.

### Device support

Entity creation is driven by the properties the device actually reports, not by a hard-coded model list. Every SolarFlow device shares one generic property set, so an unlisted or brand-new model still gets full entity coverage; it is simply labelled `SolarFlow (generic)` until its name is added to `registry.py`.

Model naming prefers the `product` field in the report payload, which is authoritative. The mDNS service name is only a fallback.

### The P1 meter

The meter reports a flat payload with a `deviceId` and **no serial number at all**. Identity
therefore falls back to `deviceId`, and no writable entities are created because writes require
a serial. It is read-only by nature.

---

## What the P1 meter does and does not give you

The meter is the only feedback the control loop has, so it is worth being precise about what it
can tell you. What follows was measured on a three-phase meter (`meterType` 3, `protocolType`
51) against a second, independent P1 reader on the same connection point through a splitter —
so both saw the same electricity at the same instant. Roughly 1,300 paired samples at 0.36 s
and 1.1 s intervals, in two runs, one with the controller in `standby` so nothing was steering
the grid.

### What it gets right

**The reading is accurate.** Over 60 samples at rest, the two meters averaged −22.6 W and
−18.6 W with a spread of 16.5 W on both. The 4 W gap is smaller than the sample-to-sample
noise, so there is no measurable systematic error.

**The phase readings are consistent with the total.** `a_aprt_power + b_aprt_power +
c_aprt_power` matches `total_power` to within 0.2 W on average. Single snapshots disagree by
tens of watts because the fields are not sampled at the same instant, but there is no offset.

**The sign convention holds.** Positive is import, negative is export, confirmed against the
reference meter across the full range from −1,700 W to +2,250 W.

**Fast to query.** Round trips average 23 ms with a worst case of 31 ms and no failures over 30
consecutive requests. The network is never the bottleneck.

### The refresh rate

**`total_power` updates roughly once per second**, in step with the meter's own P1 telegram.
Sampled four times a second, each value repeats about three times before changing.

Polling faster than 1 s therefore returns the same number again. Below that there is nothing to
gain, and the same holds for the reference meter — this is a property of the telegram, not of
the device.

### The delay

**The meter lags a second reader by 0.4 to 1.1 seconds.** This is the finding that matters most
for control, and it is consistent rather than occasional.

| Load step | Delay behind the reference |
|---|---|
| 2,234 W, rise | 1,111 ms |
| 1,414 W, rise | 740 ms |
| 1,900 W, rise | 389 ms |
| 2,250 W, rise | 0 ms |

That last one is not a contradiction: the step happened to coincide with a refresh. A single
observation would have concluded the meters were equally fast, which is exactly what the first
run did conclude before the other three were measured.

Cross-correlating every sample rather than only the steps confirms it independently. The best
agreement between the meter and the reference is at a shift of one to two samples — 0.4 to
0.7 s — and it holds in both runs, so it is not an artefact of the controller acting on the
grid.

Visible in the raw samples, where the meter holds a stale value for three cycles after a load
appears:

```
T+0.000   reference 2234 W    meter  -69 W
T+0.376   reference 2234 W    meter  -69 W
T+0.742   reference 2234 W    meter  -69 W
T+1.111   reference 2179 W    meter 2179 W
```

**What this means in practice.** A control loop reading this meter is acting on a picture of the
grid that is up to a second old. At a 10 s control interval that is a fraction of the cycle and
hardly matters; at a 1 s interval it is the dominant source of error. It also sets a floor on
how tightly any loop can hold the grid at zero, no matter how fast it writes.

### What it does not have

**No cumulative counters.** The meter reports instantaneous power only. Every sample taken
between two polls is gone — poll at 10 s while the meter refreshes at 1 s and nine readings are
discarded. A meter that counts loses nothing at any polling rate, because the difference between
two readings still covers the whole interval.

**No import and export registers.** The smart meter's own `1.8.1`, `1.8.2`, `2.8.1` and `2.8.2`
are in every P1 telegram, and the reference reader exposes all four. This meter exposes none of
them, not even their net sum.

That matters more than it used to. Where import and export are settled separately — as they
will be under some tariff regimes — the net figure hides what you are billed on. In one
two-minute window the reference registers showed 5 Wh imported and 14 Wh exported: a net of
−9 Wh concealing 19 Wh of actual exchange.

**No second endpoint.** `/properties/report` is all there is. `/properties/list`,
`/device/info`, `/meter/report` and `/properties/get` all return 404, and a port scan finds only
80 open — no MQTT, no Modbus. There is no richer channel hiding behind another protocol.

### Suggestions for the device firmware

Each of these is something the hardware already has and the API does not expose. They are listed
in the order they would help a control loop.

**Expose a timestamp for the reading itself.** The payload carries `timestamp` and `messageId`,
but nothing that says when the underlying measurement was taken. A consumer cannot tell a fresh
value from one repeated for the third time, and so cannot correct for the delay or detect a
stalled telegram feed.

**Reduce the delay, or document it.** Up to a second of lag on a meter sold for closed-loop
control is significant. If it is inherent to the design, saying so in the documentation would
let integrators compensate rather than discover it by measurement.

**Expose the four energy registers.** `1.8.1`, `1.8.2`, `2.8.1`, `2.8.2` arrive in every
telegram. Publishing them costs nothing and gives consumers an exact energy balance over any
interval, independent of polling rate, plus the import/export split that billing increasingly
requires.

**Report a serial number.** The device identifies itself only by `deviceId`. Home Assistant and
comparable systems key device identity on a serial; without one, an integration must invent a
fallback, and no writable entities can be offered safely.

**Document the units and conventions.** `a_aprt_power` is labelled apparent power but tracks
real power in practice. `protocolType` 51 and `meterType` 3 are undocumented. A short table of
fields, units and sign conventions would remove a class of integration errors entirely.

---

## Dashboard

`dashboard/zendure.yaml` is the view in the screenshot above: twelve sections covering
operation, controller settings, power flow, energy, the P1 meter, per-battery detail,
diagnostics and device configuration.

Paste it under `views:` in a dashboard in YAML mode, or open **Edit dashboard → three-dot menu
→ Raw configuration editor** and add it there.

Cards carry no `name:` override on purpose, so every label comes from the integration. Renaming
an entity in Home Assistant changes the dashboard along with it and the two cannot drift apart.

Entity IDs in the file were derived from the device name *Zendure SolarFlow 3000 Mix AC+*.
Home Assistant deviates on a name clash — appending `_2` — or after the device is renamed, so
verify under **Developer tools → States** filtered on `zendure` if cards come up empty.

---

## How the controller works

The strategy is a single `select` entity, `Operation mode`:

| Mode | Behaviour |
|---|---|
| `standby` | Hands off. Reads and reports, writes nothing |
| `manual` | Follows the `Manual power` number; its sign picks charge or discharge |
| `smart_matching` | Tracks the grid meter towards zero exchange, both directions |
| `smart_charge_only` | Absorbs surplus only; never discharges |
| `smart_discharge_only` | Covers demand only; never charges |
| `quick_charge` | Charges at the configured maximum, ignoring the meter |
| `quick_discharge` | Discharges at the configured maximum, ignoring the meter |

The default is `standby`, so nothing moves until a mode is chosen deliberately.

### Two loops, because the two decisions have different tempos

Which mode applies and which direction runs changes on the scale of minutes. Following the
house between those decisions is a per-second job. Before v1.1.0 both ran on the battery poll,
so a meter reporting every second was read once every ten and nine samples went in the bin.

**The mode loop** runs at the end of each battery refresh, so it always acts on readings from
that same cycle. It owns everything that needs the device to be believed: mode, direction,
when a direction may start, stop or reverse, SOC protection, and the ceilings.

**The trim loop** runs once per meter sample and may do exactly one thing — adjust the limit
*within* the direction already running. It never starts a direction, never reverses one, and
never overrules a mode. Reaching zero is its floor, not a reversal: the limit is released and
the mode loop decides what happens next.

That restriction is the fix rather than a compromise. A 1700 W coffee machine was measured
producing a swing of +1750, −1700, +1550, −1500 W across 80 seconds. Those reversals were not
wrong decisions — by the time the mode loop looked, the grid genuinely had been at −1700 W for
several seconds, and charging was the right answer to that reading. The fault was lateness, not
logic, and a loop that trims every second never reaches the state where the reversal looks
reasonable.

The two loops use the same equation in different forms:

```
mode loop (absolute):     target = (grid + battery − buffer) × factor
trim loop (incremental):  target = limit + (grid − buffer) × trim strength
```

They agree whenever the device is holding the limit it was given, and they fail differently
when it is not. The absolute form needs a fresh battery reading, which only the mode loop has.
The incremental form needs only the limit last written, which the controller knows exactly
because it wrote it — but it would drift if the device silently failed to follow. Hence the
pairing: the trim loop follows, and every battery poll resynchronises against measured truth.

A single timer running both would be the worst of the two: it would correct twice for one
deviation at the fast rate, and poll the battery ten times more often than anything needs.

### Does the trim loop earn its place

Yes, and speeding the mode loop up is not an alternative. Simulated across the devices the
measurements admit:

| | Wrong-way energy | Residual at constant load |
|---|---|---|
| Mode loop only, ten seconds | 37.1 Wh | 44 W, settled |
| Mode loop at one second, no trim loop | 35.1 Wh | **2103 W, ringing** |
| **Mode loop ten seconds plus trim loop** | **14.5 Wh** | **44 W, settled** |

The trim loop more than halves the error. Running the mode loop faster instead does not work: its
absolute formula reads measured battery power, which lags the limit it was given, so at one second
it re-derives a target from a device that has not caught up.

### One rule: never order the same watts twice

Every sample is a fresh **measurement**, but not a fresh **error**. Part of what it shows has
already been ordered and is still on its way, so the loop keeps that amount and corrects only the
remainder. See 1000 W too much with 800 W already on order, and it corrects 200.

That single piece of bookkeeping is what makes the loop device-independent. There is no dead time,
response fraction, timeout or noise estimate anywhere in the controller, because it does not matter
whether the device answers in one sample or in ten — the loop simply does not ask again for what it
has already asked for.

The mode loop takes part in the same bookkeeping. It knows the outstanding amount exactly, because
it has the measured battery power: whatever the new limit asks for beyond what the device is
actually delivering is still on its way. Without that, the two loops order the same watts, which is
measurable as a slow limit cycle of several hundred watts.

Two things remain, and neither describes the device:

**A threshold**, `Trim threshold`, default 40 W, with `Mode threshold` at 10 W for the other
loop. Do not write a correction smaller than the house wanders by on its own, or the loop chases
the household into a limit cycle — observed at a load of about 200 W as 19 writes a minute with
the grid swinging between −106 and +122 W. The wander in a quiet house is roughly −30 to +20 W,
which is where 40 comes from.

Both are settings rather than constants because the right value is a property of the house: the
wander measured on this one varied between 4 and 146 W median over a single afternoon, a factor
of thirty-five. Raising them is worth trying and easy to undo — see the notes under v1.3.0 for
what was measured at 125 and 350 W, and why it is not the recommendation it first appeared to
be.

**A gain**, `Trim strength`, default 80%. Below 100 for the reason any feedback loop is: the
bookkeeping removes double-counting, not the meter's own delay.

That setting kept its name and its entity id through the rewrite, but its meaning changed: it used
to be 80% of the *deviation*, applied upward only, with downward corrections always at full
strength. It is now 80% of the *remaining* error — remaining after subtracting what is already on
order — in both directions. The asymmetry is gone because the bookkeeping makes it unnecessary: a
downward correction can no more be ordered twice than an upward one. The id was left alone
deliberately; renaming it would break every dashboard and template that references it, and this
integration has been bitten by that before.

### Why the device is not modelled

Measured in manual mode, where no controller is in the loop, the device gave no consistent rate:
750, 820, 1083 and 1500 W per second across six steps, with a dead time of nought to one sample.
Four separate attempts to pin that down to a number were wrong, each derived from a single
interval. A real load step — a 2330 W boiling-water tap, with nothing written at all — arrives in a
single sample, so the meter passes a sharp edge cleanly and the spread belongs to the device.

Simulated against the real integration code across the range those measurements admit — dead time
nought to one sample, 700 W/s to instant, meter one to two samples behind — the loop settles within
44 W on every one. Widened well past what was measured, to three samples of dead time and 400 W/s,
one corner degrades to a slow 727 W cycle; that is outside anything the hardware has shown.

### Faults stop the battery

A closed loop with no feedback is worse than no loop. When the meter cannot be believed, both
limits are written to zero and the controller reports itself blocked. That covers a missing meter,
a coordinator that has stopped updating, a reading older than `METER_MAX_AGE`, a missing or
non-numeric value, and a reading beyond the grid connection's capacity.

That last bound is the connection, not the battery. This meter reads the whole house: an inverter
ceiling of 3.6 kW would be quite wrong here, since the installation's own measurements record an
evening peak of 4384 W from an oven and a dryer together and a boiling-water tap alone draws
2330 W. 3×25 A at 230 V is 17.25 kW, and nothing past that can be real. The check earns its place
because scale and sign errors are a live risk on this API — six of them were found in the zenSDK
documentation for this device alone.

Releasing to zero is always safe: it is the direction the clamp already allows, and it cannot
reverse anything.

### Standby stops the battery, once

Selecting `standby` sends one command and then goes quiet:

```json
{"inputLimit": 0, "outputLimit": 0, "smartMode": 0}
```

The limits go to zero whether or not this controller set them — selecting standby should stop the battery, not hand it over. `smartMode` goes back to 0 in the same breath, because the smart modes force it to 1 to keep their frequent limit writes out of flash and the device will not drop to its low-power state while that flag is set. See [Standby draw](#known-limitations).

**Once, and only once.** A zero is still a command, and repeating it every poll is what v0.5.0 removed: on a device running its own energy manager the manager sets its limit back a second later, the integration overwrites it on the next poll, and the two produce a square wave in grid power at the polling interval. After the command the controller is passive again, and a limit that appears afterwards is left alone.

That is also what keeps the `writer` column in the trace meaningful: outside that one command, every limit change in `standby` came from somewhere else.

### The smart control loop

```
charging:    target = clamp( (-grid - battery + charge_buffer)    * factor , floor , max )
discharging: target = clamp( ( grid + battery - discharge_buffer) * factor , floor , max )
```

Four details do the real work.

**The first step is undershot** (factor 0.75 rather than 1.00). The device's actual response is not yet known, so committing the full correction invites overshoot. Once the direction holds, the mode loop balances at 1.00 and hands the direction to the trim loop.

**Direction is read from the device, not from memory.** The controller's own record is empty after a restart, a reload or a mode switch, while the device carries on with the limit it was last given. Deriving direction from which limit is non-zero avoids waiting for an idle state that cannot arrive.

**Start thresholds gate starting, not continuing.** A direction already running keeps being balanced whatever the grid does, so it winds down to zero when the load disappears. A direction the mode forbids is cleared and released.

**Idle means a direction change is safe, not that no direction is running.** A limit of 30 W
sits inside the deadband while the device is still charging, so the running direction is only
considered over when the device reports both limits at zero.

**Idle is decided by what is commanded**, not by measured power. An inverter draws its own standby power continuously — a 3000 Mix AC+ idles at 29 to 36 W depending on Backup mode, and only reaches a few watts with both that setting and the flag clear — so the pack never reads zero. Both limits at zero means a direction change is safe regardless.

### Buffers and thresholds

Both buffers mean the same thing: how many watts of grid **import** to aim for. The default is 5 W on each side, so the loop settles just on the consuming side of zero. That is deliberate — overshooting into import wastes the difference between the import tariff and the value of the stored energy, while undershooting into export gives away energy worth the import tariff for the feed-in rate, roughly a third of it.

Both start thresholds are a distance from zero, expressed as a positive number of watts. Which side of zero they sit on is in the name: `Start discharging at import` counts import, `Start charging at export` counts export.

Keep each start threshold further from zero than its buffer, or the direction never starts.

### Minimum power

`Min charge power` and `Min discharge power` raise the target above what the meter asks for, in every smart mode. Both default to 0, which disables the floor.

With a 150 W charge floor and 100 W of surplus, the device charges 150 W: 100 from the surplus, 50 bought from the grid. That is the point — the energy is cheap now and worth more later. Note that zero-on-the-meter only holds while both floors are at 0.

The two directions are not symmetric in value. Overcharging is compared against the future import tariff, the full retail price; overdischarging exports at the feed-in rate. Setting the charge floor is usually worthwhile, setting the discharge floor rarely is.

### Reversing direction

`Direction change delay` pauses between opposite directions: the current limit is cleared, the
configured number of seconds elapses, then the new direction is written. It defaults to 10
seconds, matching the default polling interval; set it to 0 to reverse immediately.

**Measured on a 3000 Mix AC+: a reversal costs nothing an ordinary step does not.** Eleven
commands in `manual` with PV and the largest intermittent load switched off, scored on the P1
meter because the battery reading lags 5 to 10 s. Three reversals against eight same-direction
steps of equal amplitude: dead time 1 to 5 s against 1 to 6 s, and not one sample resting near
zero on the way through. The largest — 2000 W charge to 2000 W discharge in a single command,
4000 W across zero — had the shortest dead time of the run at 1 second. On this hardware the
setting belongs at 0. Other inverters may differ, which is why it is a setting.

The smart modes already wind a direction down before starting the other one, but `quick_charge`,
`quick_discharge` and `manual` write their target straight away — so a mode switch could take
several kilowatts from full charge to full discharge inside one cycle. A 3000 Mix AC+ handles
that; an older or smaller inverter may not.

The pause is timed rather than counted in polls, and the clock starts when the opposite limit
is actually cleared rather than when the mode changed, so it cannot elapse while the old
direction is still running. A pause already under way outranks the device state — clearing the
limits is the first thing it does, and reading the resulting "no direction" as nothing to
reverse from would end the pause on the next cycle. Controller status shows `reversing` with
the seconds remaining.

At a polling interval coarser than the delay the pause lasts one full cycle: it is never
shorter than configured, only longer.

### Safety

If the meter stops reporting, both limits go to zero — a closed loop without feedback is worse than no loop. Below the lower SOC bound the battery is charged back regardless of strategy, except in `manual` and `quick_discharge` where the operator's intent is explicit; switchable off.

`smartMode` is set to RAM before writing limits, so frequent adjustments never reach flash. At a ten second interval that is tens of thousands of limit writes a year. `standby` hands it back on entry, because the device stays awake while it is set.

---

## Supported Entities

| Group | Count | Entities |
|---|---|---|
| **Sensor** | 50 | AC coupling status, AC state, Backup output power, Backup output power 2, Battery charge power, Battery discharge power, Battery module count, Battery state, DC bus voltage, DC state, Enclosure temperature, Fan level, Fan speed step, Fault level, Battery power, Grid input power, Home output power, PV input power, PV state, PV string 1–6, Remaining discharge time, SOC calibration status, SOC limit status, Signal strength, State of charge · *disabled by default:* Activation voltage, Bind state, Device timestamp, Factory mode state, Grid HD status, HV battery voltage (raw), IoT connection state, LCN state, Legacy mode, Message id, OTA state, Off-grid state, Phase switch, Powerhub status, Report timestamp, Slave address, Timezone, Timezone offset, Voltage wake-up, Write response |
| **Per battery** | 11 | Current, Max cell voltage, Min cell voltage, Power, State, State of charge, Temperature, Voltage · *disabled by default:* Firmware version, Pack type, Serial number |
| **Energy** | 5 | Energy charged, Energy discharged, PV energy, Energy imported, Energy exported |
| **Efficiency** | 2 | Charge efficiency, Discharge efficiency |
| **Binary sensor** | 12 | Data ready, Dry contact, Error, Fan, Grid connected, Heating, Lamp, Pass-through, Reverse flow · *disabled by default:* HV battery control, PV-AC coupling, SOC compensation |
| **Number** | 7 | Charge limit (AC), Charge power limit, Discharge limit, Inverter power limit, Lower SOC limit, Upper SOC limit · *disabled by default:* Calibration interval |
| **Select** | 5 | Backup mode, Converter mode, PV export · *disabled by default:* Fan speed mode, Grid standard |
| **Switch** | 2 | Skip flash write · *disabled by default:* Fan forced on |
| **Controller** | 17 | Operation mode, Controller status, Manual power, Direction change delay, Max charge power, Max discharge power, Min charge power, Min discharge power, Start discharging at import, Start charging at export, Charge buffer, Discharge buffer, Trim strength, SOC protection, Trace recording, Mode threshold, Trim threshold |
| **P1 meter entry** | 6 | Grid power total, Phase A/B/C apparent power, Meter type · *disabled by default:* Protocol type |

Entities appear only when the device reports the underlying key. `PV string 3`–`6`, `Fan level` and `Fan` are documented in zenSDK but absent from the 3000 Mix AC+ payload, so they are never created on that model.

---

## The Energy dashboard

The device publishes no cumulative counters, so kilowatt-hours are integrated here.

| Entity | Source | Feeds |
|---|---|---|
| Energy charged | `outputPackPower` | Home battery storage, energy in |
| Energy discharged | `packInputPower` | Home battery storage, energy out |
| PV energy | `solarInputPower` | Solar production, DC-connected panels only |
| Energy imported / exported | meter `total_power` | Grid consumption and return |

Integration is trapezoidal: each interval uses the average of the previous and current reading, which is exact for any linear ramp. Gaps beyond five minutes are skipped rather than bridged, and readings that are negative or above 20 kW are ignored. Totals persist across restarts and are never reset on reload — the Energy dashboard reads a drop as a meter replacement and would double-count.

All sources are AC-side fields, matching the side the meter is on. Conversion loss therefore stays visible as the gap between the grid figures and the battery figures rather than being absorbed into them.

### Conversion efficiency

Two diagnostic sensors surface that loss. `Charge efficiency` is DC over AC, `Discharge efficiency` is AC over DC. Only packs in the matching state contribute.

The figure drops sharply at low power, because the converter's own consumption is nearly constant. How large that fixed part is depends on Backup mode — see [Standby draw](#known-limitations). On a 3000 Mix AC+ with `Backup mode = closed` it is around 27 W; with `economic` it is around 41 W.

Measured over eight uninterrupted hours at a stable discharge, 29,239 samples with `closed`:

| Band | AC delivered | DC from cells | Efficiency | Loss |
|---|---|---|---|---|
| 100–150 W | 596.3 Wh | 738.7 Wh | **80.7%** | 32 W |
| 150–200 W | 569.4 Wh | 681.0 Wh | **83.6%** | 31 W |

The loss is flat across that range, so the proportional term cannot be read from it. Charge efficiency is roughly 95% and nearly flat. Round-trip over the lifetime counters was 80.8%.

That is not a measurement error, and it cuts both ways: trickle-discharging is expensive, but so is leaving the converter awake with nothing to do. Whichever is worse depends on how long it sits idle — and on whether your tariff still nets import against export.

---

## Notes on correctness

The zenSDK document is wrong or incomplete in several places. Each correction below was found by comparing it against live hardware.

**Temperatures** are stored in tenths of a Kelvin: `(raw - 2731) / 10`.

**Pack voltage** (`totalVol`) is in centivolts despite the document listing whole volts. A raw 2688 is 26.88 V.

**SOC setpoints** (`socSet`, `minSoc`) are in tenths of a percent. The document's `minSoc` range of 0–50 is also wrong; a live sample read 800, meaning 80.0%.

**Pack current** (`batcur`) is a 16-bit two's complement value in deciampere. Read unsigned, every discharge becomes roughly 6500 A.

**`gridReverse`** value 2 forbids PV export and 1 allows it. The document offers no labels; reading 2 as `auto` would be permissive on a device that is actually blocking export.

**The key is `oldMode`,** not `OldMode` as documented.

Three independent checks confirm the voltage and current scales, across samples at very different power levels:

| Check | Sample 1 | Sample 2 |
|---|---|---|
| `totalVol` × `batcur` vs reported pack power | 26.88 V × 22.8 A = 613 W vs 612 W | 26.80 V × 1.7 A = 45.6 W vs 46 W |
| Cell voltage × 8 vs `totalVol` | 3.36 V × 8 = 26.88 V | 3.35 V × 8 = 26.80 V |

The cell-count agreement also identifies the pack as 8S LFP.

---

## Brand images

The integration ships its own icon and logo in `custom_components/zendure_restapi/brand/`. Home Assistant 2026.3 and later picks these up directly, taking priority over the brands CDN; older versions ignore the folder and fall back to the default placeholder. No submission to `home-assistant/brands` is needed.

---

## Debug logging

The controller logs every decision it makes at `DEBUG`: which property is written and what it
held before, which writes are skipped because the device already holds the value, why a
reversal pause started and how long remains, and the resulting state with the readings behind
it.

```yaml
# configuration.yaml
logger:
  default: warning
  logs:
    custom_components.zendure_restapi: debug
```

A typical reversal reads:

```
write inputLimit: 3000 -> 0
skip outputLimit=0, device already holds it
reversal charge -> discharge: cleared both limits, pausing 10s
reversing | pausing 10s before discharge | dir=none target=0W grid=0.0W battery=-3000.0W
reversing | pausing 5s before discharge  | dir=none target=0W grid=0.0W battery=-3000.0W
reversal pause elapsed, discharge may start
write acMode: 1 -> 2
write outputLimit: 0 -> 3000
quick | discharging at maximum | dir=discharge target=3000W grid=0.0W battery=-3000.0W
```

The status sensor shows the outcome; this shows the reasoning that led there, which is what you
need when a mode does something unexpected three steps into a sequence.

### The sample trace

The **Trace recording** switch writes one CSV row per meter sample to
`config/zendure_restapi/trace_<date>_<time>.csv`. Switching it on always starts a new file;
switching it off flushes the buffer and releases the file, so a measurement run is one file. It
reports off after a restart whatever it was doing before — a recording that silently resumes is
worse than one you have to start yourself.

The switch is the only control. Hanging it on the debug log level would need no new entity, but
that level cannot be lowered again without a restart, so every recording would run until the next
one and no file would ever be closed properly.

The row is the measurement and the command side by side — grid power as accepted for control
and as the meter reported it, battery power on both the AC side and at the cells with the pack
state that signs it, state of charge, both limits, `acMode`, `smartMode`, the age of the battery
payload, and the loop's own bookkeeping. Nothing is averaged, scored or derived, because a definition of "how well did that
go" is worth changing after you have looked at the data, and one written into the file cannot be.

The `writer` column names whoever last moved a limit: `mode`, `trim`, `none`, or `foreign` for a
setpoint this integration did not write. Telling those apart needs a short history of this
controller's own writes rather than just the latest one: the device's report lags a write by a
sample or two, so at a one-second battery poll an older value of our own comes back after a newer
one, and matching on the latest write alone reads that as somebody else's setpoint. That last case is the useful one. With the operation
mode in `standby` this controller writes nothing at all, so a device running its own energy
manager can be recorded under exactly the same conditions and scored on exactly the same
yardstick as the loops here. Set the operation mode to `standby`, let the device manage itself,
and compare the two files.

Two things to watch when doing that. The battery poll interval sets the resolution at which a
foreign write becomes visible, so a manager that evaluates on the same period as the poll will
be undercounted — for a measurement run, poll the battery every second or two. And entering
`standby` costs two writes of its own — clearing a limit it still owned, then handing back
`smartMode`; give it a minute before the run starts.

At one sample a second a day of recording is roughly 86,000 rows and some 15 MB. Rows are
buffered for half a minute at a time, so an abrupt restart loses that half minute.

## Reporting an unsupported property

The integration logs every reported key it has no entity for, once each, at `INFO` level. For a complete picture, download diagnostics from the device page: the file contains the device report plus the full list of unrecognised keys.

Diagnostics files end up attached to public issues, so identifying values are stripped first. That covers both the plain keys and their flattened per-pack form — `pack1.sn` is redacted along with `sn`, which an exact-match filter would miss. The MQTT status is summarised rather than copied, because its contents are undocumented and may carry a broker address or device key.

---

## Known limitations

**No failsafe if Home Assistant stops.** A limit written before the outage keeps executing —
the device has no watchdog that returns it to zero. Keep `Max charge power` and
`Max discharge power` at values you would accept running unattended.

**Standby draw needs two settings, not one.** With limits at zero the inverter still drew 36 W
from the cells — measured as a median with quartiles at 35 and 36, and confirmed against the raw
cell values, 26.48 V at 1.3 A. Over a day that is 0.86 kWh, about a quarter of a 3.8 kWh
household.

Two settings together bring it down, and neither works alone:

| Backup mode | `smartMode` | Cell draw |
|---|---|---|
| economic | 1 | 36 W |
| economic | 0 | 35 W |
| closed | 1 | 29 W |
| closed | 0 | **5 W** |

`standby` writes the flag; **Backup mode must be set to `closed` by hand**, and that gives up the
sub-10 ms changeover on the backup outlet. Earlier releases of this README attributed the whole
36 W to mandatory grid monitoring under VDE-AR-N 4105 and UL 1741. That cannot be the full story:
grid monitoring does not stop when the backup outlet is closed, and the draw does.

**The flag is only worth those 25 W at a zero limit.** Once the converter is actually converting
it is awake regardless: measured at 500 W of charging, the cells gave 453 W with the flag set and
453 W with it clear. The 47 W of loss there matches `27 + 0.035 × 500` from the curve above. The
flag governs where limit writes are stored, not the power state.

**And the device does not clear the flag by itself.** In `manual` with both limits at zero it
stayed clear for as long as nothing wrote to it. What sets it back is this controller: at the
start of every smart cycle, and again before any limit write in any mode — so a limit and a set
flag cannot be separated from Home Assistant. Reports elsewhere of the device reverting the flag
on its own were not reproducible here; what looked like that turned out to be the device's own
energy manager still running alongside.

**`acCouplingState` bit 15 is undocumented.** It is set on every sample from a 3000 Mix AC+ and
its meaning is unknown. Reported as-is.

**Efficiency is not constant.** The converter's overhead has a fixed part and a proportional one,
and the fixed part dominates. Treating it as a constant, in either direction, gives wrong answers
at the other end of the range. The fixed part also depends on Backup mode: around 27 W with
`closed`, around 41 W with `economic`, so a curve measured under one does not describe the other.

**Efficiency cannot be read off a trace with transients in it.** `battery_ac_w` and `pack_dc_w`
are 5 to 10 seconds out of step, so on any edge the same division returns nonsense — in one
otherwise clean night, 43% and 104% on bands with 70 and 130 samples. Only long stretches at a
stable power level give a usable number, and the shape of the curve above 200 W is not
established under `closed`. That needs a run holding several fixed power levels for long enough
to average out the sampling offset between the AC and cell readings.

---

## Changelog

The current release is listed in full. Earlier ones are grouped by minor version, keeping the
findings and the fixes that changed behaviour and dropping the housekeeping.

### v1.4.0 — No settling hold, a rest state, and smaller trace files

- **`holding` is now governed by `Direction change delay`**, the same setting the quick and manual
  modes already obeyed. At zero — the default on hardware that does not need it — there is no
  settling state at all. It used to come from a constant of its own and was armed on every start,
  which is why `holding` followed `starting` in 28 of 28 observations, 17 of them from standstill
  where no direction had changed and there was nothing to settle from. On a busy evening that was
  280 seconds of doing nothing in two and a half hours.
- **Measured before it was changed**, in `manual` with PV and the largest intermittent load
  switched off: three reversals against eight same-direction steps of equal amplitude. Dead time 1
  to 5 s for the reversals against 1 to 6 s for the plain steps, and not one sample resting near
  zero on the way through. The largest reversal — 2000 W charge to 2000 W discharge in a single
  command, 4000 W across zero — had the shortest dead time of the whole run at 1 second. So on
  this inverter a reversal costs nothing that an ordinary step does not, `Direction change delay`
  belongs at 0, and with it there is no reason to hold. On hardware that does need a pause, set
  the delay and both paths observe it.
- **With both of its own limits at zero across two consecutive polls, the controller hands
  `smartMode` back to the device** so the inverter can drop to its low-power state. There is no
  setting: the poll rate is the hysteresis. A limit passing through zero during a direction change
  lasts a sample, not a poll, so it never triggers this. Leaving needed no code — every limit
  write already sets the flag first, in every mode, so the first order out of rest carries the
  wake-up with it. Entering is written once and never held down; repeating it is what turned
  v0.5.0 into a square wave against the device's own manager.
- **What that is worth on a 3000 Mix AC+ with Backup mode closed.** With both limits at zero the
  cells give 30 W with the flag set and 5 W with it clear — 25 W. While actually converting the
  flag changes nothing: 453 W from the cells at 500 W of charging either way, so the saving exists
  only at a zero limit. Waking costs nothing measurable — the same 500 W order responded in 6 s
  from rest and 6 s from awake.
- **The rest state has not yet been seen in production.** It is built and tested — eight
  behavioural checks against the real module — but no idle stretch since it went live has lasted
  the two polls it needs. Over five hours of clean trace both limits sat at zero for 17.2% of the
  time, but almost all of that was a single stretch of 44 minutes with the battery held in a mode
  that could not act; three hours of ordinary operation gave stretches with a median of 6 seconds
  and a maximum of 12, and nine hours of night gave none at all. So: nothing while actively
  regulating, and 25 W times however long the battery structurally has nothing to do. That second
  figure needs a day without interventions to establish, and it is saved cell energy rather than
  avoided import, so what it is worth is whatever that charge displaces later. Treat the yield as
  undetermined.
- **Trace files roll over at roughly 5,000 rows** instead of 100,000 — about an hour at a
  one-second meter, some 150 kB. The old limit was more than a day and some fifteen megabytes per
  file, which is exactly the file you cannot copy off a running system or recover after a crash.
  The rollover point is drawn per file within ±400 rows of the nominal size rather than fixed:
  files that all break on the same minute and second look like a pattern in the data when read
  back, and a load that happens to be periodic with the file length would otherwise fall in the
  same place in every file. Each recording stays one series — the timestamp is taken once when the
  switch goes on and every file of that recording carries it with a sequence number,
  `trace_<date>_<time>_001.csv` and up — so a rollover remains distinguishable from a new
  recording. The row count includes the unflushed buffer, so a file lands on its limit rather than
  up to one flush past it.
- The control loops are otherwise untouched. Documentation corrected in the same release: the
  conversion-efficiency figures now distinguish the two Backup mode settings, carry an eight-hour
  measurement at a stable discharge, and warn that efficiency cannot be read off a trace
  containing transients.
- *A first attempt at the rest state shipped a `Rest delay` setting in seconds. Measurement showed
  it did nothing: the check runs on the battery poll, entering needs two polls inside one idle
  stretch, and at a ten second poll that means nothing under about eleven seconds could ever fire
  — so every value from 0 to 10 behaved identically. The setting is gone; a constant floor of ten
  seconds keeps the hysteresis intact when the poll interval is set short for a comparison run.*

### v1.3.0 — Two write thresholds you can set

- **Added `Mode threshold` and `Trim threshold`**, the smallest adjustment each loop bothers to
  write. They replace two constants of 10 and 40 W and default to those values, so nothing
  changes until they are raised on purpose. Range 0 to 500 W.
- Both apply to **adjustments only**. Opening a direction is never gated: the mode loop skips the
  threshold while starting, and the trim loop is only entered while a direction is already
  running. A threshold above the day's surplus therefore cannot leave the battery idle.
- **What the numbers do, and what they do not.** Scoring 1818 writes over four hours by the grid
  error four seconds after each one against the three seconds before it, the optimum came out at
  mode 125 and trim 350: from 1818 writes to 318, and from +13.8 to +81.6 Wh. Measured again on
  the hardware against churn — import and export integrated separately, which is the quantity
  that costs money — the same settings came out **worse**:

  | | Mode 125 / trim 350 | 10 and 40 |
  |---|---|---|
  | Churn | 126 Wh/h | **110 Wh/h** |
  | Band, p5 to p95 | 481 W | **303 W** |
  | Writes per minute | 3.5 | 8.4 |

  The first measure rewards few large corrections: a write at 300 W of error scores well while
  the error is allowed to grow in between. What matters is that the error stays small, and only
  churn sees that. The two windows were not equally restless, so this is a direction rather than
  a verdict — but the shipped defaults stay at 10 and 40 until a run alternating the two settings
  in blocks says otherwise.
- The two buffers, the two start thresholds and the two minimum powers now run to 200 W rather
  than 1000, and every setting in watts steps by 5.
- Fixed a `TypeError` introduced with the thresholds: they read their setting with a default
  argument where `ZendureSettings.get_int()` takes none, so the mode loop raised on every cycle
  and wrote nothing at all. Observed as 74 seconds of `smart_matching` doing nothing while 900 W
  of surplus went to the grid. The test harness now mirrors the real signatures — it had been the
  lenient one, which is why the call passed there and raised on the device.

### v1.2.0 — Standby lets the device sleep

- **Standby now hands the flash flag back.** The smart modes force `smartMode` to 1 so their
  frequent limit writes stay out of flash, and nothing put it back, so the device stayed awake at
  its full idle draw. Standby writes `smartMode` 0 once on entry and nothing after. Measured on a
  SolarFlow 3000 Mix AC+, cell draw with nothing running:

  | Backup mode | `smartMode` | Cell draw |
  |---|---|---|
  | economic | 1 | 36 W |
  | economic | 0 | 35 W |
  | closed | 1 | 29 W |
  | closed | 0 | **5 W** |

  Necessary but not sufficient: **Backup mode must be set to `closed` by hand**. That is a
  standing choice about the backup outlet — closing it gives up the sub-10 ms changeover — and
  not something to toggle on every standby, so the controller leaves it alone. With both applied,
  idle drops by roughly 0.75 kWh a day.
- An owned limit is still cleared first, on the cycle before, so the cleanup command does not
  land in flash. Standby continues to touch no limit at all, which is what keeps the `writer`
  column in the trace meaningful.

### v1.1.0–v1.1.3 — The trim loop and the sample trace

- **The controller became two loops.** The mode loop runs on the battery poll and owns mode,
  direction and safety; a trim loop subscribed to the meter adjusts the limit within the running
  direction once per sample. It cannot start, reverse or end a direction, so every expensive
  decision stays with the loop that has fresh battery data. Added `Trim strength` (30–100%,
  default 80): how much of the *remaining* error to correct per sample — remaining, because the
  loop subtracts what it has already ordered and not yet seen.
- **A fault now stops the battery.** `Meter max age` is enforced at last, and a missing, stale,
  non-numeric or implausible reading writes both limits to zero rather than steering on a guess.
- **Added a per-sample CSV trace** in `config/zendure_restapi/`, started and stopped by the
  **Trace recording** switch. One row per meter sample, recording what was measured and what was
  commanded, and scoring nothing: band, error in watt-hours and recovery time are all defined
  afterwards, on the file. The `writer` column names whoever moved a limit, including `foreign`
  for a setpoint this integration did not write — so a device managing itself can be recorded
  under the same conditions and measured against the same yardstick as the loops here. Telling
  the two apart needs a short history of this controller's own writes rather than only the
  latest, because the device's report lags a write by a sample or two.
- **Fixed the mode loop dropping reductions from its bookkeeping.** It booked what a new limit
  asks for beyond what the device delivers, clamped at zero, so increases were recorded and
  decreases were not. On a falling load or a mode change the trim loop then saw the whole
  deviation as fresh, ordered a second reduction on top of the one in flight, and drove the limit
  to zero — after which the battery stopped and the mode loop needed a start plus a settling
  hold, some thirty seconds of doing nothing. Measured three times with an identical signature.
  The difference is now taken signed.
- **Fixed `pack_dc_w` reading an AC-side field instead of the cells.** Derived from
  `packInputPower` and `outputPackPower`, which sit on the AC side like the rest, it duplicated
  `battery_ac_w` in every row and the converter loss it exists to expose stayed invisible. It now
  sums `packN.power` across the packs, signed by each pack's state.
- **Fixed a race in the idle test.** A payload that may predate the most recent trim write is no
  longer treated as idle, so a reversal cannot be written into a running inverter.
- Fixed `Battery power` being permanently unavailable since v1.0.3, and a status message that
  reported `within thresholds` for a direction the mode had ruled out.
- The trim loop now runs during the settling hold and on the starting cycle; it used to be
  disabled in both, which left short load events handled entirely at ten-second granularity.
- Every setting in watts shares one step of 5 W, and the six that express a distance from zero on
  the grid — the two buffers, the two start thresholds and the two minimum powers — share one
  range of 0 to 200 W.

### v1.0.0–v1.0.4 — First stable release

The point at which every defect found against live hardware had been fixed and the remaining
unknowns were documented rather than suspected.

- **v1.0.0** adds `Direction change delay`, a pause between opposite directions. The smart modes
  already wound a direction down before starting the other, but the quick and manual modes wrote
  their target immediately, so a mode switch could reverse several kilowatts within one cycle.
  Also adds a `LICENSE` file and `DEBUG` logging of every controller decision.
- **v1.0.1** fixed a deadlock between two sources of truth: the wind-down branches trusted the
  controller's memory while the step function derived direction from the device. Observed as
  *waiting for idle before discharge* while the battery charged at 3 kW. Direction is now
  reconciled against the device every cycle.
- **v1.0.2** fixed two pieces of logic undoing each other — reaching the deadband cleared the
  remembered direction while reconciliation restored it, 98 times in 100 minutes on hardware.
- **v1.0.3** added `Battery power`, one signed figure, positive while discharging.
- **v1.0.4** characterised the P1 meter against an independent reader through a splitter, about
  1,300 paired samples: accurate and phase-consistent, but 0.4 to 1.1 seconds behind, with no
  cumulative counters.

### v0.8.0–v0.9.9 — Meter, energy and thresholds

- **Fixed a permanent offset**: battery power was read on the DC side, larger than the AC side by
  the converter's own consumption, so the loop settled exporting by roughly that loss instead of
  importing by the buffer.
- **Fixed the sign of `Charge buffer`.** The two buffers carried opposite meanings under one
  name; both now mean watts of grid import.
- **Fixed a mode running a direction it forbids** — `smart_charge_only` discharging at 200 W
  because the device happened to be discharging when the mode was selected.
- **Removed `Invert meter sign`**, verified against a second meter on the same connection: 2088 W
  on the Zendure against 2059 W on the reference reader, both positive while importing.
- **Fixed a diagnostics leak**: the battery serial travelled in `flat_data` as `pack1.sn`, which
  exact-match redaction did not cover.
- Both start thresholds renamed and made positive, so which side of zero they sit on is carried
  in the name rather than in the sign.
- `Min charge power` and `Min discharge power` became floors that raise the target, rather than
  gates that decline to act. They apply in every smart mode.
- Energy counters integrate AC-side fields rather than the DC pack reading, matching the side the
  meter is on. Added `PV energy`, `Charge efficiency` and `Discharge efficiency`.
- `chargeMaxLimit` became writable as `Charge power limit`; brand images added; minimum Home
  Assistant version raised to 2024.10.0.

### v0.7.0–v0.7.1 — Energy dashboard

- v0.7.1: fixed the options flow resetting every controller setting, because it replaces the entire options dict rather than merging into it.
- v0.7.0: energy counters integrated trapezoidally from power, restoring across a restart. Gaps beyond five minutes are skipped and implausible readings ignored.

### v0.4.0–v0.6.0 — Operation-mode controller

- v0.6.0: version aligned across the integration, release script, dashboard and session reports.
- v0.5.3: settling period measured in seconds rather than polling cycles.
- v0.5.2: removed the `passive` mode; with `standby` no longer writing, the difference favoured `standby`.
- v0.5.1: smart modes deadlocked after any restart. Direction is now read from the device rather than from the controller's memory.
- v0.5.0: `standby` writes nothing at all. It previously forced both limits to zero on every poll, which on a device running its own energy manager produced a square wave at the polling period.
- v0.4.2: fixed stale readings outside the smart modes, and made the power ceiling the lower of setting and device.
- v0.4.1: discharging never started, because the idle test compared measured pack power against a 30 W band while the inverter's own draw is around 41 W.
- v0.4.0: seven operation modes executed once per poll. Corrected `gridReverse`.

### v0.1.0–v0.3.0 — Foundation

- v0.3.0: clearer entity names throughout.
- v0.2.1: corrected the `minSoc` range from the documented 0-50 to 0-100.
- v0.2.0: P1 meter support; corrected `socSet`, `minSoc` and `totalVol` scaling and the `oldMode` key.
- v0.1.0: local HTTP client, mDNS discovery, dynamic entity creation driven by the keys the device actually reports.

---

## Inspiration & Acknowledgements

| Resource | Used for |
|---|---|
| [Zendure/zenSDK](https://github.com/Zendure/zenSDK) | Local HTTP API and SolarFlow property reference |
| Zendure SolarFlow FAQ | Grid-monitoring standby behaviour under VDE-AR-N 4105 and UL 1741 |
| Community zenSDK automation | Control-loop structure: undershot first step, idle gating, direction hold |

---

## Disclaimer

This software is **not affiliated with or endorsed by Zendure** in any way. The Zendure name and product names are trademarks of Zendure.

This integration writes directly to a grid-connected battery inverter. Provided **"as-is"** without warranty. The authors accept no liability for any damage, loss of data, or service disruption.

## Licence

[MIT](LICENSE)
