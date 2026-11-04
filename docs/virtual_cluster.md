# Virtual cluster

A **Virtual cluster** shows how a cluster of batteries would regulate in your house, before you buy a second one. It makes up to three copies of one of your batteries, groups them in a cluster of their own and lets them steer on a **virtual P1 meter** fed by your real house load.

**Nothing happens for real.** The Virtual cluster writes nothing to your battery, does not claim the grid, and your battery keeps doing exactly what it does now — with its own Operation mode, its own automations, its own meter. The test costs computing power and a little database, nothing else.

```
real (unchanged)                       virtual (writes nothing real)
P1 meter ──┐                           virtual P1 = house load × Load factor − copies
           ├─► house load ────────────►        │
battery ───┘   (P1 + battery power)     Virtual cluster
                                        ├─ Virtual Mix 1  twin (offsets 0 by default)
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
| From standstill, full power after the first | ~2 s (1500 W/s; since v1.7.2, before 200 W/s) |
| A running battery changes its power at | 200 W/s |
| A stop is complete after | ~3 s |
| Conversion, both ways | 95 % |
| Draw at rest | 29 W with smartMode 1, 5 W with 0 |
| Energy per percent, discharging | the usable capacity measured on your battery over the range it was measured on (e.g. 7.97 kWh over 100 → 5 %: 84 Wh) |
| Energy per percent, charging | that divided by the measured round trip efficiency of the cells (0.96: 87.5 Wh) |
| Without measurements | the nominal pack capacity over 100 %, both ways |

Since v1.7.2 the copies follow your battery's state of charge in step. Measured with `follow_source`: before, a copy ran 8 % fast charging and 4 % fast discharging, so it was full about 13 minutes early over a whole charge; a copy also reached full power in a quick mode some 15 s after your battery did. *Virtual cluster start* in the log and the diagnostics show which energy per percent the copies use (`measured` or `nominal`).

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
| SoC offset clone 1 / 2 / 3 | −50 … +50 % | 0 / −20 / +15 | applied at the start and at *Reset* only; only the differences count, since the set is centred on your battery (v1.7.2, see [Where the copies start](#where-the-copies-start)); copy 1 since v1.7.2 |
| Response offset clone 1 / 2 / 3 | 0–10 s, step 0.5 | 0 / 1 / 3 s | a write is in that copy this much later than the measured 1.8 s, as another unit or firmware might answer; applies at once; copy 1 since v1.7.2. With both offsets of copy 1 at 0 it is the twin of your battery |
| Max share clone 1 / 2 / 3 | 0–5000 W, step 50 | 0 | the most that copy gives or takes in the group, as *Max share* on a battery under a real cluster; 0 is its own maximum; applies at once, in every mode — also quick and `follow_source`, so leave it at 0 to compare a copy with your battery (v1.7.2) |
| Reset | button | | every copy still, both limits 0, the copies started again around your battery's state of charge of that moment |
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

## Where the copies start

At the start of the Virtual cluster (adding it, a restart of Home Assistant, a reload) and at *Reset* — and only then — every copy gets a state of charge of its own (v1.7.2):

1. **Centred on your battery.** Each copy gets your battery's SoC plus its own offset minus the mean offset (copy 1 at 0 by default). With one copy its offset centres away: it starts on your battery's SoC. The copies then average your battery: *Group state of charge* starts on it.
2. **Shifted into 5–100 %.** When that leaves a copy below 5 % or above 100 %, the whole set moves until it fits, so the differences between the copies stay the offsets you set.
3. **Cut only when the set is wider than 95 points** (offsets −50 and +50): the lowest starts on 5 %, the rest at most 100 %.

With offsets 0 / −20 / +15:

| Your battery | Virtual Mix 1 / 2 / 3 | Average |
|---|---|---|
| 46 % | 47.7 / 27.7 / 62.7 | 46.0 |
| 4 % | 25 / 5 / 40 (shifted +19.3) | 23.3 |
| 95 % | 85 / 65 / 100 (shifted −11.7) | 83.3 |

With one copy, or offsets that add up to 0, Virtual Mix 1 starts on your battery's SoC. After a shift it does not: compare it with your battery on the change in state of charge, not on the level. For such a comparison, press *Reset* while your battery is in the middle (with these offsets between 23 and 83 %), where nothing shifts.

What happened is on the entities: the cluster status carries `start_soc_real`, `start_centre` and `start_shift`, each copy's *State of charge* `start_offset_vs_real`. A changed offset counts from the next start or *Reset*.

## Dashboard

**Virtual Cluster** (`/zendure-cluster`). The tabs carry their names: *Cluster*, *Virtual Mix 1*, *Virtual Mix 2*, *Virtual Mix 3*. Each copy is called by its device name throughout; the settings *SoC offset clone 2* and *Response offset clone 2* belong to *Virtual Mix 2*, those of clone 3 to *Virtual Mix 3*.


Since v1.7.2 in the layout of the battery dashboard: sections with a heading and badges, a slider for every number you can set, gauges for what is measured, and ApexCharts for the chart. What you set for the group is on the *Cluster* tab, what you set per copy on that copy's tab. Price Control and the device itself stay on your battery's own dashboard.

- **Virtual Cluster:** group state of charge, group power, batteries taking part, the virtual P1, the house load and the real grid; badges for state of charge, status and mode.
- **Operation mode:** one status text — status and reason; primary, how many run, demand and *Grid at last distribution* (the grid reading the last distribution was decided on, once per *Redistribute every*); the virtual P1 as the sum it is (house × Load factor − copies) and the spread of the copies' state of charge. The mode as buttons, *Reset* and *Rebuild dashboard*.
- **Overview:** gauges for the virtual P1, the group state of charge, the group power and the real P1.
- **Distribution:** a row per copy with role, direction, limit and power. A secondary shows the limit the cluster gives it. The primary gets no limit from the cluster — it runs its own loop on the meter — so its limit reads *own loop* and its direction comes from its measured power, more than 25 W either way.
- **Cluster settings:** *Clones* (another number sets the Virtual cluster up again), *Load factor*, the buffers, the start thresholds, *Concentrate below* and *Redistribute every* as sliders.
- **Today and tomorrow:** like the day plan chart of the battery dashboard — the virtual P1, the group discharging and charging, your battery, its day plan (*Plan (real battery)*; the copies follow it in `follow_source`), and the state of charge of the group and of your battery. One grid line per 500 W per copy. Without ApexCharts Card a history graph of the last 24 hours instead.
- **A tab per copy:** headed *Virtual Mix n (simulated)* with its state of charge, power, controller status and response time; its status text; gauges for the virtual P1 it steers on, its state of charge and its limits; *Copy settings* as sliders — *Max share*, *SoC offset* and *extra response time* (copy 1 too since v1.7.2); and its measured response time over 24 hours.

Your own battery gets no tab here: it is no member of the Virtual cluster and keeps its own dashboard. Its power and state of charge show on the *Cluster* tab, for comparison.

Made once. After another number of clones a notification offers *Rebuild dashboard*; it replaces the dashboard, edits included. It goes when the last cluster or Virtual cluster is deleted.

## Using it

1. **Model check first.** Clones 1, Load factor 1, `follow_source`. Copy 1 now does what your battery does: their power should run alongside on the Cluster tab. The difference is how well the model matches your battery — look at it before trusting a cluster result.
2. **Your house.** Clones 3, Load factor 1. With most house loads below one battery's maximum, the primary works alone most of the time; the secondaries join at peaks and for large solar surpluses. This shows concentrating, the quick join, and what a second or third battery would add.
3. **The split under load.** Clones 3, Load factor 3: each copy gets about what your battery gets now. Splits, redistributions, direction changes and load drops with running secondaries come up all the time.
4. **All three at a low load.** The primary keeps 60 % of its own maximum before the others get a share, so with copies of a 3 kW battery the secondaries hardly ever run at a normal house load. Set *Concentrate below* to 0 and *Max share clone 1* to, say, 300 W: the primary keeps 180 W and the rest is split over copies 2 and 3. Not what a real cluster would do for its yield, but it shows the split all day (v1.7.2).
5. **State of charge.** Large offsets, for example −40 and +35: a secondary that is empty or full early.
6. *Reset* between runs, so a run can be repeated.

The day of a test is not a different day for your battery: its energy, its logger and its daily result are what they would have been.

## What it does not test

- A real inverter holding a fixed limit as a secondary.
- Real writes to a second device, and their losses.
- Real communication faults.

That is for a test with real batteries — see [Testing your setup](cluster.md#testing-your-setup).

---

Part of [Zendure RestAPI for Home Assistant](https://github.com/windsurf/hassio-zendure-restapi), licensed under the [Apache License 2.0](../LICENSE) since v1.7.0 (MIT for v1.6.x and earlier); see [NOTICE](../NOTICE). Zendure and SolarFlow are trademarks of their respective owners; this project is not affiliated with or endorsed by Zendure.
