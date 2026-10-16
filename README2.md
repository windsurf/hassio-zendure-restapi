# Zendure RestAPI – Technical reference

*The short version with installation in three steps is in [README.md](README.md) — 🇳🇱 [Nederlands](README.nl.md) · 🇩🇪 [Deutsch](README.de.md). This page is the full reference: how the controller works, what was measured, every entity, and the changelog. Questions and experiences: the [forum topic](https://community.home-assistant.io/t/zendure-restapi-local-zero-export-control-for-solarflow-batteries/1029076).*

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)

> **Disclaimer:** This software is not affiliated with or endorsed by Zendure in any way. It is provided "as-is" without warranty or support, for the educational use of developers and enthusiasts. Use at your own risk.

Local monitoring and control of Zendure SolarFlow batteries over the zenSDK HTTP API, with the Zendure P1 meter, a HomeWizard P1 meter, a YouLess, an EcoTracker or a Shelly Pro 3EM as grid reading. No cloud account, no MQTT broker, no Bluetooth pairing — the device runs a plain JSON REST server on your LAN and this integration talks to it directly.

Beyond reading the device, the integration runs an **operation-mode controller**: seven strategies that turn a price decision made elsewhere into charge and discharge limits, tracking a linked meter towards zero grid exchange.

> **Actively tested with:** Zendure SolarFlow 3000 Mix AC+ (`solarFlow3000MixAC+`) and the Zendure P1 meter (three-phase, meterType 3)

![Zendure dashboard](images/dashboard.png)

*The example dashboard, showing `smart_matching` holding the grid at zero: the house draws
175 W, the battery supplies it, and the meter reads 0 W. A ready-made view is included — see
[Dashboard](#dashboard).*

---

## 📌 Supported products

Every SolarFlow on the local zenSDK API speaks the same protocol, so every model below can be installed and read out. What differs is how much of the control loop has been proven on it. Only the 3000 Mix AC+ has been run by the author; everything else rests on the shared protocol, Zendure's property documentation and Zendure's own Home Assistant integration, which drives these models over the same local API.

| Model | PV on the device | Confidence | Basis |
|---|---|---|---|
| SolarFlow 3000 Mix AC+ | no | **reference** | developed and measured on it |
| SolarFlow 4000 Mix AC+ | no | **high** | same platform and internal 8 kWh pack (`packType` 70); driven locally by Zendure's integration |
| SolarFlow 2400 AC, 2400 AC+, 1600 AC+ | no | **high** | listed by zenSDK; same AC-coupled layout as the reference |
| SolarFlow 800, 800 Plus, 800 Pro, 800 Pro 2, 2400 Pro | yes | **experimental** | same protocol, but the control loop has only run on a device without PV of its own — see [Device support](#device-support) |
| SolarFlow 4000 Mix Pro | yes | **experimental** | as above, and not yet in Zendure's own integration either |
| Zendure P1 meter | – | **reference** | the grid meter this integration was built on |
| YouLess LS120 (external grid meter) | – | **high** | measured against the Zendure meter, 0.4–1.1 s faster; integration path new in v1.5.0 |
| HomeWizard P1 meter, API v1 (external grid meter) | – | **high** | documented local API, read every second; new in v1.5.1 |
| EcoTracker, Shelly Pro 3EM (external grid meter) | – | **experimental** | documented local APIs; new in v1.5.1 |
| Smart Meter 3CT | – | **experimental** | same `/properties/report` and `total_power` as the P1 meter; read it as external meter type *Zendure* |
| Hyper 2000, Hub 1200/2000, AIO 2400, Ace 1500 | – | **not possible** | no local HTTP API; MQTT or Bluetooth only |

**Own one of these?** Every model above the line *not possible* is worth trying — install it, and open a
[New model report](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml)
with the diagnostics download attached, whether it works or not. One report is enough to move a model up a level.
Details on what differs per model are under [Device support](#device-support).

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

Add the battery and, if you have one, the Zendure P1 meter as **separate entries**. The controller links to the Zendure meter automatically when exactly one is present. A HomeWizard, YouLess, EcoTracker or Shelly Pro 3EM is an entry of its own too — **Add integration → Zendure RestAPI → External P1 meter** — and is linked the same way; see [An external P1 meter](#an-external-p1-meter).

The polling interval is configurable from 1 to 60 seconds under **Configure**. The default is 10 seconds, and it doubles as the control interval.

### Languages

The setup and options screens, error messages, entity names and the states of selects and status
sensors are translated into English, Dutch, German, French, Italian, Spanish, Portuguese, Polish,
Czech, Swedish and Danish. Screens follow the language of your user profile; entity names follow
the language of the system (*Settings → System → General*).

**Entity IDs stay English in every language.** For most languages Home Assistant would build a new
entity ID from the translated name — `sensor.…_laadtoestand` on a Dutch system, `…_ladezustand` on a
German one — and the dashboard, the documentation and every shared automation would stop matching.
This integration builds the ID from the English name instead, so `sensor.…_state_of_charge` is the
same everywhere. Existing entity IDs never change: Home Assistant keeps them in its registry.
The operation modes keep their technical names (`smart_matching`, …) in every language, because
automations and documentation refer to them by those names. Translations other than English and
Dutch were made without a native speaker: corrections are very welcome as an issue or pull
request against `custom_components/zendure_restapi/translations/`.

### Device support

Entity creation is driven by the properties the device actually reports, not by a hard-coded model list. Every SolarFlow device on the local zenSDK API shares one property set, so an unlisted or brand-new model still gets full entity coverage; it is simply labelled `SolarFlow (generic)` until its name is added to `registry.py`. Power ceilings come from the device itself (`chargeMaxLimit`, `inverseMaxPower`), and every power entry runs to 5,000 W.

Model naming prefers the `product` field in the report payload, which is authoritative. The mDNS service name is only a fallback.

The confidence per model is listed under [Supported products](#-supported-products) at the top of this page.

**Why PV on the device is experimental.** The loop reads the battery's contribution as `outputHomePower − gridInputPower`. On an AC-coupled device that is the battery and nothing else. With panels on the device's own inputs it includes their output, so what the loop treats as battery power is battery plus PV. That may well be the right quantity for holding the grid at zero, but it has not been tested, and a trace from such a device is what would settle it.

**One battery steers at a time.** Two batteries in a smart mode would each see the whole grid deviation and correct it twice between them, so the second battery to enter a smart mode is stopped with status `blocked` and a reason naming the first. Manual, quick and standby are unaffected. One Home Assistant instance is assumed to be one grid connection. Sharing a deviation between batteries is a planned extension and needs measurements from a two-device installation first.

**Battery packs** get a `Model` sensor derived from the serial number and `packType`, with the nominal capacity as an attribute. The table follows Zendure's own integration; on this installation only the Mix series' internal pack (`I8000`) has been confirmed.

**Have a model that is not marked reference?** Open a *New model report* issue and attach the diagnostics download (device page → three dots → Download diagnostics). Serial numbers and addresses are redacted, and the file lists every property this version has no entity for.

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

`dashboard/zendure_restapi.yaml` is the dashboard **Zendure RestAPI** in two views, built from
standard Home Assistant cards only. The view **Price Control** holds the Price Control device: the
switch, its status, the price, both price limits and the mode per band (see
[Price control](#price-control)); without one it explains how to add it.
The view **Zendure RestAPI** has thirteen sections:

| Section | Content |
|---|---|
| Zendure Home Battery | state of charge, battery power, state, SOC limit, remaining time, error |
| Operation mode | what the controller does and why, the mode select, controller status, trace recording, trim strength, direction change delay |
| Overview | gauges for grid, state of charge, AC input and output, both limits, and the conversion efficiencies once measured |
| Device control | converter mode, SOC limits and protection, charge and discharge limit, Meter source, manual power |
| Controller settings | the ten controller settings |
| Power flow, Energy, Configuration, Battery 1, System status, Diagnostics, Zendure P1 Meter | readings and device settings |
| External P1 Meter | power, status, telegram age, the External meter select and, for HomeWizard and YouLess, the energy registers; shown only when an external meter is configured |

**Created for you.** Tick *Create the Zendure RestAPI dashboard* when adding the battery — or
later under the battery's **Configure** — and the dashboard **Zendure RestAPI** appears in the
sidebar: an ordinary dashboard, editable like any other. Before saving it, the integration fits
the view to this installation: the entity IDs of your battery and Zendure P1 meter, power gauges
up to the battery's own AC limit, and no cards for entities your model does not report. Without
a Zendure P1 meter the grid gauge reads the external meter and the P1 section is left out. An
existing dashboard at `/zendure-restapi` is never overwritten; delete it and tick the option
again to rebuild it. A notification says what happened.

Home Assistant has no public call for an integration to add a dashboard. The integration uses
the same route the frontend uses when you create one; should that route ever be missing, the
notification gives the manual route instead and nothing else changes.

**By hand.** The power gauges in the file run to 3000 W, the AC limit of the 3000 Mix AC+; for
another model change `max:` to its limit, as the comment at the top of the file explains. Create
a dashboard, open **Edit dashboard → three-dot menu → Raw configuration editor** and replace its
content with the file below its comment block.

Cards carry no `name:` override on purpose, so every label comes from the integration. Renaming
an entity in Home Assistant changes the dashboard along with it and the two cannot drift apart.

Entity IDs in the file were derived from the device name *Zendure SolarFlow 3000 Mix AC+*.
Home Assistant deviates on a name clash — appending `_2` — or after the device is renamed, so
verify under **Developer tools → States** filtered on `zendure` if cards come up empty.

---

## Price control

Dynamic trading, built in. At or below the **cheap price** the battery charges, at or above the
**expensive price** it discharges, and in between it follows the meter or rests — each band in a
mode of your choosing. Any sensor that gives the current price per kWh will do: Nord Pool,
EnergyZero, Zonneplan, Tibber, Frank Energie, or a template of your own.

**Setting it up.** Price control is a device of its own, linked to one battery — this
integration's own steering, not a Zendure product, so it is simply called **Price Control**. Add
it under **Add integration → Zendure RestAPI → Price control**, choose the battery and the price
sensor; the sensor can be changed later under its **Configure**. Then switch it on. Everything
else is an entity of the device, editable live and shown in the *Price Control* tab of the
dashboard. Entity IDs start with `price_control`; a second Price Control, for a second battery,
carries that battery in its name and IDs.

| Entity | What it does | Default |
|---|---|---|
| Price Control (`switch.price_control`) | on or off | off |
| Cheap price | at or below: the cheap-price mode, in the currency Home Assistant is set to | 0.15 |
| Expensive price | at or above: the expensive-price mode | 0.35 |
| Cheap price mode | `quick_charge` (from the grid at full power) or `smart_charge_only` (surplus only) | `quick_charge` |
| Expensive price mode | `smart_discharge_only` (cover the house, no export) or `quick_discharge` (sell at full power) | `smart_discharge_only` |
| In-between mode | `smart_matching` (grid at zero) or `standby`; also without a usable price | `smart_matching` |
| Electricity price | the price as price control reads it | — |
| Status | `off`, `no_price`, `cheap`, `between` or `expensive`; the target mode as attribute | — |

**How it decides.** On every price change, every quarter of an hour and at start-up. It writes
the Operation mode only when its target changes, or when it is switched on: a mode set by hand
holds until the price crosses a limit. Without a usable price — no sensor chosen, or the sensor
unavailable — and with a cheap price at or above the expensive one, the in-between mode applies,
which buys and sells nothing on purpose. The battery's own SOC limits still apply: the smart
modes do not order into a full or empty battery, and the quick modes report the refusal.

**One writer.** Leave price control off when an automation or planner of your own already sets
the Operation mode; two writers would undo each other's choices.

**Set the gap wide enough.** A full cycle loses about a tenth of the energy at full power and up
to a fifth at a few hundred watts (see [Conversion efficiency](#conversion-efficiency)), so the
expensive price has to exceed the cheap one by more than that to earn anything. Two fixed prices
are the simple start; a planner that looks at the whole day's prices does better, and drives the
same Operation mode.

**Next.** This is the simple version, on two fixed prices. A fuller one is planned for v1.5.7:
the cheapest and most expensive periods of the day from the Nord Pool day prices (the HACS
integration by default), separate import and export prices, conversion losses on both sides,
and the spread as an amount per kWh after those losses.

---

## An external P1 meter

The control loops can take their grid reading from a P1 reader other than the Zendure meter.
The structure is generic — one adapter per reader, an endpoint and a parser — so readers are added
without touching the loops. **A Zendure P1 meter is not required**: with an external reader
configured and the meter source on `auto` (the default), the smart modes run on that reader alone.

| Reader | Endpoint | Field | Telegram time | Poll interval | Status |
|---|---|---|---|---|---|
| HomeWizard P1 meter, local API v1 | `/api/v1/data` | `active_power_w` | no | 1 s | supported; documented local API |
| YouLess LS120 | `/e?f=j` | `pwr` | yes, `tm` | 250 ms (100–1000) | measured by the author |
| Zendure P1 meter / Smart Meter 3CT | `/properties/report` | `total_power` | no | 1 s | P1 measured by the author; 3CT same API, unseen |
| EcoTracker (everHome) | `/v1/json` | `power` | no | 1 s | experimental |
| Shelly Pro 3EM | `/rpc/EM.GetStatus?id=0` | `total_act_power` | no | 1 s | experimental; the clamps must sit on the grid connection |

### Adding it

**Settings → Devices & Services → Add integration → Zendure RestAPI → External P1 meter**: choose
the type, enter the IP address and, for a YouLess, the poll interval. The address is tried once
before the entry is saved, so a wrong address or a wrong type is caught on the form. A HomeWizard
P1 meter is **discovered** by itself: it is offered under *Discovered* as soon as its local API is
on. Its sockets, kWh meters and water meters announce themselves on the same service and are
ignored. Address and interval can be changed later under **Configure** on the entry.

The entry is a device named **External P1 meter**, whatever the reader, with these sensors:

| Sensor | Entity ID | Notes |
|---|---|---|
| Power | `sensor.external_p1_meter_power` | W, import positive; unavailable unless status is `live` |
| Status | `sensor.external_p1_meter_status` | `live` (telegram younger than 5 s), `stale`, or `unreachable` (eight failed polls in a row) |
| Telegram age | `sensor.external_p1_meter_telegram_age` | seconds since the last new telegram; diagnostic |
| Energy imported / exported | `sensor.external_p1_meter_energy_imported` / `_exported` | kWh, HomeWizard and YouLess only; usable in the Energy dashboard |

### Several external meters

Since v1.5.3 any number of external meters can be added — a YouLess, a HomeWizard and a Home
Assistant sensor side by side on one splitter, say. Each battery has an **External meter** select,
next to **Meter source**, listing them by type and address plus `none`:

| Situation | Meter used |
|---|---|
| One external meter, nothing chosen | that one, as in v1.5.2 |
| Several, nothing chosen | none, with a warning in the log, until one is chosen |
| One chosen | that one; `Meter source` still decides between it and the Zendure meter |
| `none` chosen | none: `auto` uses the Zendure meter, `external` stops the battery |
| The chosen meter is removed | as if nothing was chosen |

The select shows the meter the battery is actually linked to, so a choice that cannot be honoured
reads `none` rather than a wish. Switching takes effect on the next sample, without a restart.

The first external meter keeps the device name **External P1 meter** and its entity IDs
`sensor.external_p1_meter_*`. Later ones carry their type in the name — *External P1 meter (YouLess
LS120)*, entity IDs `sensor.external_p1_meter_youless_ls120_*` — so the dashboards keep showing the
first one and the others are easy to add.

### A Home Assistant sensor as external meter

**Add integration → Zendure RestAPI → Home Assistant sensor as external meter** turns any power
sensor already in Home Assistant into an external meter: a Zonneplan Connect, a DSMR reader, the
YouLess integration that ships with Home Assistant. Import is positive. Where import and export are
separate sensors, both positive, choose the export sensor too and it is subtracted. W, kW and MW are
accepted.

Whether such a meter can steer depends on how often its sensor is updated, not on this integration.
A reading counts as new when the sensor's `last_reported` moves — also when the value is unchanged —
and a reading older than 5 s is `stale`. A sensor refreshed every ten seconds is therefore stale most
of the time and `auto` uses the Zendure meter in between; the Zonneplan Connect, which reaches Home
Assistant through the Zonneplan cloud about once every five minutes, is for the dashboard only. A sensor that turns `unavailable` reads as `unreachable` after two seconds.

**Upgrading from v1.5.0 or v1.5.1**, where the reader was configured on the battery under
**Configure**: on the first start of v1.5.2 it moves into an entry of its own, with the same type,
address and interval, and the battery's options lose the three fields. Nothing needs doing; the
**Meter source** setting is kept.

### Readers

Endpoints and fields follow each maker's documentation. HomeWizard needs *Local API* switched on in the HomeWizard Energy app, EcoTracker its
local HTTP server switched on in its app.

**Telegram time matters for the interval.** A reader that reports when each telegram arrived — the
YouLess with `tm` — can be polled faster than the telegram without counting the same measurement
twice; only a moved `tm` is a sample. A reader without it cannot tell a new telegram from the same
one fetched again, so every poll is a reading and the integration holds it to one poll a second
whatever the interval setting says. HomeWizard's API v2 does report the telegram time, and offers a
websocket that pushes each new one; it needs a token and HTTPS and is planned for a later release.

Not affiliated with HomeWizard, YouLess, everHome or Shelly; these are compatibility notes.

### Why

Measured through a splitter on one P1 port, the YouLess and the Zendure meter are equally
accurate and refresh equally often, once per telegram. The YouLess sees every load step
**0.4 to 1.1 s earlier** (see [The delay](#the-delay)). The battery itself takes about five
seconds to respond, so this is a modest share of the total — but it is won on every edge, and
it is the part of the chain that costs nothing to shorten.

The YouLess integration that ships with Home Assistant refreshes every ten seconds, which would
throw that away nine times over. This integration therefore polls the reader itself, on
`/e?f=j`, four times a second by default. Four polls per one-second telegram is not wasteful:
it cuts the time between a telegram arriving and being seen from 0–1 s to 0–0.25 s. Only a new
telegram — a moved `tm` — counts as a sample, so the trim loop and the trace still run once a
second.

**Measured again with both meters read by this integration** (14 October 2026, one hour, one
sample per second, battery in `standby` so the controller did not move the grid; the grid ranged
from −860 to +1,757 W):

| | YouLess | Zendure P1 meter |
|---|---|---|
| Difference YouLess − Zendure | median 0 W, p5–p95 −20 to +24 W | — |
| Load steps above 250 W (11) | reference | **0.9 s later** on average (0 s 4×, 1 s 5×, 2 s 1×, 3 s 1×) |
| Same value as the second before | 5 % of seconds | **28 %** of seconds |
| Longest run of the same value | 3 s | 9 s; 4 s 123×, 5 s 19× |

Equally accurate, as through the splitter; the difference is timing. The Zendure meter repeats its
previous telegram in more than a quarter of the seconds, sometimes for four or five in a row, so a
loop reading it sees a load change about a second later on average and occasionally several.

### Choosing the meter

The **Meter source** select decides which meter feeds both loops:

| Source | Behaviour |
|---|---|
| `zendure` | The Zendure P1 meter only, as in every version before 1.5.0. |
| `external` | The external reader only. When it goes quiet the battery stops, exactly as with a silent Zendure meter. Use this for comparison runs, where a silent fallback would mix two meters in one block. |
| `auto` | **Default since v1.5.1.** The external reader while it delivers telegrams; the Zendure meter as soon as it has been silent for 5 s; back again on the next telegram. Without an external reader configured this is exactly `zendure`. |

The controller status carries `meter_source` (the setting) and `meter_active` (the meter in use
at this moment). Both meters stay subscribed whatever the setting, and the choice is made per
sample, so a fallback takes effect on the next Zendure sample rather than after a resubscription.
On every change of meter the trim loop's previous reading is dropped: the step between two meters
is their offset and their lag, not movement of the grid.

All the existing safeguards apply to either meter unchanged: a reading older than 60 s, missing,
non-numeric or beyond the connection's 17.25 kW stops the battery.

### Failure behaviour

A reader that does not answer is retried every 5 s after eight failed polls in a row, with one
warning in the log; it does not keep four requests a second going to an address that is not
there. A frozen `tm` counts as silence even when requests succeed — with the P1 cable pulled the
YouLess was seen to keep answering while `tm` stood still, once for 52 days. Since v1.5.1 the
first telegram after a start is not trusted either: a reader counts as live from the first `tm`
that moves.

### Adding a reader

Subclass `ExternalMeter` in `p1meter.py`, set `kind` and `path`, implement `parse()` returning
the telegram stamp, net power in W with import positive, and any extra fields; add the class to
`ADAPTERS` and a label to `EXTERNAL_TYPE_LABELS` in `const.py`. A reader without a telegram stamp
of its own returns `None` and keeps the default `min_interval_ms` of 1000; one with a stamp may
lower it.

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

If the meter stops reporting, both limits go to zero — a closed loop without feedback is worse than no loop.

At the device's own SOC bounds the smart modes respect the direction the device has closed: at `upper_limit` charging is not opened, at `lower_limit` discharging is not, and a direction already running is cleared once. The other direction stays open, as it does on the device. Before v1.5.4 the loop kept ordering into a battery that accepted nothing — at 100 % 59 trim writes in a quarter of an hour, at 5 % a discharge limit averaging 1,353 W with nothing flowing — and the status read "within 10 W". `quick` and `manual` keep their order, which is the operator's, and the status adds that the device refuses it. A missing or unreadable `socLimit` counts as no bound.

Below the lower SOC bound the battery is charged back regardless of strategy, except in `manual` and `quick_discharge` where the operator's intent is explicit; switchable off.

`smartMode` is set to RAM before writing limits, so frequent adjustments never reach flash. At a ten second interval that is tens of thousands of limit writes a year. `standby` hands it back on entry, because the device stays awake while it is set.

---

## Supported Entities

| Group | Count | Entities |
|---|---|---|
| **Sensor** | 50 | AC coupling status, AC state, Backup output power, Backup output power 2, Battery charge power, Battery discharge power, Battery module count, Battery state, DC bus voltage, DC state, Enclosure temperature, Fan level, Fan speed step, Fault level, Battery power, Grid input power, Home output power, PV input power, PV state, PV string 1–6, Remaining discharge time, SOC calibration status, SOC limit status, Signal strength, State of charge · *disabled by default:* Activation voltage, Bind state, Device timestamp, Factory mode state, Grid HD status, HV battery voltage (raw), IoT connection state, LCN state, Legacy mode, Message id, OTA state, Off-grid state, Phase switch, Powerhub status, Report timestamp, Slave address, Timezone, Timezone offset, Voltage wake-up, Write response |
| **Per battery** | 12 | Model, Current, Max cell voltage, Min cell voltage, Power, State, State of charge, Temperature, Voltage · *disabled by default:* Firmware version, Pack type, Serial number |
| **Energy** | 5 | Energy charged, Energy discharged, PV energy, Energy imported, Energy exported |
| **Efficiency** | 2 | Charge efficiency, Discharge efficiency |
| **Binary sensor** | 12 | Data ready, Dry contact, Error, Fan, Grid connected, Heating, Lamp, Pass-through, Reverse flow · *disabled by default:* HV battery control, PV-AC coupling, SOC compensation |
| **Number** | 7 | Charge limit (AC), Charge power limit, Discharge limit, Inverter power limit, Lower SOC limit, Upper SOC limit · *disabled by default:* Calibration interval |
| **Select** | 5 | Backup mode, Converter mode, PV export · *disabled by default:* Fan speed mode, Grid standard |
| **Switch** | 2 | Skip flash write · *disabled by default:* Fan forced on |
| **Controller** | 19 | Operation mode, Meter source, External meter, Controller status, Manual power, Direction change delay, Max charge power, Max discharge power, Min charge power, Min discharge power, Start discharging at import, Start charging at export, Charge buffer, Discharge buffer, Trim strength, SOC protection, Trace recording, Mode threshold, Trim threshold |
| **Price Control entry** | 8 | Price Control (switch), Status, Electricity price, Cheap price, Expensive price, Cheap price mode, Expensive price mode, In-between mode |
| **P1 meter entry** | 6 | Grid power total, Phase A/B/C apparent power, Meter type · *disabled by default:* Protocol type |
| **External P1 meter entry** | 3–5 | Power, Status, Telegram age · *HomeWizard and YouLess:* Energy imported, Energy exported. One entry per meter; a Home Assistant sensor gets the first three. |

Entities appear only when the device reports the underlying key. `PV string 3`–`6`, `Fan level` and `Fan` are documented in zenSDK but absent from the 3000 Mix AC+ payload, so they are never created on that model.

### SOC limit status

`socLimit` maps to three values: `none`, `upper_limit` and `lower_limit`. All three occur in
practice, and the sensor is early enough to act on.

| Transition | Behaviour |
|---|---|
| To `upper_limit` | one second before the device stopped accepting charge |
| To `lower_limit` | in the same sample as the meter, ten seconds before `Battery power` followed |

That ten-second lag is the battery poll interval, not the sensor: the meter and the flag change
together, the cell readings catch up on the next payload.

Over three days the sensor produced eight transitions — four to each limit, each with a return to
`none` in between, no chattering and no intermediate states.

Since v1.5.4 the smart modes act on it themselves; see [Safety](#safety).

**Treat `unavailable` separately from `none`.** The sensor spent ten seconds unavailable once in
those three days. Anything keying off this entity should distinguish "no limit active" from "no
value", or a brief gap will read as the limit having cleared.

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

The loss is flat across that range, so the proportional term cannot be read from it. Round-trip over the lifetime counters was 80.8%.

#### Separating the fixed and proportional parts

A later run isolated the two. Rather than integrating over hours — which mixes in the sampling offset between the AC and cell readings — it takes **paired values that each held still for at least five seconds**, so both columns describe the same moment. Thirty-three such pairs, `closed`:

| AC | DC | Loss | Efficiency |
|---|---|---|---|
| 31 W | 68 W | 37 W | 45.6% |
| 94 W | 133 W | 39 W | 70.7% |
| 131 W | 170 W | 39 W | 77.1% |
| 152 W | 190 W | 38 W | 80.0% |
| 170 W | 208 W | 38 W | 81.7% |
| 190 W | 227 W | 37 W | 83.7% |

Least squares over all thirty-three:

```
P_DC = 1.027 × P_AC + 32.9
```

**A fixed term of 33 W and a marginal conversion of 97.4%.** Stronger than the fit: the loss itself sits between 36 and 40 W across the whole range, median 38. There is no poor efficiency at low power — there is one fixed cost that dominates when little is flowing.

#### The charge side has its own characteristic

Three `quick_charge` windows at a fixed 3000 W limit, on two different days and across a state of charge from 26% to 99%:

| Duration | AC in | DC in | Loss | Efficiency |
|---|---|---|---|---|
| 24.6 min | 2999 W | 2856 W | 144 W | 95.2% |
| 14.3 min | 2999 W | 2853 W | 146 W | 95.1% |
| 18.6 min | 2980 W | 2837 W | 143 W | 95.2% |

The discharge fit above predicts about 100 W at this power; measured is 143 to 146 W, three times, with a spread of 3 W. **The discharge curve does not describe the charge side** — either the proportional term is larger there, or the fixed term is. Three points at one power level cannot separate the two.

### Usable capacity

The device publishes no capacity figure, so it is derived from charge windows: energy in against the change in state of charge.

| Window | Duration | SOC | AC in | Wh AC per point | Wh DC per point |
|---|---|---|---|---|---|
| 1 | 15.0 min | 22 → 30 | 750 Wh | 93.7 | 89.3 |
| 2 | 25.3 min | 30 → 39 | 764 Wh | 84.9 | 80.3 |
| 3 | 116.5 min | 39 → 99 | 5702 Wh | 95.0 | 90.4 |
| 4 | 352.8 min | 9 → 100 | 8436 Wh | 92.7 | 87.3 |

**Median 93.2 Wh AC per percentage point**, or 88.3 Wh on the cell side.

| | 3000 Mix AC+ with one 3840 Wh pack ×2 |
|---|---|
| Usable capacity, cell side | **8.8 kWh** |
| Energy needed at the AC port for 0 → 100% | **9.3 kWh** |

Which of the two you want depends on the question. Planning purchases in metered kilowatt-hours needs the AC figure; sizing what the battery can deliver needs the cell figure. Eleven further discharge windows gave 86 to 88 Wh per point on the cell side, consistent with the table.

Short windows are unreliable here: window 2 spans nine points, where rounding of the whole-percent scale weighs heavily. The two long windows, 60 and 91 points, are the ones to trust.

That is not a measurement error, and it cuts both ways: trickle-discharging is expensive, but so is leaving the converter awake with nothing to do. Whichever is worse depends on how long it sits idle — and on whether your tariff still nets import against export.

---

## zenSDK and this integration

[Zendure/zenSDK](https://github.com/Zendure/zenSDK) is the protocol source: the local HTTP API and
the property reference. Its property document was last changed on 17 February 2026, the
repository on 27 March 2026. Its product list names eight devices; the SolarFlow 3000 Mix AC+ —
the reference device here — and the 4000 Mix AC+, 800 Pro 2 and 4000 Mix Pro are not on it.
This integration covers those models as well, and corrects the document where live hardware
disagrees with it: see [Notes on correctness](#notes-on-correctness).

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

### Keeping the status in the history

Many setups keep the recorder small by excluding chatty sensors with a glob such as
`sensor.zendure_*`. That also drops the controller status — the one sensor that says *why* the
battery did what it did — and with it any way to look back at a night. Keep it in: an entity
listed under `include` wins over an `exclude` glob.

```yaml
# configuration.yaml
recorder:
  include:
    entities:
      - sensor.<device>_controller_status
  exclude:
    entity_globs:
      - sensor.zendure_*
```

The status changes only when the state or the reason changes, so it adds little to the database.
The four energy sensors are worth keeping for the same reason: without them the Energy dashboard
has no history for the battery.

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
state that signs it, state of charge and the SOC limit flag, both limits, `acMode`, `smartMode`,
the age of the battery payload, and the loop's own bookkeeping. Since v1.5.0 the row also names the
meter the reading came from (`meter_source`) and carries the external meter's raw power next to
the Zendure one (`external_w` beside `grid_raw_w`), so the lag between the two is measured
continuously rather than in one test. `grid_raw_w` is always the Zendure meter.

Having the AC and cell readings in the same row is what makes conversion loss measurable without
a second instrument — but the two are 5 to 10 seconds out of step, so only pairs that hold still
for several seconds give a usable ratio. `soc_limit` carries the raw 0, 1 or 2 rather than the
sensor's text, in keeping with the rest of the row. Nothing is averaged, scored or derived, because a definition of "how well did that
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

**At a state-of-charge limit the device drops to 5 W on its own, flag or no flag.** Over 58.9 hours
of trace, 116,534 samples with the battery delivering nothing:

| State | Samples | Cell draw |
|---|---|---|
| Limits zero, flag clear (resting) | 37,671 | 5 W |
| Limit set but device refusing — full or empty | 78,762 | **5 W** |
| Limits zero, flag still set | 101 | 30 W |

The 29 W figure from the table above is real but short-lived: it exists only in the window between
the controller releasing a direction and the rest state taking effect, which is ten seconds. Within
a zero-limit stretch the cell draw reads 0 W for the first ten seconds, 31 W for the next ten, and
5 W from twenty seconds on. **Idle cost is therefore 5 W in almost every idle situation**, not 29,
and a standing-loss estimate that multiplies 29 W by all awake time overstates it by a wide margin.

**And the device does not clear the flag by itself.** In `manual` with both limits at zero it
stayed clear for as long as nothing wrote to it. What sets it back is this controller: at the
start of every smart cycle, and again before any limit write in any mode — so a limit and a set
flag cannot be separated from Home Assistant. Reports elsewhere of the device reverting the flag
on its own were not reproducible here; what looked like that turned out to be the device's own
energy manager still running alongside.

**At a state-of-charge limit the controller keeps ordering, and reports success.** The device
refuses outright — not a taper, a flat zero — while the loop carries on writing a limit and
reports `discharge at 143 W, within 10 W` or `charging at maximum`, because it reads the limit
back rather than the power. Observed at both ends: 630 samples in `quick_charge` at 100% with an
ordered 3000 W and `battery_ac_w` exactly 0, and 4380 samples in `smart_discharge_only` at 9% with
an ordered 58–178 W and the same flat zero.

Nothing breaks, but two things follow. In a charge mode at the top the discharge limit stays at
zero, so the battery cannot cover a household peak while sitting full; at the bottom the mirror
applies to solar surplus. And the rest state never engages, because the limit is not zero. Watch
`SOC limit status` rather than the reported power if you need to know whether an order is landing.

**`acCouplingState` bit 15 is undocumented.** It is set on every sample from a 3000 Mix AC+ and
its meaning is unknown. Reported as-is.

**Efficiency is not constant.** The converter's overhead has a fixed part and a proportional one,
and the fixed part dominates. Treating it as a constant, in either direction, gives wrong answers
at the other end of the range. The fixed part also depends on Backup mode: around 27 W with
`closed`, around 41 W with `economic`, so a curve measured under one does not describe the other.
Discharging under `closed` it works out at `P_DC = 1.027 × P_AC + 32.9`; charging does not follow
that line and needs its own figures — see [Conversion efficiency](#conversion-efficiency).

**Efficiency cannot be read off a trace with transients in it.** `battery_ac_w` and `pack_dc_w`
are 5 to 10 seconds out of step, so on any edge the same division returns nonsense — in one
otherwise clean night, 43% and 104% on bands with 70 and 130 samples. Only long stretches at a
stable power level give a usable number, and the shape of the curve above 200 W is not
established under `closed`. That needs a run holding several fixed power levels for long enough
to average out the sampling offset between the AC and cell readings.

---

## Changelog

The current release is listed in full, with v1.5.4 — the last one to change the control loop — kept in full below it. Earlier ones are grouped by minor version, keeping the
findings and the fixes that changed behaviour and dropping the housekeeping.

### v1.5.6 — Entity names in eleven languages, a dashboard and price control

No change to the control loop.

- **Entity names are translated** — all 133 of them, in the same eleven languages as the setup
  screens: `Laadtoestand`, `Ladezustand`, `État de charge`. Battery packs read as `Accu 1
  temperatuur`, `Batterie 1 Temperatur`, with the number in its place for each language. The
  operation modes keep their technical names.
- **Entity IDs stay English**, whatever the language: built from the English name, so dashboards,
  documentation and automations match everywhere. Existing IDs are untouched. See
  [Languages](#languages).
- English names are exactly those of v1.5.5. A test checks that every entity has a name in every
  language, with the same placeholders, and that the English text has not moved.
- **The dashboard, created for you**: tick *Create the Zendure RestAPI dashboard* when adding the
  battery, or later under **Configure**. The dashboard **Zendure RestAPI** gets the entity IDs,
  gauge ranges and sections of your own devices; an existing one is never overwritten. See
  [Dashboard](#dashboard).
- **Price control**, dynamic trading built in, as a device of its own: **Add integration →
  Zendure RestAPI → Price control**, choose the battery and a price sensor, switch it on. A cheap
  and an expensive price and the mode for each band are entities of the device **Price
  Control**. Off by default. The simple version, on two fixed prices; a fuller one follows in
  v1.5.7. See [Price control](#price-control).
- The dashboard file is now `dashboard/zendure_restapi.yaml`, the whole dashboard in two views,
  *Zendure RestAPI* and *Price Control*; `dashboard/zendure.yaml` is gone.
- The options screen of a battery is titled *Options* now that it holds more than the polling
  interval.

### v1.5.5 — Documentation

No change to the integration: the code is the v1.5.4 code, only the version number moved.

- **[zenSDK and this integration](#zensdk-and-this-integration)**: what Zendure's SDK documents and
  lists, when it last changed, and which models and corrections this integration adds.
- **The two P1 meters measured again**, now both read by this integration: the YouLess sees a load
  step 0.9 s earlier on average, and the Zendure meter repeats its previous value in 28 % of the
  seconds. See [Why](#why).
- **[Keeping the status in the history](#keeping-the-status-in-the-history)**: a recorder exclude
  glob on `sensor.zendure_*` also drops the controller status; how to keep it.
- **The short READMEs** (English, Dutch, German) list more of what the integration does, each with
  the measurement behind it: standby draw, flash wear, a full battery left alone, efficiency
  sensors, any Home Assistant power sensor as meter, and replays before every release.
- **Validation runs no longer pile up**: a new push cancels a run still in progress for the same
  branch or tag.

Since then, documentation only and without a new version: a forum topic, a table of tested setups
and the key measurements in the short READMEs.

### v1.5.4 — The SOC bounds in the control loop

The last change to the control loop in the 1.5 series. One change to how the battery is steered, plus one line; everything else
in the loop is unchanged, and a replay of nine runs without a SOC bound gives identical writes.

- **The smart modes respect the device's SOC bounds.** At `upper_limit` charging is not opened, at
  `lower_limit` discharging is not; a running direction is cleared once; the other direction stays
  open. The trim loop does not adjust a direction the device has closed. Replayed over an hour with
  the device refusing one side: limit writes 309 → 130 (full, `smart_matching`), 177 → 0 (full,
  `smart_charge_only`), 437 → 76 (empty, `smart_matching`), 366 → 0 (empty,
  `smart_discharge_only`), and nothing ordered into the closed side any more. The grid is the same
  in every run, because the device refused that energy anyway.
- **The status says so**: `charging blocked: battery full (upper limit)` instead of `within 10 W`.
  In `quick` and `manual`, which keep their order, the reason adds `refused by device: battery full
  (upper limit)` or `… empty (lower limit)`.
- **`manual` at 0 W clears the direction record**, so the rest state can take effect there too.
- A test keeps the version in `const.py` and `manifest.json` the same.
- **README.de.md**, a German version of the short README.

### v1.5.3 — Several external meters, and any Home Assistant sensor as one

No change to the control loop: `controller.py` is the v1.5.2 one, and the replay of nine runs gives
identical writes.

- **Several external meters.** Each battery gets an **External meter** select that picks which one
  it uses. With one external meter nothing changes; with several and none chosen, none is used until
  one is. See [Several external meters](#several-external-meters).
- **A Home Assistant sensor as external meter**, for meters without an adapter here: Zonneplan,
  DSMR, the Home Assistant YouLess integration. Optional separate export sensor. See
  [A Home Assistant sensor as external meter](#a-home-assistant-sensor-as-external-meter).
- **Device names stay apart.** The first external meter is *External P1 meter*, later ones carry
  their type, so their entity IDs do not collide.
- **Dashboard rebuilt** after the author's own: a header row with the battery's state, an
  Operation mode section that says what the controller does and why, gauges for grid, state of
  charge, power and limits, and device control apart from the controller settings. The External
  meter select sits in the External P1 Meter section.
- Translations for the new screens and the select, in all eleven languages.

### v1.5.2 — The external meter as a device of its own, and eleven languages

No change to the control loop: the controller is byte-for-byte the v1.5.1 one, and the replay of
nine runs gives identical writes.

- **The external P1 meter is an entry of its own**, added through **Add integration → Zendure
  RestAPI → External P1 meter**, with a device and sensors: power, status (`live`, `stale`,
  `unreachable`), telegram age, and for HomeWizard and YouLess the energy registers. Fixed entity
  IDs `sensor.external_p1_meter_*`, whatever the reader. Before, it lived in the battery's options
  and was visible only in the controller status and the trace.
- **Migration.** A reader configured on the battery in v1.5.0 or v1.5.1 is moved into its own
  entry on the first start, with the same settings; the battery's options keep everything else.
- **HomeWizard P1 meters are discovered.** Other HomeWizard products on the same service are
  ignored. If the local API is off, the confirmation says where to switch it on.
- **The address is tried before saving.** A wrong address or a wrong reader type shows on the form
  instead of as a silent poller later.
- **Adding a device starts with a choice**: a Zendure device, or an external P1 meter.
- **Translations** for setup, options, errors and the states of selects and status sensors, in
  English, Dutch, German, French, Italian, Spanish, Portuguese, Polish, Czech, Swedish and Danish.
  Entity names and the operation modes stay English. See [Languages](#languages).
- **Dashboard.** Shows an **External P1 Meter** section when one is configured.
- **README.nl.md**, a Dutch version of the short README.
- **Minimum Home Assistant version 2025.2.0** in `hacs.json`, corrected from 2024.10.0: the
  discovery code has imported `ZeroconfServiceInfo` from `helpers.service_info` since v1.4.3, which
  exists from 2025.2 on.
- **Validation workflow** `.github/workflows/validate.yml`: the HACS action and hassfest on every push,
  pull request and daily, as HACS requires for its default list. Checked locally with hassfest from
  Home Assistant `master`: no errors.

### v1.5.1 — Works with HomeWizard, more models, and one battery steering at a time

No change to the control loop for a single battery.

- **Works with HomeWizard.** New external meter types: HomeWizard P1 meter (local API v1), EcoTracker, Shelly Pro 3EM, and the Zendure P1 meter or Smart Meter 3CT read directly. A Zendure P1 meter is no longer needed for the smart modes. Readers without a telegram time are polled once a second.
- **`Meter source` now defaults to `auto`.** Without an external meter configured that is exactly the old `zendure`, so existing installations are unchanged; with one configured it is used. Set `zendure` explicitly to keep the Zendure meter while an external one is configured. The meter type in the options form defaults to HomeWizard.
- **Model names** for the SolarFlow 800 Pro 2, 4000 Mix AC+ and 4000 Mix Pro, under both spellings the 4000 AC+ is known by.
- **Power entries run to 5,000 W** instead of 3,600, so the 4 kW and 5 kW Mix models are not cut off. The device's own ceiling still applies.
- **Battery pack model and capacity**, derived from the serial number and `packType`: one `Model` sensor per pack.
- **Supported devices** documented per model with a confidence level, in [Device support](#device-support).
- **New model report** issue template asking for the diagnostics download.
- `hacs.json` sets `hide_default_branch`, so HACS offers releases only and never an unreleased state of the main branch.
- **An external meter counts as live only once its telegram time moves.** Found on the author's installation: a YouLess whose P1 cable had been out for 52 days kept answering with the same `tm` and a fixed −7 W. The freshness test already ignored it after the first telegram, but that first one was accepted, so for the first seconds after a restart a weeks-old reading could steer. Readers without a telegram time are unaffected.
- **`external_w` in the trace is empty while the external meter has no current telegram**, by the same 5 s rule `auto` uses, instead of repeating the last value it ever saw.
- `product` and `version` are no longer reported as unknown properties in the diagnostics; they are identity fields.
- **A second battery in a smart mode now stands aside** with status `blocked`, instead of correcting the same deviation a second time. The first battery to enter a smart mode keeps steering until it leaves the smart modes.

### v1.5.0 — An external P1 meter as the grid reading

A second meter for both control loops; with the default setting nothing changes.

- **New: external P1 reader**, configured on the battery entry under **Configure** — type, IP
  address, poll interval (100–1000 ms, default 250). Supported: YouLess LS120. Polled directly on
  `/e?f=j`; a sample is a new telegram, not a new request. See
  [An external P1 meter](#an-external-p1-meter).
- **New: `Meter source` select** — `zendure` (default, unchanged behaviour), `external`, or `auto`
  with the Zendure meter as fallback after 5 s without a telegram.
- **Controller status** gains `meter_source` and `meter_active`.
- **The trace gains two columns**, `meter_source` and `external_w`, at the end: twenty-three in
  total. Reading by name needs no change, and the first twenty-one keep their positions.
- **Diagnostics** include the external meter's counters; its address is redacted along with the
  device address. Entry options are now redacted too.
- No change to the loops themselves: thresholds, bookkeeping, rest state and fail-safe are as in
  v1.4.3.
- **The README is split in two.** `README.md` is the short version with supported products and
  three-step installation, shown by HACS; this page, `README2.md`, is the full technical reference.

### v1.4.3 — Measurements added to the documentation, and `soc_limit` in the trace

One column added to the trace; the control loops are untouched.

- **`soc_limit` is now a trace column**, carrying the raw `socLimit` value — 0 none, 1 upper
  limit, 2 lower limit — next to `soc`. The value was already in the polled payload, so nothing
  extra is requested and the poll load is unchanged. It makes the relation between the flag and
  what the device actually accepts readable straight from the file instead of having to be
  reconstructed from Home Assistant history. **The header gains a twenty-first column:** anything
  reading the CSV by position needs adjusting, reading by name does not.

- **The fixed and proportional parts of the conversion loss are now separated.** Measured on
  thirty-three AC/DC pairs that each held still for at least five seconds, so both readings
  describe the same moment: `P_DC = 1.027 × P_AC + 32.9`. A fixed term of 33 W and a marginal
  conversion of 97.4%. The loss itself sits between 36 and 40 W across the whole 31–190 W range —
  there is no poor efficiency at low power, there is one fixed cost that dominates when little is
  flowing. See [Conversion efficiency](#conversion-efficiency).
- **The charge side does not follow that line.** Three `quick_charge` windows at 3000 W, on two
  days and across a state of charge from 26% to 99%, gave 143 to 146 W of loss where the discharge
  fit predicts about 100 W. Documented as its own characteristic rather than folded into one curve.
- **Usable capacity derived from charge windows:** 93.2 Wh at the AC port per percentage point,
  88.3 Wh on the cell side — 9.3 kWh to fill from empty, 8.8 kWh available from the cells. Both
  figures are given because the right one depends on the question.
- **Idle draw corrected.** At a state-of-charge limit the device drops to 5 W by itself, flag or no
  flag: 78,762 samples with a limit set and the device refusing. The 29 W figure is real but lives
  only in the ten seconds between the controller releasing a direction and the rest state taking
  effect — 101 samples in 58.9 hours. A standing-loss estimate that multiplies 29 W by all awake
  time overstates it by a wide margin.
- **`SOC limit status` documented.** All three values occur; `upper_limit` arrives one second
  before the device stops accepting charge, `lower_limit` in the same sample as the meter. Treat
  `unavailable` separately from `none`.
- **New known limitation:** at either state-of-charge limit the controller keeps ordering and
  reports success, because it reads the limit back rather than the power. Nothing breaks, but the
  opposite direction stays unavailable and the rest state never engages.

### v1.4.2 — Settings brought in line with what the measurements showed

- **`Start discharging at import` now defaults to 5 W instead of 30.** Two days of traces
  show the loop tracks a 5 W threshold without hunting: median error 3 W over a full
  night, 89% of the time within 10 W. The old 30 W was a guess made before any of that
  was measured, and it leaves the base load on the grid for no reason.
- **`Mode threshold` and `Trim threshold` now stop at 200 W instead of 500.** No measured
  configuration has ever needed more than 40 W, and a range that runs to 500 makes the
  useful part of the slider unusably small.
- **`Max charge power`, `Max discharge power` and `Manual power` now step in 100 W
  instead of 5 W.** These are ceilings and setpoints, not trim values; nobody sets a
  ceiling to 1,645 W, and a 5 W step over a 3,600 W range means dragging the slider
  through 720 positions.
- No behaviour changes. The control loop, the rest state and the trace writer are
  untouched.

### v1.4.1 — The rest state now actually enters

- **Fixed: the rest state could never fire.** It gated on `_owns_limits`, a
  flag meaning "this controller currently holds a nonzero limit" — which
  goes False on every path that winds a direction down, including the ones
  this controller performs itself, so it could never be True at the same
  moment both limits read zero. The idle test now uses `_last_direction`,
  which is already set to "none" on exactly the paths where no direction is
  running and nowhere else, and which three other branches in the same loop
  already rely on for the same question.
- **Found on trace, not by inspection.** A recording taken straight after
  the v1.4.0 restart showed a mode switch wind a discharge down through the
  forbidden-direction path — a fully legitimate, self-caused zero — after
  which `smartMode` sat at 1 for the next 745 seconds with both limits at
  zero, and no rest was ever entered. A second stretch of 288 seconds in the
  same recording did the same. Both are explained by the flag, not by
  anything about the hardware or the load.
- No other behaviour changes. `_owns_limits` is untouched and keeps its
  original meaning for standby bookkeeping.

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
