# Virtual cluster

A **Virtual cluster** shows how a cluster of batteries would regulate in your house, before you buy a second one. It makes up to three copies of one of your batteries, groups them in a cluster of their own and lets them steer on a **virtual P1 meter** fed by your real house load.

**Nothing happens for real.** The Virtual cluster writes nothing to your battery, does not claim the grid, and your battery keeps doing exactly what it does now — with its own Operation mode, its own automations, its own meter. The test costs computing power and a little database, nothing else.

```
real (unchanged)                       virtual (writes nothing real)
P1 meter ──┐                           virtual P1 = house load × Load factor − copies
           ├─► house load ────────────►        │
battery ───┘   (P1 + battery power)     Virtual cluster
                                        ├─ Virtual Mix 1  twin, real SoC at the start
                                        ├─ Virtual Mix 2  twin SoC + offset
                                        └─ Virtual Mix 3  twin SoC + offset
```

## Contents

1. [How a copy works](#how-a-copy-works)
2. [Setting up](#setting-up)
3. [Settings](#settings)
4. [Entities](#entities)
5. [Dashboard](#dashboard)
6. [Using it](#using-it)
7. [What it does not test](#what-it-does-not-test)

## How a copy works

Each copy is this integration's own controller — mode loop, trim loop, hold after a direction change, SOC bounds, measured diagnostics — on a model of the device instead of the network. It starts with your battery's settings: maxima, minimum and maximum SOC, buffers, thresholds, trim strength, everything you tuned.

The model of the device copies your battery's report once — product, firmware, every static value — and moves the rest itself, as a SolarFlow 3000 Mix AC+ was measured to:

| | |
|---|---|
| A written limit is in the device after | 1.8 s |
| From standstill, the first power after a write | ~8 s |
| Power rises at | 200 W/s |
| A stop is complete after | ~3 s |
| Conversion, both ways | 95 % |
| Draw at rest | 29 W with smartMode 1, 5 W with 0 |
| Capacity | the usable capacity measured on your battery, else the nominal pack capacity |

**The virtual P1 meter** is a device of its own, *Virtual P1 meter*, under the Virtual cluster:

- **House load** = real grid + AC power of your battery.
- **Power** = house load × *Load factor* − what the copies deliver. This is what the copies steer on.

It is computed on every sample your battery's own trim loop gets, so it is as fast as your real meter. Your battery's power comes from its last poll: for up to one poll after your battery changes its power, the house load is off by that change.

## Setting up

*Settings → Devices & services → Add integration → Zendure RestAPI → Virtual cluster (test).*

Choose the battery to copy. That is all: the Virtual cluster appears with three copies, follows your battery's Operation mode, and the dashboard **Virtual Cluster** is made.

One Virtual cluster at a time. It can exist next to a real battery cluster. Delete it like any entry; its devices and entities go with it, and the dashboard *Virtual Cluster* when no cluster is left.

**Your battery steered by something else.** When this integration only monitors your battery — Operation mode `standby`, and your own automation or a HEMS sets the limits — `follow_source` runs the copies in standby too. Choose a mode for the Virtual cluster yourself, `smart_matching` for instance: the copies then regulate on the virtual P1 while your battery carries on as before.

## Settings

All on the device *Virtual cluster*:

| Entity | Values | Default | |
|---|---|---|---|
| Operation mode | the cluster's modes, and `follow_source` | `follow_source` | `follow_source` runs the mode your battery runs, so also what your planner or Price Control chose; `manual` on your battery means standby here |
| Clones | 1–3 | 3 | another number sets the cluster up again |
| Load factor | 1–3, step 0.5 | 1 | 1 is your house; 3 gives each copy about what your battery gets now |
| SoC offset clone 2 / clone 3 | −50 … +50 % | −20 / +15 | against copy 1; applied at the start and at *Reset* |
| Response offset clone 2 / clone 3 | 0–10 s, step 0.5 | 1 / 3 s | a write is in that copy this much later than the measured 1.8 s, as another unit or firmware might answer; applies at once; copy 1 stays the twin |
| Reset | button | | every copy still, both limits 0, copy 1 on your battery's state of charge of that moment |
| Charge/Discharge buffer, Start discharging at import, Start charging at export | as on a battery | your battery's | |
| Concentrate below, Redistribute every | as on the cluster | 300 W, 30 s | |
| Rebuild dashboard | button | | builds *Virtual Cluster* again |

## Entities

**Virtual cluster:** the settings above, and Status, Group power, Group state of charge, Batteries taking part — as on the [cluster](cluster.md#entities).

**Virtual P1 meter** (model marked *SIMULATED*):

| Entity | |
|---|---|
| Power | the virtual P1: house load × Load factor − copies; attributes `source` and `telegrams` |
| House load | real grid + AC power of your battery |
| Real grid | your real P1 meter, as the copies' meter samples it |
| Load factor | the factor in use, set on the Virtual cluster |

**Each copy** (*Virtual Mix 1/2/3*, model marked *SIMULATED*): State of charge, Battery power, Controller status (with its role and limit), Charge limit, Discharge limit, Response time. No energy counters, so the Energy dashboard never sees a battery that does not exist, and no Price Control.

## Dashboard

**Virtual Cluster** (`/zendure-cluster`). The tabs carry their names: *Cluster*, *Virtual Mix 1*, *Virtual Mix 2*, *Virtual Mix 3*. Each copy is called by its device name throughout; the settings *SoC offset clone 2* and *Response offset clone 2* belong to *Virtual Mix 2*, those of clone 3 to *Virtual Mix 3*.

- **Status:** mode, status with its reason, primary and demand, and *Grid at last distribution*: the grid reading the last distribution was decided on, once per *Redistribute every*; the virtual P1, the house load and the real P1 next to it follow every telegram, so the two can differ for up to that interval.
- **Virtual P1 meter:** power, house load, real grid and load factor.
- **Distribution:** a row per copy with role, direction and limit; power and state of charge per copy and of your battery (*Real*).
- **Settings** of the Virtual cluster, the offsets named after the copy they belong to.
- **Last 24 hours:** the virtual P1, the real P1, the group and every battery; the state of charge of every battery.
- **A tab per copy:** headed *Virtual Mix n (simulated)*, with its state of charge, power, controller status, from the second copy on its SoC offset and extra response time, and its response time; its status card; and four gauges: the virtual P1 it steers on, its state of charge, its charge limit and its discharge limit.

Your own battery gets no tab here: it is no member of the Virtual cluster and keeps its own dashboard. Its power and state of charge show on the *Cluster* tab, for comparison.

Made once. After another number of clones a notification offers *Rebuild dashboard*; it replaces the dashboard, edits included. It goes when the last cluster or Virtual cluster is deleted.

## Using it

1. **Model check first.** Clones 1, Load factor 1, `follow_source`. Copy 1 now does what your battery does: their power should run alongside on the Cluster tab. The difference is how well the model matches your battery — look at it before trusting a cluster result.
2. **Your house.** Clones 3, Load factor 1. With most house loads below one battery's maximum, the primary works alone most of the time; the secondaries join at peaks and for large solar surpluses. This shows concentrating, the quick join, and what a second or third battery would add.
3. **The split under load.** Clones 3, Load factor 3: each copy gets about what your battery gets now. Splits, redistributions, direction changes and load drops with running secondaries come up all the time.
4. **State of charge.** Large offsets, for example −40 and +35: a secondary that is empty or full early.
5. *Reset* between runs, so a run can be repeated.

The day of a test is not a different day for your battery: its energy, its logger and its daily result are what they would have been.

## What it does not test

- A real inverter holding a fixed limit as a secondary.
- Real writes to a second device, and their losses.
- Real communication faults.

That is for a test with real batteries — see [Testing your setup](cluster.md#testing-your-setup).

---

Part of [Zendure RestAPI for Home Assistant](https://github.com/windsurf/hassio-zendure-restapi), licensed under the [Apache License 2.0](../LICENSE) since v1.7.0 (MIT for v1.6.x and earlier); see [NOTICE](../NOTICE). Zendure and SolarFlow are trademarks of their respective owners; this project is not affiliated with or endorsed by Zendure.
