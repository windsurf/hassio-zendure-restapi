# Zendure RestAPI – Home Assistant Integration

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/windsurf/hassio-zendure-restapi.svg)](https://github.com/windsurf/hassio-zendure-restapi/releases)
[![100% local](https://img.shields.io/badge/100%25-local-brightgreen.svg)](#why-this-integration)

**Your Zendure SolarFlow battery in Home Assistant, 100% local, in three steps.**
No cloud account, no MQTT broker, no YAML packages to copy. Install it from HACS, add your battery, pick a mode — the battery holds your grid at zero, once a second.

![Zendure dashboard](images/dashboard.png)

*`smart_matching` holding the grid at zero: the house draws 175 W, the battery supplies it, the meter reads 0 W. This dashboard ships with the integration.*

---

## 📌 Supported products

| Device | Status |
|---|---|
| **SolarFlow 3000 Mix AC+** | ✅ Tested daily by the author |
| **SolarFlow 4000 Mix AC+** | ✅ Supported — same platform and battery as the 3000 Mix (power settings up to 3.6 kW) |
| **SolarFlow 2400 AC · 2400 AC+ · 1600 AC+** | ✅ Supported — same AC-coupled design |
| **SolarFlow 800 · 800 Plus · 800 Pro · 800 Pro 2 · 2400 Pro** | 🧪 Works, smart modes experimental (solar panels on the device) |
| **SolarFlow 4000 Mix Pro** | 🧪 Works, smart modes experimental (solar panels on the device) |
| **Zendure P1 meter** | ✅ Tested daily by the author |
| **YouLess LS120** (as grid meter) | 🆕 New in v1.5 — measured 0.4–1.1 s faster than the Zendure meter |
| Hyper 2000 · Hub 1200/2000 · AIO 2400 · Ace 1500 | ❌ No local API on these models |

**🙋 Own a model marked ✅ or 🧪? You are exactly who this project needs.**
Install it, and if anything looks off — or if it all just works — open an
[issue](https://github.com/windsurf/hassio-zendure-restapi/issues/new) with your model and the
diagnostics download attached. One report is enough to move a model from 🧪 to ✅.

---

## Why this integration

- **100% local.** Talks straight to the battery's own HTTP server on your network. Keeps working when the internet or the Zendure cloud does not.
- **Zero-export control, once a second.** A fast loop follows your meter every second; a slower one handles direction and safety. Built and tuned on measurements, not guesses.
- **Seven operation modes** — from hands-off `standby` to `smart_matching`, charge-only, discharge-only, quick charge/discharge and manual. Drive them from your own price automation.
- **Faster meter, if you have one.** Use a YouLess LS120 instead of the Zendure P1 meter and every load step is seen up to a second earlier.
- **Safe by design.** No valid meter reading, no movement: the battery stops. The smart modes are built for one battery per grid connection.
- **Low standby draw.** Hands the battery back to its low-power state when there is nothing to do.
- **Everything visible.** Ready-made dashboard, a controller status that says *why*, and an optional per-second CSV trace for the curious.

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

*Optional:* a YouLess LS120 as faster grid meter — battery → **Configure** → enter its IP address, then set **Meter source** to `auto`.

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

## More

- 📖 **[Technical reference](README2.md)** — how the control loop works, measurements, every entity, the trace, the Energy dashboard, the full changelog.
- 🐞 [Issues](https://github.com/windsurf/hassio-zendure-restapi/issues) — bugs, questions and model reports.
- ⭐ Useful? A star on this repository helps others find it.

**Latest release – v1.5.0:** an external P1 meter as the grid reading — a YouLess LS120 sees every load step up to a second earlier. [Full changelog](README2.md#changelog).

---

> **Disclaimer:** Not affiliated with or endorsed by Zendure. Provided as-is, without warranty. Use at your own risk.

Licence: MIT — see [LICENSE](LICENSE).
