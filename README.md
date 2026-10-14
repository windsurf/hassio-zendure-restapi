# Zendure RestAPI – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)
[![100% local](https://img.shields.io/badge/100%25-local-brightgreen.svg)](#why-this-integration)
[![Works with HomeWizard](https://img.shields.io/badge/works%20with-HomeWizard%20P1-1e90ff.svg)](#-supported-meters)

🇳🇱 [Nederlandse versie](README.nl.md)

**Your Zendure SolarFlow battery in Home Assistant, 100% local, in three steps.**
No cloud account, no MQTT broker, no YAML packages to copy. Install it from HACS, add your battery, pick a mode — the battery holds your grid at zero, once a second.

**🔌 Works with HomeWizard.** Already have a HomeWizard P1 meter? That is all you need — no Zendure meter required. YouLess, EcoTracker, Shelly Pro 3EM and the Zendure meters work too.

![Zendure dashboard](images/dashboard.png)

*`smart_matching` holding the grid at zero: the house draws 175 W, the battery supplies it, the meter reads 0 W. This dashboard ships with the integration.*

**Quick start below** · full details in the [Technical reference](#-technical-reference)

---

## 📌 Supported products

| Device | Status |
|---|---|
| **SolarFlow 3000 Mix AC+** | ✅ Tested daily by the author |
| **SolarFlow 4000 Mix AC+** | ✅ Supported — same platform and battery as the 3000 Mix |
| **SolarFlow 2400 AC · 2400 AC+ · 1600 AC+** | ✅ Supported — same AC-coupled design |
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
| **Shelly Pro 3EM** (clamps on the grid connection) | 🧪 Documented local API, one reading per second |
| **Any power sensor in Home Assistant** (Zonneplan, DSMR, …) | 🧪 New in v1.5.3 — for the dashboard and comparison; steers only if it updates every second |

Set it up in one go: **Add integration → Zendure RestAPI → External P1 meter**, choose your meter, enter its IP address. A HomeWizard P1 meter shows up under *Discovered* by itself. It becomes a device of its own with power, status and energy sensors, and since the **Meter source** select is on `auto`, the battery uses it straight away.
HomeWizard: switch on *Local API* in the HomeWizard Energy app first.

*Not affiliated with HomeWizard, YouLess, everHome or Shelly; these are compatibility notes.*

---

## Why this integration

- **100% local.** Talks straight to the battery's own HTTP server on your network. Keeps working when the internet or the Zendure cloud does not.
- **Zero-export control, once a second.** A fast loop follows your meter every second; a slower one handles direction and safety. Built and tuned on measurements, not guesses.
- **Seven operation modes** — from hands-off `standby` to `smart_matching`, charge-only, discharge-only, quick charge/discharge and manual. Drive them from your own price automation.
- **Your own meter.** HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM or Zendure — read directly over your network, once a second, not through a slower Home Assistant sensor.
- **Safe by design.** No valid meter reading, no movement: the battery stops. Two batteries never fight over the same meter.
- **Low standby draw.** Hands the battery back to its low-power state when there is nothing to do.
- **Everything visible.** Ready-made dashboard, a controller status that says *why*, and an optional per-second CSV trace for the curious.
- **Speaks your language.** Setup screens and states in English, Nederlands, Deutsch, Français, Italiano, Español, Português, Polski, Čeština, Svenska and Dansk.

---

## 🚀 Get started

**1. Enable the local API** — under EN 18031 it is off by default.
In the Zendure app: add **HEMS**, then exit the app. Check it from any browser:
`http://<battery-ip>/properties/report` should show JSON.

**2. Install via HACS**

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=windsurf&repository=hassio-zendure-restapi&category=integration)

Or: HACS → three-dot menu → Custom repositories → `https://github.com/windsurf/hassio-zendure-restapi`, category **Integration**. Install **Zendure RestAPI** and restart Home Assistant.

**3. Add your devices**
Home Assistant usually finds the battery and the P1 meter by itself under **Settings → Devices & Services**. Otherwise add **Zendure RestAPI** and enter the IP address. Set **Operation mode** to `smart_matching` and watch the grid go to zero.

**Your meter:** a HomeWizard P1 meter is offered under *Discovered*; click **Add**. Others: **Add integration → Zendure RestAPI → External P1 meter**, choose YouLess, EcoTracker, Shelly or Zendure and enter the IP address. Done — `auto` picks it up. A Zendure P1 meter is found as a device of its own and needs nothing more.

---

## Operation modes

| Mode | What the battery does |
|---|---|
| `standby` | Nothing — stops once, then leaves the device alone |
| `smart_matching` | Charges from surplus and discharges for demand, holding the grid at zero |
| `smart_charge_only` | Only soaks up surplus, never discharges |
| `smart_discharge_only` | Only covers demand, never charges |
| `quick_charge` / `quick_discharge` | Full power, ignoring the meter |
| `manual` | Follows a fixed setpoint |

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
- [Changelog](https://github.com/windsurf/hassio-zendure-restapi/blob/main/README2.md#changelog)

**Latest release – v1.5.3:** several external meters side by side, with an *External meter* select on the battery to pick one; any power sensor in Home Assistant as external meter. **v1.5.2:** the external P1 meter as a device of its own with power, status and energy sensors; HomeWizard P1 meters discovered automatically; setup screens in eleven languages; external meter on the dashboard. Upgrading from v1.5.0/v1.5.1 moves your meter settings over by themselves.

## More

- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) — bugs, questions and [model reports](https://github.com/windsurf/hassio-zendure-restapi/issues/new?template=new_model.yml).
- ⭐ Useful? A star on this repository helps others find it.

---

> **Disclaimer:** Not affiliated with or endorsed by Zendure. Provided as-is, without warranty. Use at your own risk.

Licence: MIT — see [LICENSE](LICENSE).
