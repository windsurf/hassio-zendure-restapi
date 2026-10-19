# Zendure RestAPI – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)
[![100% local](https://img.shields.io/badge/100%25-local-brightgreen.svg)](#why-this-integration)
[![Works with HomeWizard](https://img.shields.io/badge/works%20with-HomeWizard%20P1-1e90ff.svg)](#-supported-meters)
[![Dynamic tariffs](https://img.shields.io/badge/dynamic-tariffs-f5a623.svg)](#-price-control)

🇳🇱 [Nederlandse versie](README.nl.md) · 🇩🇪 [Deutsche Version](README.de.md)

**Your Zendure SolarFlow battery in Home Assistant, 100% local, in three steps.**
No cloud account, no MQTT broker, no YAML packages to copy. Install it from HACS, add your battery, pick a mode — the battery holds your grid at zero, once a second.

**🔌 Works with HomeWizard.** Already have a HomeWizard P1 meter? That is all you need — no Zendure meter required. YouLess, EcoTracker, Shelly Pro 3EM and the Zendure meters work too.

**💶 Dynamic tariffs, built in.** On a dynamic contract? Price Control plans charging and discharging per quarter of an hour over the Nord Pool prices of today and tomorrow — import and export prices, conversion losses and wear included. Net metering (Dutch *salderen*) included: one switch while it lasts. No automation to write: switch it on.

![Zendure dashboard](images/dashboard.png)

*`smart_matching` holding the grid at zero: the house draws 79 W, the battery supplies it, and the controller keeps the meter within 10 W of zero. The integration creates this dashboard for you, with two more views: Price Control and Measured.*

**Quick start below** · full details in the [Technical reference](#-technical-reference)

---

## 📌 Supported products

| Device | Status |
|---|---|
| **SolarFlow 3000 Mix AC+** | ✅ Tested daily by the author |
| **SolarFlow 4000 Mix AC+** | ✅ Supported — same platform and battery as the 3000 Mix |
| **SolarFlow 2400 AC** | ✅ Tested by a user — read-out and `smart_matching` confirmed |
| **SolarFlow 2400 AC+ · 1600 AC+** | ✅ Supported — same AC-coupled design as the 2400 AC |
| **SolarFlow 800 · 800 Plus · 800 Pro · 800 Pro 2 · 2400 Pro** | 🧪 Works, smart modes experimental (solar panels on the device) |
| **SolarFlow 4000 Mix Pro** | 🧪 Works, smart modes experimental (solar panels on the device) |
| Hyper 2000 · Hub 1200/2000 · AIO 2400 · Ace 1500 | ❌ No local API on these models |

**🙋 Own a model marked ✅ or 🧪? You are exactly who this project needs.**
Install it, and if anything looks off — or if it all just works — open a
[New model report](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml)
with the diagnostics download attached. One report is enough to move a model from 🧪 to ✅.

---

## 🔌 Supported meters

The battery needs to know what your home draws from or returns to the grid. Use the meter you already have.

| Meter | Status |
|---|---|
| **HomeWizard P1 meter** (local API v1) | ✅ **Works with HomeWizard** — found automatically on your network; documented local API, read every second |
| **YouLess LS120** | ✅ Measured by the author — sees every load step up to a second earlier than the Zendure meter |
| **Zendure P1 meter** | ✅ Tested daily by the author |
| **Zendure Smart Meter 3CT** | 🧪 Same API as the Zendure P1 meter |
| **EcoTracker** (everHome) | 🧪 Documented local API, one reading per second |
| **Shelly Pro 3EM** (clamps on the grid connection) | ✅ Tested by a user as grid meter for the control loop, one reading per second |
| **Any power sensor in Home Assistant** (Zonneplan, DSMR, …) | 🧪 New in v1.5.3 — for the dashboard and comparison; steers only if it updates every second |

Set it up in one go: **Add integration → Zendure RestAPI → External P1 meter**, choose your meter, enter its IP address. A HomeWizard P1 meter shows up under *Discovered* by itself. It becomes a device of its own with power, status and energy sensors, and since the **Meter source** select is on `auto`, the battery uses it straight away.
HomeWizard: switch on *Local API* in the HomeWizard Energy app first.

*Not affiliated with HomeWizard, YouLess, everHome or Shelly; these are compatibility notes.*

---

## Why this integration

- **100% local.** Talks straight to the battery's own HTTP server on your network. Keeps working when the internet or the Zendure cloud does not.
- **Zero-export control, once a second.** A fast loop follows your meter every second; a slower one handles direction and safety. Built and tuned on measurements, not guesses.
- **Seven operation modes** — from hands-off `standby` to `smart_matching`, charge-only, discharge-only, quick charge/discharge and manual. Drive them by hand, from Price Control or from your own automation.
- **Dynamic tariffs, built in.** Price Control steers the battery on two fixed prices or on a day plan over the Nord Pool prices — see [Price control](#-price-control).
- **Settings are entities.** Every setting survives a restart and can be changed live from the dashboard or an automation. Updates through HACS leave your own configuration alone.
- **Your own meter.** HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM or Zendure — found on your network or added by IP, read directly once a second, not through a slower Home Assistant sensor. Or any power sensor Home Assistant already has.
- **Safe by design.** No valid meter reading, no movement: the battery stops. Two batteries never fight over the same meter; each has its own controller, meter choice and price control.
- **Low standby draw, measured.** Hands the battery back to its low-power state when there is nothing to do: about 5 W instead of 29–36 W (Backup mode `closed`), for every idle hour.
- **Spares the flash memory.** The smart modes write their limits to RAM, not flash — tens of thousands of writes a year that never wear the device.
- **Leaves a full battery alone.** At the device's own SOC bounds nothing is ordered into the closed direction: a whole night at 100 % took two limit writes, and the status says why.
- **Everything visible.** Ready-made dashboard, a controller status that says *why*, charge and discharge efficiency as sensors, and an optional per-second CSV trace for the curious.
- **Every change measured before release.** Changes to the control loop are replayed against recorded per-second runs first; the measurements and the corrections to Zendure's documentation are public in the technical reference.
- **Speaks your language.** Setup screens, entity names and states in English, Nederlands, Deutsch, Français, Italiano, Español, Português, Polski, Čeština, Svenska and Dansk.

---

## 🚀 Get started

**1. Enable the local API and switch Zendure's own control off** — under EN 18031 the API is off by default.
In the Zendure app:
- add **HEMS** — this switches the local API on;
- on the device page, switch **Add to HEMS** off — so Zendure's own control stays out of the way. Other ways and the app's power settings: [Before you start](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#before-you-start).

Then exit the app. Check it from any browser:
`http://<battery-ip>/properties/report` should show JSON.

**2. Install via HACS**

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

Or: HACS → three-dot menu → Custom repositories → `https://github.com/windsurf/hassio-zendure-restapi`, category **Integration**. Install **Zendure RestAPI** and restart Home Assistant.

[![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=zendure_restapi)

**3. Add your devices**
Home Assistant usually finds the battery and the P1 meter by itself under **Settings → Devices & Services**. Otherwise add **Zendure RestAPI**, choose **Zendure device** and enter the IP address. The dashboard **Zendure RestAPI** appears in your sidebar by itself; an existing one is left alone. Set **Operation mode** to `smart_matching` and watch the grid go to zero.

**Your meter:** a HomeWizard P1 meter is offered under *Discovered*; click **Add**. Others: **Add integration → Zendure RestAPI → External P1 meter**, choose YouLess, EcoTracker, Shelly or Zendure and enter the IP address. Done — `auto` picks it up. A Zendure P1 meter is found as a device of its own and needs nothing more.

---

## Operation modes

The mode names stay the same in every language, so automations and documentation can refer to them.

| Mode | What the battery does |
|---|---|
| `standby` | Nothing — stops once, then leaves the device alone |
| `smart_matching` | Charges from surplus and discharges for demand, holding the grid at zero |
| `smart_charge_only` | Only soaks up surplus, never discharges |
| `smart_discharge_only` | Only covers demand, never charges |
| `quick_charge` / `quick_discharge` | Full power, ignoring the meter |
| `manual` | Follows a fixed setpoint |

---

## 💶 Price control

![Price Control view of the dashboard](images/dashboard_price.png)

*The Price Control view: the day plan for today and tomorrow, which quarters charge and which discharge, and the expected margin. With net metering on, the export price follows the import price.*

Two ways to steer the battery on a dynamic tariff, in a device of its own:

- **Fixed prices** — at or below a **cheap price** the battery charges (from the grid, or from solar only), at or above an **expensive price** it discharges, in between it holds the grid at zero (the modes per band are yours to change). Any price sensor will do: Nord Pool, EnergyZero, Zonneplan, Tibber, Frank Energie or your own.
- **Day plan** (new in v1.6.0) — over the [Nord Pool](https://github.com/custom-components/nordpool) prices of today and tomorrow it works out per quarter of an hour whether to charge, discharge or neither, with separate import and export prices, conversion losses, wear and a minimum margin per kWh. By default it trades: charging and discharging at the battery's full power; the modes per band can make it cover the house only or charge from solar only. Ready for the end of Dutch net metering in 2027: switch *Net metering* off and the export price follows its own formula.

Replayed on 30 days of a real installation, the day plan earned **€ 1.58 a day** — as much as a full home-energy planner on the same days; the fixed prices earned € 0.21.

**Day plan — two prerequisites from HACS:** **Nord Pool** for the prices, **ApexCharts Card** for the chart.

1. Install **Nord Pool** ([open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=custom-components&repository=nordpool&category=integration)) and **ApexCharts Card** ([open in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=RomRider&repository=apexcharts-card&category=plugin)); restart Home Assistant and refresh the browser.
2. **Add integration → Nord Pool**: *Region* your price area (`NL` for the Netherlands), *Currency* `EUR`, *Include VAT* on, *Price in cents* off, *Energy scale* `kWh`, *Template for additional costs* left as it is.
3. Add Price Control (below) with that sensor, `sensor.nordpool_kwh_…`, and *The price sensor holds* on **Spot price incl. VAT**.
4. Set **Decision** to **Day plan**; in the Netherlands, switch **Net metering** on until 1 January 2027. Compare **Import price** with your supplier's price for this quarter; adjust *Import surcharge* if they differ.

Full walk-through, tariff settings for other contracts and countries: [Setting up Nord Pool](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#setting-up-nord-pool-for-the-day-plan).

**Add it:** **Add integration → Zendure RestAPI → Price control**, choose the battery and your price sensor. The device **Price Control** appears next to your battery; switch it on there. Every setting can be changed live, on that device or in the *Price Control* tab of the dashboard.

Off by default, and it writes the Operation mode only when its decision changes, so a mode set by hand holds. If an automation or planner of your own already steers the Operation mode, leave it off. Details in the [technical reference](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#price-control).

---

## 📏 Measured on the reference device

SolarFlow 3000 Mix AC+, one internal pack. Method and raw figures in the technical reference.

| | Measured |
|---|---|
| [Charge efficiency](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#conversion-efficiency) at 3 kW, AC → cells | **95.2 %** (three windows, 95.1–95.2 %) |
| [Discharge losses](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#conversion-efficiency) | a fixed ~33 W plus 97.4 % conversion — 95 %+ at high power, 81–84 % at 100–200 W |
| [Usable capacity](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#usable-capacity) | **8.8 kWh** at the cells; 9.3 kWh at the AC port for 0 → 100 % |
| [Idle draw](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#known-limitations) | 29–36 W while awake; about 5 W once `standby` lets the device sleep (Backup mode `closed`) |
| [Zendure P1 meter delay](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#the-delay) | 0.4–1.1 s behind a YouLess on the same port; repeats its previous value in 28 % of the seconds |

---

## 📖 Technical reference

Everything this page leaves out is in **[README2.md – Technical reference](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md)**:

- [Supported products](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#-supported-products) — confidence per model and what it rests on
- [Device support](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#device-support) — what differs between models, and why solar panels on the device are experimental
- [How the controller works](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#how-the-controller-works) — the two loops, the bookkeeping, the safeguards
- [External meters](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#an-external-p1-meter) — HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM, Zendure 3CT
- [Languages](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#languages) — eleven, corrections welcome
- [Supported entities](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#supported-entities) and [The Energy dashboard](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#the-energy-dashboard)
- [Debug logging and the sample trace](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#debug-logging)
- [zenSDK and this integration](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#zensdk-and-this-integration) — what Zendure's SDK lists, and what this integration adds and corrects
- [Changelog](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#changelog)

**Latest release – v1.6.3:** **measured diagnostics** — what the battery actually does, next to what its settings assume: charge and discharge efficiency per block, round trip efficiency per cycle, usable capacity, full cycles, standby draw, response time and grid tracking error, and the **realized margin** per day next to the expected one; a warning when the battery draws too much at rest. **v1.6.2:** a warning when something else also steers the battery (the Zendure app not on Custom Mode, or an automation of your own); price control keeps writing after a reload or a slow restart. **v1.6.1:** the three **modes per band** now drive the **day plan** as well, and it plans with them; **presets** that follow *Decision* and *Net metering*.

## More

- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) — bugs, questions and [model reports](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml).
- 💬 [Forum topic](https://community.home-assistant.io/t/zendure-restapi-local-zero-export-control-for-solarflow-batteries/1029076) — questions, setups and experiences.
- ⭐ Useful? A star on this repository helps others find it.

---

> **Disclaimer:** Not affiliated with or endorsed by Zendure. Provided as-is, without warranty. Use at your own risk.

Licence: MIT — see [LICENSE](LICENSE).
