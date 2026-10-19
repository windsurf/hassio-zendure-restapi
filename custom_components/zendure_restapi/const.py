"""Constants for the Zendure RestAPI integration."""

DOMAIN = "zendure_restapi"
INTEGRATION_VERSION = "1.6.4"
MANUFACTURER = "Zendure"

# ── Config entry keys ────────────────────────────────────────────────────
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SN = "sn"
CONF_MODEL = "model"
CONF_PRODUCT = "product"
CONF_DEVICE_TYPE = "device_type"
CONF_SCAN_INTERVAL = "scan_interval"

# An external P1 reader is an entry of its own since v1.5.2. CONF_EXTERNAL_HOST
# is the key v1.5.0 and v1.5.1 used in the battery options; it is read once,
# by the migration, and nowhere else.
CONF_EXTERNAL_TYPE = "external_meter_type"
CONF_EXTERNAL_HOST = "external_meter_host"
CONF_EXTERNAL_INTERVAL = "external_meter_interval_ms"
# A Home Assistant sensor as external meter (v1.5.3): the power entity, and
# optionally a second entity with export as a positive number, for
# integrations such as DSMR that report import and export separately.
CONF_SOURCE_ENTITY = "source_entity"
CONF_EXPORT_ENTITY = "export_entity"
# The device name chosen when the entry was created. Fixed per entry, so the
# entity IDs derived from it do not move when a second meter is added.
CONF_DEVICE_NAME = "device_name"

# ── Defaults ─────────────────────────────────────────────────────────────
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 10
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60

DEFAULT_EXTERNAL_INTERVAL = 250  # ms; a DSMR 5 telegram arrives once a second
MIN_EXTERNAL_INTERVAL = 100
MAX_EXTERNAL_INTERVAL = 1000

# Upper bound for every power entry: device limits, controller ceilings and
# manual power. The device's own ceiling still applies where it reports one;
# this only stops the slider from ending below what the hardware can do. The
# 4000 Mix models take 4 kW and the 4000 Mix AC+ up to 5 kW on its AC input,
# which a bound of 3600 W cut off.
POWER_CEILING = 5000            # W

# ── Device types ─────────────────────────────────────────────────────────
# The meter reports a flat payload with meterType and no serial number; the
# battery reports a properties envelope with sn, product and packData.
DEVICE_TYPE_BATTERY = "battery"
DEVICE_TYPE_METER = "meter"
# An external P1 reader as its own config entry, since v1.5.2. Before that it
# was configured in the battery entry's options, and those are migrated.
DEVICE_TYPE_EXTERNAL = "external_meter"

# Name of the external meter's device. The same for every reader type, so
# its entity ids are too and the dashboards work whatever the meter is.
EXTERNAL_DEVICE_NAME = "External P1 meter"

# mDNS service of the HomeWizard P1 meter's local API v1. Only the P1 meter
# (product_type HWE-P1) is offered; HomeWizard sockets and kWh meters announce
# themselves on the same service.
ZEROCONF_HOMEWIZARD = "_hwenergy._tcp.local."
HOMEWIZARD_P1_PRODUCT = "HWE-P1"

# Sent whenever the set of external meters or a battery's choice changes, so
# the External meter selects refresh their options.
SIGNAL_EXTERNALS_CHANGED = "zendure_restapi_externals_changed"

# Status of an external meter, also a sensor.
EXTERNAL_STATUS_LIVE = "live"
EXTERNAL_STATUS_STALE = "stale"
EXTERNAL_STATUS_UNREACHABLE = "unreachable"

# Presence of this key identifies a meter payload.
METER_MARKER_KEY = "meterType"

# ── HTTP ─────────────────────────────────────────────────────────────────
# The local API caps its receive buffer at 512 bytes, so every write is sent
# as a single-property body. Never batch properties into one POST.
HTTP_TIMEOUT = 8
MAX_WRITE_BODY_BYTES = 512

PATH_REPORT = "/properties/report"
PATH_WRITE = "/properties/write"
PATH_RPC = "/rpc"

RPC_MQTT_STATUS = "HA.Mqtt.GetStatus"
RPC_MQTT_GET_CONFIG = "HA.Mqtt.GetConfig"
RPC_MQTT_SET_CONFIG = "HA.Mqtt.SetConfig"

# ── External P1 meters ───────────────────────────────────────────────────
# Supported readers. Each is one adapter class in p1meter.py: an endpoint and
# a parser, nothing else. Adding a reader means adding an adapter.
EXTERNAL_TYPE_HOMEWIZARD_V1 = "homewizard_v1"
EXTERNAL_TYPE_YOULESS = "youless"
EXTERNAL_TYPE_ECOTRACKER = "ecotracker"
EXTERNAL_TYPE_SHELLY_PRO_3EM = "shelly_pro_3em"
EXTERNAL_TYPE_ZENDURE = "zendure"
# Labels for the options form, in the order offered. HomeWizard first because
# it is the reader most Zendure owners in the Netherlands already have.
EXTERNAL_TYPE_LABELS = {
    EXTERNAL_TYPE_HOMEWIZARD_V1: "HomeWizard P1 meter (local API v1)",
    EXTERNAL_TYPE_YOULESS: "YouLess LS120",
    EXTERNAL_TYPE_ECOTRACKER: "EcoTracker (everHome)",
    EXTERNAL_TYPE_SHELLY_PRO_3EM: "Shelly Pro 3EM",
    EXTERNAL_TYPE_ZENDURE: "Zendure P1 meter or Smart Meter 3CT",
}
EXTERNAL_TYPES = tuple(EXTERNAL_TYPE_LABELS)
DEFAULT_EXTERNAL_TYPE = EXTERNAL_TYPE_HOMEWIZARD_V1

# Any power sensor already in Home Assistant. Not on the reader form: it has
# no address, and is added through its own menu entry.
EXTERNAL_TYPE_HA_SENSOR = "ha_sensor"
EXTERNAL_KIND_LABELS = {
    **EXTERNAL_TYPE_LABELS,
    EXTERNAL_TYPE_HA_SENSOR: "Home Assistant sensor",
}
# A state is read from memory, not over the network, so it can be looked at
# often; only a new last_reported counts as a reading.
HA_SENSOR_INTERVAL = 250        # ms

# A request normally takes tens of milliseconds, so the timeout is far below
# the Zendure one: a poll that hangs for seconds holds up every one behind it.
EXTERNAL_TIMEOUT = 2            # s
EXTERNAL_BACKOFF_AFTER = 8      # failed polls in a row, two seconds at 250 ms
EXTERNAL_BACKOFF_SECONDS = 5    # s between retries once backed off

# ── mDNS ─────────────────────────────────────────────────────────────────
ZEROCONF_TYPE = "_zendure._tcp.local."

# ── Data model ───────────────────────────────────────────────────────────
# Prefix used for flattened per-pack keys: pack1.socLevel, pack2.power, ...
PACK_PREFIX = "pack"

# Pack state as the device reports it, used to sign the cell power: a pack
# publishes a magnitude, not a direction.
PACK_STATE_CHARGING = 1
PACK_STATE_DISCHARGING = 2

# Kelvin offset used by Zendure for 0.1K temperature storage.
KELVIN_OFFSET_DECIKELVIN = 2731

# Envelope keys that carry identity rather than telemetry.
IDENTITY_KEYS = frozenset({"sn", "deviceId", "product", "version"})


# ── Operation modes ──────────────────────────────────────────────────────
# The strategy layer. "Operation mode" decides *what* the battery should be
# doing; the controller decides *how*, by writing acMode and the limits.
MODE_STANDBY = "standby"
MODE_MANUAL = "manual"
MODE_SMART_MATCHING = "smart_matching"
MODE_SMART_DISCHARGE = "smart_discharge_only"
MODE_SMART_CHARGE = "smart_charge_only"
MODE_QUICK_CHARGE = "quick_charge"
MODE_QUICK_DISCHARGE = "quick_discharge"

OPERATION_MODES = (
    MODE_STANDBY,
    MODE_MANUAL,
    MODE_SMART_MATCHING,
    MODE_SMART_DISCHARGE,
    MODE_SMART_CHARGE,
    MODE_QUICK_CHARGE,
    MODE_QUICK_DISCHARGE,
)

# The mode that writes nothing at all. Anything that touches the device, even
# writing a zero, is a command, and a device with its own energy manager will
# fight it. Passive is the only genuinely read-only state.

# Modes in which the closed-loop controller tracks the meter.
SMART_MODES = frozenset({MODE_SMART_MATCHING, MODE_SMART_DISCHARGE, MODE_SMART_CHARGE})

# ── Meter source ─────────────────────────────────────────────────────────
# Which meter feeds both loops. The default is `auto` since v1.5.1. Without an
# external meter configured `auto` is exactly `zendure`, so an installation
# that never set up an external meter behaves as it always has; one that has
# set one up uses it — which is what someone with a HomeWizard and no Zendure
# meter needs to get the smart modes working at all. In v1.5.0 the default was
# `zendure`. `external` uses the external P1 reader only and blocks like any
# meter would when it goes quiet — the setting for a clean comparison run,
# where a silent switch to the other meter would contaminate the block. `auto`
# prefers the external reader and falls back to the Zendure meter while the
# external one has no fresh telegram.
METER_SOURCE_ZENDURE = "zendure"
METER_SOURCE_EXTERNAL = "external"
METER_SOURCE_AUTO = "auto"
METER_SOURCES = (METER_SOURCE_ZENDURE, METER_SOURCE_EXTERNAL, METER_SOURCE_AUTO)

# ── Device property values ───────────────────────────────────────────────
AC_MODE_CHARGE = 1
AC_MODE_DISCHARGE = 2

# ── Settings stored in config entry options ──────────────────────────────
OPT_OPERATION_MODE = "operation_mode"
OPT_MANUAL_POWER = "manual_power"
OPT_MAX_CHARGE_POWER = "max_charge_power"
OPT_MAX_DISCHARGE_POWER = "max_discharge_power"
OPT_START_DISCHARGE_AT = "start_discharge_at"
OPT_START_CHARGE_AT = "start_charge_at"
OPT_DIRECTION_DELAY = "direction_change_delay"
OPT_MODE_THRESHOLD = "mode_threshold"
OPT_TRIM_THRESHOLD = "trim_threshold"
OPT_MIN_CHARGE_POWER = "min_charge_power"
OPT_MIN_DISCHARGE_POWER = "min_discharge_power"
OPT_CHARGE_BUFFER = "charge_buffer"
OPT_DISCHARGE_BUFFER = "discharge_buffer"
OPT_SOC_PROTECTION = "soc_protection"
OPT_TRIM_FACTOR = "trim_factor"
OPT_METER_SOURCE = "meter_source"
# The entry_id of the external meter a battery uses, or EXTERNAL_NONE.
# Unset with one external meter configured means that one.
OPT_EXTERNAL_METER = "external_meter"
EXTERNAL_NONE = "none"
# A one-off request to create the Zendure RestAPI dashboard (v1.6.0). Set by
# the config flow or the options, cleared once the request has been handled.
OPT_CREATE_DASHBOARD = "create_dashboard"
# Set once the dashboard was created, found, or tried without being asked
# (v1.6.3): it is made by default, once, and never again after a user deleted it.
OPT_DASHBOARD_OFFERED = "dashboard_offered"

# ── Price control (v1.6.0) ───────────────────────────────────────────────
# The Operation mode of one battery set from the electricity price: an entry
# and a device of its own, added like an external meter. Off until switched
# on, so an automation or planner that already steers the Operation mode stays
# the only one that does.
DEVICE_TYPE_PRICE = "price_control"
CONF_BATTERY_ENTRY = "battery_entry"
# Our own steering, not a Zendure product: no Zendure in the name.
PRICE_DEVICE_NAME = "Price Control"
PRICE_MODEL = "Dynamic tariffs"
OPT_PRICE_ENTITY = "price_entity"
OPT_PRICE_CONTROL = "price_control"
OPT_PRICE_CHEAP = "price_cheap"
OPT_PRICE_EXPENSIVE = "price_expensive"
OPT_PRICE_CHEAP_MODE = "price_cheap_mode"
OPT_PRICE_EXPENSIVE_MODE = "price_expensive_mode"
OPT_PRICE_BETWEEN_MODE = "price_between_mode"
PRICE_MODE_OPTIONS = {
    OPT_PRICE_CHEAP_MODE: (MODE_QUICK_CHARGE, MODE_SMART_CHARGE),
    OPT_PRICE_EXPENSIVE_MODE: (MODE_SMART_DISCHARGE, MODE_QUICK_DISCHARGE),
    OPT_PRICE_BETWEEN_MODE: (MODE_SMART_MATCHING, MODE_STANDBY),
}
PRICE_STATUS_OFF = "off"
PRICE_STATUS_NO_PRICE = "no_price"
PRICE_STATUS_CHEAP = "cheap"
PRICE_STATUS_BETWEEN = "between"
PRICE_STATUS_EXPENSIVE = "expensive"
PRICE_STATUS_NO_PLAN = "no_plan"
PRICE_STATUSES = (
    PRICE_STATUS_OFF, PRICE_STATUS_NO_PRICE, PRICE_STATUS_NO_PLAN, PRICE_STATUS_CHEAP,
    PRICE_STATUS_BETWEEN, PRICE_STATUS_EXPENSIVE,
)
SIGNAL_PRICE_CHANGED = f"{DOMAIN}_price_changed_{{}}"

# ── Price control: the day plan (v1.6.0) ─────────────────────────────────
# A second way to decide, next to the two fixed prices: a plan over the
# prices of today and tomorrow (Nord Pool, HACS integration), with separate
# import and export formulas, conversion losses and a margin per kWh. See
# dayplan.py.
OPT_PRICE_DECISION = "price_decision"
DECISION_FIXED = "fixed_prices"
DECISION_PLAN = "day_plan"
PRICE_DECISIONS = (DECISION_FIXED, DECISION_PLAN)
# What the price sensor holds; the formulas need the bare spot price.
OPT_PRICE_CONTAINS = "price_contains"
PRICE_CONTAINS_OPTIONS = ("spot_vat", "spot", "import_price")
OPT_NET_METERING = "net_metering"
OPT_IMPORT_FACTOR = "import_factor"
OPT_IMPORT_SURCHARGE = "import_surcharge"
OPT_EXPORT_FACTOR = "export_factor"
OPT_EXPORT_SURCHARGE = "export_surcharge"
OPT_WEAR_COST = "wear_cost"
OPT_MIN_MARGIN = "minimum_margin"
OPT_HOUSE_LOAD = "expected_house_load"
OPT_CHARGE_EFFICIENCY = "charge_efficiency"
OPT_DISCHARGE_EFFICIENCY = "discharge_efficiency"
PLAN_ACTIONS = ("charge", "discharge", "idle")

# The three modes per band, filled in once whenever Decision or Net metering
# is switched (v1.6.1); they can be changed freely until the next switch.
# Fixed prices cover the house and hold the grid at zero in between; the day
# plan trades at full power. With the day plan and net metering the battery
# rests in between: the plan already moves the energy where the spread is,
# and holding the grid at zero would spend what it set aside. Replayed on 30
# consecutive days, EUR per day, net metering / 2027 rules:
#   fixed, in between smart_matching   0.22 / 0.45
#   fixed, in between standby          0.04 / 0.05
#   plan,  in between smart_matching   1.67 / 1.02
#   plan,  in between standby          1.73 / 0.67
def price_presets(decision: str, net_metering: bool) -> dict[str, str]:
    """The cheap, expensive and in-between mode for a decision."""
    plan = decision == DECISION_PLAN
    return {
        OPT_PRICE_CHEAP_MODE: MODE_QUICK_CHARGE,
        OPT_PRICE_EXPENSIVE_MODE: MODE_QUICK_DISCHARGE if plan else MODE_SMART_DISCHARGE,
        OPT_PRICE_BETWEEN_MODE: MODE_STANDBY if plan and net_metering else MODE_SMART_MATCHING,
    }

DEFAULTS = {
    OPT_OPERATION_MODE: MODE_STANDBY,   # hands off until a mode is chosen
    OPT_MANUAL_POWER: 0,
    OPT_MAX_CHARGE_POWER: 800,
    OPT_MAX_DISCHARGE_POWER: 800,
    OPT_START_DISCHARGE_AT: 5,          # W of import before discharging starts
    OPT_START_CHARGE_AT: 5,             # W of export before charging starts
    OPT_DIRECTION_DELAY: 10,            # s to pause between directions, 0 = off
    # Smallest adjustment worth writing, per loop. Both keep the values the
    # constants used to hold, so the behaviour is unchanged until they are
    # raised on purpose.
    #
    # Measured over four hours of trace across three operation modes, 1818
    # writes, scoring each one by the grid error four seconds later against the
    # three seconds before it:
    #
    #     as shipped, 10 and 40 W      1818 writes   +13.8 Wh
    #     mode 125, trim 350            318 writes   +81.6 Wh
    #
    # The optimum is flat — anywhere from 250 to 450 W costs the trim loop
    # under 5% — and it moves with how restless the house is, which is why
    # these are settings rather than constants. Both loops came out well above
    # what they shipped with, the trim loop by an order of magnitude, because
    # it samples every second and therefore sees every excursion that would
    # have passed on its own.
    OPT_MODE_THRESHOLD: 10,             # W, adjustments only
    OPT_TRIM_THRESHOLD: 40,             # W, adjustments only
    OPT_MIN_CHARGE_POWER: 0,            # floor in every smart mode, 0 = off
    OPT_MIN_DISCHARGE_POWER: 0,         # floor in every smart mode, 0 = off
    OPT_CHARGE_BUFFER: 5,               # W of import to aim for while charging
    OPT_DISCHARGE_BUFFER: 5,            # W of import to aim for while discharging
    OPT_SOC_PROTECTION: True,
    OPT_TRIM_FACTOR: 80,                # % of the remaining error per sample
    OPT_METER_SOURCE: METER_SOURCE_AUTO,
    OPT_EXTERNAL_METER: "",
    OPT_PRICE_ENTITY: "",
    OPT_PRICE_CONTROL: False,
    OPT_PRICE_CHEAP: 0.15,              # per kWh, at or below: charge
    OPT_PRICE_EXPENSIVE: 0.35,          # per kWh, at or above: discharge
    OPT_PRICE_CHEAP_MODE: MODE_QUICK_CHARGE,
    OPT_PRICE_EXPENSIVE_MODE: MODE_SMART_DISCHARGE,
    OPT_PRICE_BETWEEN_MODE: MODE_SMART_MATCHING,
    # Day plan. The tariff defaults are a Dutch dynamic contract in 2026:
    # import = spot x 1.21 + supplier surcharge and energy tax incl. VAT,
    # export = spot x 1.10 + 0.02 once net metering has ended.
    OPT_PRICE_DECISION: DECISION_FIXED,
    OPT_PRICE_CONTAINS: "spot_vat",     # the Nord Pool sensor's own default
    OPT_NET_METERING: False,
    OPT_IMPORT_FACTOR: 1.21,
    OPT_IMPORT_SURCHARGE: 0.13505,      # per kWh
    OPT_EXPORT_FACTOR: 1.10,
    OPT_EXPORT_SURCHARGE: 0.02,         # per kWh
    OPT_WEAR_COST: 0.02,                # per kWh through the battery
    OPT_MIN_MARGIN: 0.02,               # per kWh, below this no cycle
    OPT_HOUSE_LOAD: 400,                # W the battery covers while discharging
    OPT_CHARGE_EFFICIENCY: 95,          # %, grid to cells
    OPT_DISCHARGE_EFFICIENCY: 96,       # %, cells to house
}

# ── Controller tuning ────────────────────────────────────────────────────
# The first step is deliberately undershot: the device's actual response is
# not yet known, so committing the full correction invites overshoot. Once a
# direction is established the mode loop applies the whole correction. It runs
# at the battery interval, where the meter's own 0.4-1.1 s delay is a tenth of
# a period and the absolute formula re-measures the result every cycle. Damping
# there would only make it converge over two cycles instead of one.
CONTROL_FACTOR_START = 0.75
CONTROL_FACTOR_BALANCE = 1.00



# Only start a new direction while the battery is genuinely idle. The measured
# power is the weaker of the two tests: an inverter draws its own standby power
# continuously, so the pack never reads zero. A 3000 Mix AC+ idles at roughly
# 41 W of discharge, which a 30 W band reads as "still running" forever.
# The primary test is therefore whether the controller is commanding anything.
CONTROL_DEADBAND = 60           # W

# Ignore adjustments smaller than this, to avoid pointless writes. The value
# lives in DEFAULTS[OPT_MODE_THRESHOLD]; this is the floor used before the
# settings are available.
CONTROL_MIN_STEP = 10           # W


# Settling time after a direction change, before the controller acts again.
# Expressed in seconds rather than cycles: the reading that matters is how long
# the device has had to respond, which does not change when the polling
# interval does. Counting cycles instead made the wait 10 s at a 5 s interval
# and 20 s at a 10 s interval, for no reason related to the hardware.

# ── The trim loop ────────────────────────────────────────────────────────
# Every sample is a fresh *measurement*, but not a fresh *error*: part of what
# it shows has already been ordered and is still on its way. The loop keeps
# that amount and corrects only the remainder, so it can never ask twice for
# the same watt however long the device takes to answer.
#
# That one piece of bookkeeping is what makes the loop device-independent, and
# it is why there is no dead time, response fraction, timeout or noise
# estimate anywhere in this file. Simulated across sixty devices — dead time
# nought to three samples, 400 to instant watts per sample, meter one to three
# samples behind — it settles within 54 W on every one of them, where a loop
# treating each sample as a fresh error rings at 2519 W on some and needs four
# times the writes.
#
# Measured on the hardware in manual mode, where no controller is in the loop,
# the device gave no consistent rate at all: 750, 820, 1083 and 1500 W per
# second across six steps, with dead time of nought to one sample. Four
# separate attempts to pin that down to a number were wrong. The loop must
# therefore not contain one.
#
# The only threshold left: do not write a correction smaller than the house
# wanders by on its own, or the loop chases the household into a limit cycle.
# Observed at a load of about 200 W: 19 writes a minute and a grid swinging
# between -106 and +122 W. The wander is roughly -30 to +20 W.
CONTROL_MIN_STEP_FAST = 40      # W

# The gain lives in DEFAULTS[OPT_TRIM_FACTOR], as a percentage of the
# remaining error per sample. Below 100 for the same reason any feedback loop
# is: the bookkeeping removes double-counting, not the meter's own delay.

# A reading beyond this is not a measurement but a fault, and faults stop the
# battery rather than steer it.
#
# The bound is the grid connection, not the battery: this meter reads the whole
# house. A 3600 W ceiling would be the inverter's maximum and quite wrong here
# — the installation's own measurements record an evening peak of 4384 W from
# an oven and a dryer together, and a boiling-water tap alone draws 2330 W.
#
# 3x25 A at 230 V is 17.25 kW, and no reading past that can be real. The check
# earns its place because scale and sign errors are a live risk on this API:
# six of them were found in the zenSDK documentation for this device alone.
METER_SANE_LIMIT = 17250        # W, three phases of 25 A at 230 V

# Meter data older than this is not trusted for closed-loop control. Until
# v1.1.0 this constant was declared and never read: the controller ran on the
# battery poll and had no timestamp to compare against. The trim loop runs on
# the meter itself, so the age is now both knowable and load-bearing.
METER_MAX_AGE = 60              # seconds

# How long `auto` keeps trusting the external meter without a new telegram
# before it moves to the Zendure meter. Far tighter than METER_MAX_AGE on purpose: that
# limit decides when to stop the battery, this one only when to listen to the
# other meter, and with a second meter at hand there is no reason to steer on a
# reading five telegrams old. A forced `external` source keeps METER_MAX_AGE,
# exactly like the Zendure meter always has.
METER_FALLBACK_AGE = 5          # seconds, five missed telegrams

# ── Trace recording ──────────────────────────────────────────────────────
# One CSV row per meter sample, started and stopped by the Trace recording
# switch. It exists to make two control regimes comparable on one yardstick:
# this controller in a smart mode, and the device's own energy manager while
# the operation mode sits in standby, which writes nothing at all.
#
# The switch is the only control. Hanging it on the debug log level needed no
# new entity but had no off: the level cannot be lowered again without a
# restart, so a recording ran until the next one and its file was never closed.
#
# The recorder scores nothing. Band, error in watt-hours and recovery time are
# all computed afterwards from the file, because those definitions are still
# moving and a definition in code costs a release to change.
TRACE_DIR = DOMAIN              # under the Home Assistant config directory

# Rows buffered before a write leaves the event loop. At one sample a second
# that is one file operation every half minute; the cost of a crash is the
# unflushed remainder, which is a measurement and not a decision.
TRACE_FLUSH_ROWS = 30

# Roll over to a new file here. A day at one sample a second is 86400 rows.
# Roughly an hour at a one-second meter, some 150 kB, which keeps a file small
# enough to copy off a running system and to recover after a crash. The old
# limit was more than a day and some fifteen megabytes, which is exactly the
# file you cannot get off the machine when you need it.
#
# Not exact: the rollover point is drawn per file within a spread of the
# nominal size. Files that all break on the same minute and second look like a
# pattern in the data when read back, and a load that happens to be periodic
# with the file length would otherwise fall in the same place in every file.
# ── Rest state ───────────────────────────────────────────────────────────
# With both of its own limits at zero, this controller hands the flash-write
# flag back so the inverter can drop to its low-power state. Measured on a
# 3000 Mix AC+ with Backup mode closed: the cells give 30 W with the flag
# set and 5 W with it clear. While actually converting the flag changes nothing
# — 453 W from the cells at 500 W of charging either way — so the saving exists
# only at a zero limit. Waking costs nothing measurable: the same 500 W order
# responded in 6 s from rest and 6 s from awake.
#
# There is no delay setting, because the poll rate already is one. Entering
# requires two consecutive polls that both see zero, so a limit passing through
# zero during a direction change never triggers it: that lasts a sample, not a
# poll. An earlier version offered a delay in seconds and was measured to be
# meaningless — at a ten second poll nothing under about eleven seconds could
# ever fire, so every value from 0 to 10 behaved identically.
#
# The floor below exists because the poll interval is configurable and the
# comparison runs in this project set the battery to one second. Two polls
# would then be two seconds and the natural hysteresis would vanish exactly
# when someone is measuring.
CONTROL_REST_MIN_SECONDS = 10   # floor under the poll-derived hysteresis

TRACE_MAX_ROWS = 5000
TRACE_ROWS_JITTER = 400     # plus or minus, drawn per file

