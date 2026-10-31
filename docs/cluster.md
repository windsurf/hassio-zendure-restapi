# Battery cluster

The battery cluster lets **up to three Zendure batteries, one per phase**, hold the grid of the whole connection at zero together. Without a cluster nothing changes: one battery steers in a smart mode and a second one reports `blocked`, as before. The same holds while the cluster steers nothing — no battery taking part, or the group in standby or a quick mode.

This first version is deliberately simple:

- **One group, on the total of the meter.** Meters in the Netherlands, Belgium and most of Germany add up the three phases; that total is what the cluster holds at zero.
- **One battery per phase.** Three batteries on three phases, or two on two, or a single one.
- **Zendure batteries of this integration only.**

Control per phase, several batteries on one phase, limits per phase and other brands are not in this version (see [Limits of this version](#limits-of-this-version)).

## Contents

1. [What it does](#what-it-does)
2. [Before you start](#before-you-start)
3. [Setting up](#setting-up)
4. [How it regulates](#how-it-regulates)
5. [Entities](#entities)
6. [Reading the status](#reading-the-status)
7. [Faults and what happens](#faults-and-what-happens)
8. [Limits of this version](#limits-of-this-version)
9. [Testing your setup](#testing-your-setup)

## What it does

One battery is **primary**: it runs exactly the control loop a single battery runs, on the grid meter and with the buffers and start thresholds of the cluster. The other batteries are **secondary**: each holds a fixed limit in the primary's direction, set by the cluster at most every 30 seconds, and writes nothing else.

To the primary a secondary's step looks like a step in the house load — a kettle switching on or off — which its loop handles every day. That is the point of this design: the loop that was measured and tuned on one battery over two months is not changed, and a slow battery never slows the group down, because only the primary follows the meter second by second.

When the house asks little, the primary works alone. Every inverter costs a fixed ~40 W the moment it converts, so two batteries at a few hundred watts each throw that away twice.

## Before you start

1. **Every battery as an ordinary battery first.** Add each one with *Zendure battery — start here*, as for a single battery, and check that it reads correctly.
2. **Zendure's own steering off** on every battery, as described in README2 under *Before you start*.
3. **A grid meter for the whole connection**: the Zendure P1 meter, an external meter over IP, or a meter from Home Assistant. The cluster steers on one meter for all batteries.
4. **No second steering.** Not Zendure's own integration, not the HEMS of a Zendure PowerHub, not an automation that sets the Operation mode of the batteries. The cluster sets it.

## Setting up

*Settings → Devices & services → Add integration → Zendure RestAPI → Battery cluster.*

```
Zendure SolarFlow 2400 AC+ (…3F7A)   [ L1 ▾ ]
Zendure SolarFlow 2400 AC+ (…9B12)   [ L2 ▾ ]
Zendure SolarFlow 800 Plus (…C044)   [ L3 ▾ ]
```

Give each battery that takes part the phase it is connected to, or *not taking part*. Two batteries on one phase are refused in this version. The serial number tail tells two of the same model apart.

After *Submit*:

- the **Battery cluster** appears under the integration, with one **sub-entry per battery**;
- buffers, start thresholds and meter source are **taken from the first battery** chosen, so a tuned installation keeps its settings;
- the group starts in **standby**, and every battery's own Operation mode is set to standby.

Later, under the cluster in *Devices & services*:

| Action | What it does |
|---|---|
| **Add battery** | a battery that is not in the group yet, on a free phase, with Priority and Max share |
| **Reconfigure** (on a battery) | phase, Priority, Max share |
| **Delete** (on a battery) | the battery leaves the group and stays in standby until you choose a mode |
| **Configure** (on the cluster) | which external meter the group uses |

**Priority** (1–9, default 5) decides which battery is primary. Among equals the fastest measured battery is primary (*Response time* in the Measured tab), then the one with the most room. **Max share** caps what a battery takes on in the group, in both directions; 0 is its own maximum.

Removing the cluster returns every battery to its own settings, in standby.

## How it regulates

**Primary.** Runs the single-battery loop: mode loop on every battery poll, trim loop on every meter sample, the hold after a direction change, the SOC bounds and the rest state, all unchanged.

**Secondaries.** Every *Redistribute every* (30 s), or sooner when needed, the cluster works out what the group should deliver — what the batteries deliver now plus what the grid still asks — and splits it:

- below **Concentrate below** (300 W) the secondaries stop and the primary works alone; they come back once the demand is above it again, and stop again below 80 % of it;
- above it the secondaries take the demand minus 60 % of the primary's maximum, split over them by room, so the primary keeps margin to trim both ways;
- limits are rounded to 50 W, and a secondary is only written again when its limit moves by 100 W or more.

**Sooner than 30 s.** When the primary has been at its maximum for 10 s while a secondary has room, or at nothing while secondaries still run, the cluster splits at once — but never within 15 s of the last split, because a secondary takes 6 to 15 s to start from standstill.

**Load drop.** When the house load falls away while secondaries run — the oven switches off — the secondaries deliver more than the house takes. The primary goes to nothing and would like to turn. The cluster lowers the secondaries at once to what the house still takes, without waiting for the 10 or 15 seconds above, and the group keeps running in the same direction. A still-standing primary is given no share of the demand at such a split: moving load onto it would mean a cold start of 6 to 15 s with the grid uncovered. It takes its share again once it runs.

**Direction change.** All batteries in the group always go the same way. Only when the group has nothing left to do in the running direction — the secondaries' share would be about zero — and the primary wants to turn, do the secondaries stop (`reversing`), one request each, and the primary turns with its own hold.

**Quick modes.** In `quick_charge` and `quick_discharge` every battery runs at its own maximum; there is no primary.

**A secondary at its SOC bound** stops at once; its share goes to the others at the next split.

**SOC protection in the group.** On a single battery a pack below its own floor (*Minimum SoC*) is charged from the grid until it is above it again. In the group the cluster decides instead: a battery below its floor does not discharge and is not primary in a discharge, but it is not charged from the grid on its own either — that would be one battery charging while the others discharge, on the same connection. It charges with the group when there is surplus. **A primary that cannot serve** the direction needed — full while there is surplus, empty while the house asks — hands over to a battery that can.

## Entities

**Battery cluster** (one device):

| Entity | Kind | |
|---|---|---|
| Operation mode | select | standby, smart_matching, smart_charge_only, smart_discharge_only, quick_charge, quick_discharge |
| Charge buffer / Discharge buffer | number | as on a battery, now for the group |
| Start discharging at import / Start charging at export | number | as on a battery |
| Concentrate below | number | 0–1000 W, default 300 |
| Redistribute every | number | 10–120 s, default 30 |
| Meter source | select | as on a battery |
| Status | sensor | see below; attributes: primary, demand, `grid` (the reading the group steers on), role, direction and limit per battery |
| Group power | sensor | the AC power of the batteries together, positive while discharging |
| Group state of charge | sensor | weighted by capacity; attribute `spread` (highest minus lowest) |
| Batteries taking part | sensor | how many are available; attributes per battery |

The grid reading itself has no sensor of its own here, as on a battery since v1.6.6: use the sensor of the meter, which keeps its history. Like the energy counters since v1.6.7, the cluster's figures are written only when they move: *Status* when the status, the primary or a battery's role or limit changes, *Group power* per 50 W, *Group state of charge* per 0.5 %, and each at most once a minute otherwise. The decision itself is taken every 2 seconds.

**On each battery in the group:**

- *Operation mode* shows `coordinated` and refuses a choice: set the mode on the cluster.
- *Controller status* shows `secondary` on a secondary; its attributes carry `coordinated_by`, `role` and, for a secondary, `follow_limit`.
- **Take part** (switch): off takes the battery out of the group for a while, in standby, without deleting its sub-entry — for testing one battery on its own.
- Meter source, External meter, buffers and start thresholds of the battery are not used while it takes part; their values are kept.
- A Price Control entry of a battery in the group writes nothing and shows `paused`.

The dashboard of this version has no cluster tab yet. A card to paste:

```yaml
type: entities
title: Battery cluster
entities:
  - select.battery_cluster_operation_mode
  - sensor.battery_cluster_status
  - sensor.zendure_p1_meter_3_phase_grid_power_total   # or the Power sensor of your external meter
  - sensor.battery_cluster_group_power
  - sensor.battery_cluster_group_state_of_charge
  - sensor.battery_cluster_batteries_taking_part
  - number.battery_cluster_concentrate_below
```

## Reading the status

| Status | Meaning |
|---|---|
| `standby` | the group's Operation mode is standby; every battery stopped once, then hands off |
| `concentrated` | the primary works alone: the demand is below *Concentrate below* |
| `regulating` | the primary steers, the secondaries hold their share |
| `reversing` | the secondaries stop because the primary is about to turn |
| `quick` | every battery at its maximum |
| `meter_stale` | no valid grid reading: secondaries to 0, the primary stops as a single battery would |
| `idle` | no battery available; the attribute `reason` reads `no battery taking part` when none takes part (all *Take part* off, or no sub-entry). A battery outside the group then steers on its own |

## Faults and what happens

| Situation | Behaviour |
|---|---|
| a battery does not report | it drops out of the split and goes to standby; its share goes to the others |
| the primary drops out | the next battery becomes primary at the next decision (every 2 s); the secondaries stop first |
| no valid grid reading | secondaries to 0, the primary stops |
| *Take part* off | the battery goes to standby and is left out, also when it had just started |
| no battery takes part | the cluster steers nothing; a battery outside the group in a smart mode steers on its own |
| a battery below its own floor | no discharge, no grid charging against the group; charges with the group's surplus |
| another controller writes as well | the *Second controller* warning per battery, as in v1.6.2 |

## Limits of this version

| In 1.7.0 | Later, in a separate product |
|---|---|
| the total of the connection | control per phase, and the total and single phases side by side |
| one battery per phase, up to three | several batteries on one phase |
| phase entered by hand | phase detection, phase values from the meters |
| one set of settings for the group | settings per phase group |
| split by room | split by SOC or by priority |
| Zendure batteries of this integration | other brands |
| no limits per phase | import/export limit per phase, main fuse, total output cap |
| Price Control pauses for batteries in the group | Price Control and day plan for the group |
| no dashboard tab | a *Cluster* tab |

## Testing your setup

1. Each battery on its own first, the others in standby: an hour in `smart_matching` with *Trace recording* on. The Measured tab gives response time and lost writes per battery, which decide who is primary.
2. Then the cluster in `smart_matching` for a day, with trace recording on every battery. Share the traces and the cluster's *Status* history.
3. A day with low demand, with *Concentrate below* at 300 W and at 0, to see what concentrating saves.
4. With a battery that has panels of its own (800 Plus, 800 Pro, 2400 Pro): one sunny afternoon as a secondary at 0 W — does it still send PV to the grid?

---

Part of [Zendure RestAPI for Home Assistant](https://github.com/windsurf/hassio-zendure-restapi), licensed under the [Apache License 2.0](../LICENSE) since v1.7.0 (MIT for v1.6.x and earlier); see [NOTICE](../NOTICE). Zendure and SolarFlow are trademarks of their respective owners; this project is not affiliated with or endorsed by Zendure.
