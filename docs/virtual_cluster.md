# Virtual cluster

A **Virtual cluster** shows how a cluster of batteries would regulate in your house, before you buy a second one. It makes up to three copies of one of your batteries, groups them in a cluster of their own and lets them steer on a **virtual P1 meter** fed by your real house load.

**Nothing happens for real.** The Virtual cluster writes nothing to your battery, does not claim the grid, and your battery keeps doing exactly what it does now — with its own Operation mode, its own automations, its own meter. The test costs computing power and a little database, nothing else.

```
real (unchanged)                       virtual (writes nothing real)
P1 meter ──┐                           virtual P1 = house load × Load factor − copies
           ├─► house load ────────────►        │
battery ───┘   (P1 + battery power)     Virtual cluster
                                        ├─ Virtual copy 1  twin (offsets 0 by default)
                                        ├─ Virtual copy 2  twin SoC + offset
                                        └─ Virtual copy 3  twin SoC + offset
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

*Settings → Devices & services → Add integration → Zendure RestAPI → Virtual cluster (simulated).*

Not next to a battery cluster (v1.7.3): the two exclude each other, since the Virtual cluster is the test before you buy. With a battery cluster set up, adding a Virtual cluster is refused, and the other way round.

Choose the battery to copy. That is all: the Virtual cluster appears with three copies, follows your battery's Operation mode, and the dashboard **Virtual Cluster** is made.

One Virtual cluster at a time. It can exist next to a real battery cluster. Delete it like any entry; its devices and entities go with it, and the dashboard *Virtual Cluster* when no cluster is left.

**Your battery steered by something else.** When this integration only monitors your battery — Operation mode `standby`, and your own automation or a HEMS sets the limits — `follow_source` runs the copies in standby too. Choose a mode for the Virtual cluster yourself, `smart_matching` for instance: the copies then regulate on the virtual P1 while your battery carries on as before.

## Settings

On the device *Virtual cluster*, and per copy on the copy's own device (*Virtual copy 1/2/3*, since v1.7.3):

| Entity | Values | Default | |
|---|---|---|---|
| Operation mode | the cluster's modes, and `follow_source` | `follow_source` | `follow_source` runs the mode your battery runs, so also what your planner or Price Control chose; `manual` on your battery means standby here |
| Copies | 1–3 | 3 | another number sets the cluster up again; *Clones* until v1.7.2 |
| Load factor | 1–3, step 0.5 | 1 | 1 is your house; 3 gives each copy about what your battery gets now |
| *Virtual copy n* SoC offset (copy 1 / 2 / 3) | −50 … +50 % | 0 / −20 / +15 | applied at the start and at *Reset* only; only the differences count, since the set is centred on your battery (v1.7.2, see [Where the copies start](#where-the-copies-start)); copy 1 since v1.7.2 |
| *Virtual copy n* Extra response time (copy 1 / 2 / 3) | 0–10 s, step 0.5 | 0 / 1 / 3 s | a write is in that copy this much later than the measured 1.8 s, as another unit or firmware might answer; applies at once; copy 1 since v1.7.2. With both offsets of copy 1 at 0 it is the twin of your battery |
| *Virtual copy n* Max share (copy 1 / 2 / 3) | 0–5000 W, step 50 | 0 | the most that copy gives or takes in the group, as *Max share* on a battery under a real cluster; 0 is its own maximum; applies at once, in every mode — also quick and `follow_source`, so leave it at 0 to compare a copy with your battery (v1.7.2) |
| Reset | button, and since v1.7.3 a tick under *Configure* | | every copy still, both limits 0, the copies started again around your battery's state of charge of that moment |
| Charge/Discharge buffer, Start discharging at import, Start charging at export | as on a battery | your battery's | |
| Concentrate below, Redistribute every | as on the cluster | 300 W, 30 s | |
| Rebuild dashboard | button, and since v1.7.3 a tick under *Configure* | | builds *Virtual Cluster* again |
| Reset settings | tick under *Configure* (v1.7.3), not stored | | every setting above and on the copies back to its default: Copies 3, Load factor 1, the offsets, response times and Max share of the table, Operation mode *Follow source*, buffers, start thresholds, *Concentrate below* and *Redistribute every*; your battery and Price Control are not touched; the copies keep running, tick *Reset* as well to start them with the new offsets |

Since v1.7.3 neither is a tile on the dashboard (and *Reset settings*, next to them, is a tick only): *Reset* and *Rebuild dashboard* are ticks under *Configure* on the Virtual cluster's entry, a deliberate step rather than a tap away. The two buttons remain as entities, for an automation of your own.

**Updating to v1.7.3: delete and add again.** Note your settings, delete the Virtual cluster while still on v1.7.2 (its dashboard at `/zendure-cluster` goes with it), update, restart Home Assistant and add the Virtual cluster again. It then starts with the names, entity ids and the dashboard `/virtual-cluster/virtual-cluster` of v1.7.3 at once; nothing of value is lost but the history of the test. Kept instead, it is moved over as below.

**Names since v1.7.3.** A copy is *Virtual copy n* whatever the model you copy, so its entity ids stay the same when you copy another battery; until v1.7.2 it was *Virtual Mix n*, or *Virtual battery n* for a model without "Mix", and its ids move once from `…virtual_mix_n_…` to `…virtual_copy_n_…`. Copy 1 with its offsets at 0 is the twin of your battery. The three settings of a copy moved from the device *Virtual cluster* to the copy's own device and lost "clone n" from their name; *Response offset* is now *Extra response time*, as the dashboard always called it. On an existing installation the entity ids move once, history included: `number.virtual_cluster_soc_offset_clone_2` → `number.virtual_copy_2_soc_offset`, `number.virtual_cluster_response_offset_clone_2` → `number.virtual_mix_2_extra_response_time`, `number.virtual_cluster_max_share_clone_2` → `number.virtual_mix_2_max_share`, `number.virtual_cluster_clones` → `number.virtual_cluster_copies`. An id you renamed yourself stays. Press *Rebuild dashboard* afterwards. With fewer copies, the settings of a copy that goes are removed with it; their values are kept for when it comes back.

## Entities

**Virtual cluster:** the settings above, and Status, Battery power, State of charge (*Group power* and *Group state of charge* until v1.7.2; ids `sensor.virtual_cluster_battery_power` and `sensor.virtual_cluster_state_of_charge`), Batteries taking part — as on the [cluster](cluster.md#entities).

**Virtual P1 meter** (model marked *(simulated)*):

| Entity | |
|---|---|
| Grid power | the virtual P1: house load × Load factor − copies; attributes `source`, `telegrams` and `load_factor` (the factor in use) |
| House load | real grid + AC power of your battery |
| Real grid power | your real P1 meter, as the copies' meter samples it |

Until v1.7.2 these were *Power* and *Real grid*, and *Load factor* was a sensor of its own, a copy of the setting; its entity is removed. The ids move once: `sensor.virtual_p1_meter_power` → `sensor.virtual_p1_meter_grid_power`, `sensor.virtual_p1_meter_real_grid` → `sensor.virtual_p1_meter_real_grid_power`.

**Each copy** (*Virtual copy 1/2/3*, model marked *(simulated)*): State of charge, Battery power, Controller status (with its role and limit), Charge limit, Discharge limit, Response time, and its settings Max share, SoC offset and Extra response time. No energy counters, so the Energy dashboard never sees a battery that does not exist, and no Price Control.

## Where the copies start

At the start of the Virtual cluster (adding it, a restart of Home Assistant, a reload) and at *Reset* — and only then — every copy gets a state of charge of its own (v1.7.2):

1. **Centred on your battery.** Each copy gets your battery's SoC plus its own offset minus the mean offset (copy 1 at 0 by default). With one copy its offset centres away: it starts on your battery's SoC. The copies then average your battery: the cluster's *State of charge* starts on it.
2. **Shifted into 5–100 %.** When that leaves a copy below 5 % or above 100 %, the whole set moves until it fits, so the differences between the copies stay the offsets you set.
3. **Cut only when the set is wider than 95 points** (offsets −50 and +50): the lowest starts on 5 %, the rest at most 100 %.

With offsets 0 / −20 / +15:

| Your battery | Virtual copy 1 / 2 / 3 | Average |
|---|---|---|
| 46 % | 47.7 / 27.7 / 62.7 | 46.0 |
| 4 % | 25 / 5 / 40 (shifted +19.3) | 23.3 |
| 95 % | 85 / 65 / 100 (shifted −11.7) | 83.3 |

With one copy, or offsets that add up to 0, Virtual copy 1 starts on your battery's SoC. After a shift it does not: compare it with your battery on the change in state of charge, not on the level. For such a comparison, press *Reset* while your battery is in the middle (with these offsets between 23 and 83 %), where nothing shifts.

What happened is on the entities: the cluster status carries `start_soc_real`, `start_centre` and `start_shift`, each copy's *State of charge* `start_offset_vs_real`. A changed offset counts from the next start or *Reset*.

## Dashboard

**Virtual Cluster** (`/virtual-cluster/virtual-cluster`, a dashboard of its own since v1.7.3; until v1.7.2 it shared `/zendure-cluster` with the battery cluster). The tabs: *Cluster* with the house with a battery of its heading (`mdi:home-battery-outline`), *Virtual copy 1*, *2* and *3* with a numbered icon, the name as tooltip. Each copy is called by its device name throughout. A dashboard a Virtual cluster made at `/zendure-cluster` under v1.7.2 is left as it is, edits included; a notification says it can be removed under *Settings → Dashboards*. The first section is headed *Virtual Cluster (simulated)*, like the copies' *Virtual copy n (simulated)*; the sliders of a copy are half a row wide, as elsewhere (v1.7.3).


Since v1.7.2 in the layout of the battery dashboard: sections with a heading and badges, a slider for every number you can set, gauges for what is measured, and ApexCharts for the chart. What you set for the group is on the *Cluster* tab, what you set per copy on that copy's tab. Price Control and the device itself stay on your battery's own dashboard.

- **Virtual Cluster:** state of charge and battery power of the cluster, batteries taking part, the virtual P1, the house load and the real grid; badges for state of charge, status and mode.
- **Operation mode:** one status text — status and reason; primary, how many run, demand and *Grid at last distribution* (the grid reading the last distribution was decided on, once per *Redistribute every*); the virtual P1 as the sum it is (house × Load factor − copies) and the spread of the copies' state of charge. The mode as buttons; *Reset* and *Rebuild dashboard* are under *Configure* (v1.7.3).
- **Overview:** gauges for the virtual P1, the state of charge and the battery power of the cluster, and the real P1.
- **Distribution:** a row per copy with role, direction, limit and power. A secondary shows the limit the cluster gives it. The primary gets no limit from the cluster — it runs its own loop on the meter — so its limit reads *own loop* and its direction comes from its measured power, more than 25 W either way.
- **Cluster settings:** *Copies* (another number sets the Virtual cluster up again), *Load factor*, the buffers, the start thresholds, *Concentrate below* and *Redistribute every* as sliders.
- **Today and tomorrow:** like the day plan chart of the battery dashboard — the virtual P1, the cluster discharging and charging, your battery, its day plan (*Plan (real battery)*; the copies follow it in `follow_source`), and the state of charge of the cluster (*SoC cluster*) and of your battery. One grid line per 500 W per copy. Without ApexCharts Card a history graph of the last 24 hours instead.
- **A tab per copy:** headed *Virtual copy n (simulated)* with its state of charge, power, controller status and response time; its status text; gauges for the virtual P1 it steers on, its state of charge and its limits; *Copy settings* as sliders — *Max share*, *SoC offset* and *Extra response time* (copy 1 too since v1.7.2); and its measured response time over 24 hours.

Your own battery gets no tab here: it is no member of the Virtual cluster and keeps its own dashboard. Its power and state of charge show on the *Cluster* tab, for comparison.

Made once. After another number of copies a notification offers *Rebuild dashboard* (the button, or since v1.7.3 a tick under *Configure* on the entry); it replaces this dashboard, edits included, and leaves the battery cluster's alone. It goes when the last Virtual cluster is deleted.

## Using it

1. **Model check first.** Copies 1, Load factor 1, `follow_source`. Copy 1 now does what your battery does: their power should run alongside on the Cluster tab. The difference is how well the model matches your battery — look at it before trusting a cluster result.
2. **Your house.** Copies 3, Load factor 1. With most house loads below one battery's maximum, the primary works alone most of the time; the secondaries join at peaks and for large solar surpluses. This shows concentrating, the quick join, and what a second or third battery would add.
3. **The split under load.** Copies 3, Load factor 3: each copy gets about what your battery gets now. Splits, redistributions, direction changes and load drops with running secondaries come up all the time.
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
