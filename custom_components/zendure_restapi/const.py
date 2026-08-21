"""Constants for the Zendure RestAPI integration."""

DOMAIN = "zendure_restapi"
INTEGRATION_VERSION = "1.1.0"
MANUFACTURER = "Zendure"

# ── Config entry keys ────────────────────────────────────────────────────
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SN = "sn"
CONF_MODEL = "model"
CONF_PRODUCT = "product"
CONF_DEVICE_TYPE = "device_type"
CONF_SCAN_INTERVAL = "scan_interval"

# ── Defaults ─────────────────────────────────────────────────────────────
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 10
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60

# ── Device types ─────────────────────────────────────────────────────────
# The meter reports a flat payload with meterType and no serial number; the
# battery reports a properties envelope with sn, product and packData.
DEVICE_TYPE_BATTERY = "battery"
DEVICE_TYPE_METER = "meter"

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

# ── mDNS ─────────────────────────────────────────────────────────────────
ZEROCONF_TYPE = "_zendure._tcp.local."

# ── Data model ───────────────────────────────────────────────────────────
# Prefix used for flattened per-pack keys: pack1.socLevel, pack2.power, ...
PACK_PREFIX = "pack"

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
OPT_MIN_CHARGE_POWER = "min_charge_power"
OPT_MIN_DISCHARGE_POWER = "min_discharge_power"
OPT_CHARGE_BUFFER = "charge_buffer"
OPT_DISCHARGE_BUFFER = "discharge_buffer"
OPT_SOC_PROTECTION = "soc_protection"
OPT_TRIM_FACTOR = "trim_factor"

DEFAULTS = {
    OPT_OPERATION_MODE: MODE_STANDBY,   # hands off until a mode is chosen
    OPT_MANUAL_POWER: 0,
    OPT_MAX_CHARGE_POWER: 800,
    OPT_MAX_DISCHARGE_POWER: 800,
    OPT_START_DISCHARGE_AT: 30,         # W of import before discharging starts
    OPT_START_CHARGE_AT: 5,             # W of export before charging starts
    OPT_DIRECTION_DELAY: 10,            # s to pause between directions, 0 = off
    OPT_MIN_CHARGE_POWER: 0,            # floor in every smart mode, 0 = off
    OPT_MIN_DISCHARGE_POWER: 0,         # floor in every smart mode, 0 = off
    OPT_CHARGE_BUFFER: 5,               # W of import to aim for while charging
    OPT_DISCHARGE_BUFFER: 5,            # W of import to aim for while discharging
    OPT_SOC_PROTECTION: True,
    OPT_TRIM_FACTOR: 80,                # % of each upward trim, see const below
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

# Trimming a limit *down* is not the mirror image of trimming it up, so the
# two do not share a factor either.
#
# Down cannot overshoot. The result is clamped at zero and the trim loop may
# not reverse, so the worst an over-large downward correction can do is reach
# a limit of 0 W — which is exactly where a vanished load wants it. Every
# cycle spent easing towards that is a cycle of pushing power onto the grid.
#
# Up can overshoot, and expensively. Every watt added while a load is about to
# disappear becomes export a moment later.
#
# Measured on hardware. At 50% both directions, a load event took four seconds
# to wind down. At 30% it took ten, and the extra six seconds sat between
# -2500 and -800 W: roughly 4 Wh exported against 2.7 Wh for the comparable
# event at full-ish strength. The upward side meanwhile reached the same
# limit either way, two seconds later — the error term is large enough that
# damping barely slows it. Lowering one shared factor made the cheap direction
# slower without making the expensive one safer.
TRIM_FACTOR_DOWN = 1.00

# The trim loop is the opposite case from the mode loop, which is why the
# upward direction has its own setting (OPT_TRIM_FACTOR) rather than sharing
# CONTROL_FACTOR_BALANCE. At one correction per second against a meter that
# reports up to one second late, the dead time is a whole period: the loop
# commits a correction for a deviation whose answer it has not seen.
#
# 100% is not safe. With the evidence gate above in place it settles at one
# sample of meter delay, but at two samples it still sustains a 500 W
# oscillation and never comes to rest — and 0.4 to 1.1 s of meter delay reaches
# into that range. 80% settles at both, which makes it the highest value that
# is demonstrably stable.
#
# It is also close to the cheapest. Replayed against three logged load events,
# summed wrong-way energy: 12.2 Wh at 30%, 11.1 at 50%, 8.6 at 80%, 8.0 at
# 100%. Damping the upward side mostly costs unserved import rather than
# saving export, and avoided import is worth more per watt-hour than avoided
# export — the latter only saves the spread between the two tariffs.
#
# An earlier default of 50 came from a simulation that predated the evidence
# gate, which damps a good deal on its own. Applies upward only; see
# TRIM_FACTOR_DOWN above.

# Only start a new direction while the battery is genuinely idle. The measured
# power is the weaker of the two tests: an inverter draws its own standby power
# continuously, so the pack never reads zero. A 3000 Mix AC+ idles at roughly
# 41 W of discharge, which a 30 W band reads as "still running" forever.
# The primary test is therefore whether the controller is commanding anything.
CONTROL_DEADBAND = 60           # W

# Ignore adjustments smaller than this, to avoid pointless writes.
CONTROL_MIN_STEP = 10           # W

# The same threshold for the trim loop, which runs once per meter sample
# instead of once per battery poll. It is wider on purpose: at ten times the
# rate, a 10 W threshold turns ordinary household flicker into a continuous
# stream of writes. A quiet house produces no writes at all, because the trim
# only acts on a deviation it has not already corrected.
CONTROL_MIN_STEP_FAST = 25      # W

# Settling time after a direction change, before the controller acts again.
# Expressed in seconds rather than cycles: the reading that matters is how long
# the device has had to respond, which does not change when the polling
# interval does. Counting cycles instead made the wait 10 s at a 5 s interval
# and 20 s at a 10 s interval, for no reason related to the hardware.
CONTROL_DIRECTION_HOLD_SECONDS = 10

# The trim loop must not act on a meter reading that cannot yet contain the
# effect of its own last write. Two cheap tests do that, and both are needed.
#
# Observed on hardware: two consecutive samples both reported 318 W, and the
# loop applied a correction for each, adding 156 W twice for one deviation.
# That is the double-correction the module docstring warns about, produced
# here by the meter's own 0.4-1.1 s delay rather than by the loop rate.
#
# A reading fetched before the write cannot contain it, and a reading whose
# value has not moved since the last correction is the same evidence twice.
# Gating on elapsed time instead would have cost responsiveness on the falling
# edge, where every sample genuinely differs and every one of them matters.
TRIM_MIN_GRID_CHANGE = 1.0      # W, below this the sample counts as unchanged

# And the harder half of the same problem. "Fetched after the write" is not
# the same as "contains the write": the meter reports 0.4-1.1 s behind, so a
# sample pulled half a second after a correction still describes the world
# before it. The loop therefore stands still until a sample is late enough
# that it must have caught up.
TRIM_SETTLE_SECONDS = 1.5

# A fixed wait is not enough on its own, because the time an effect needs to
# show up depends on how big the correction was. Observed on hardware:
#
#   18:41:30.695  16W -> 1775W  | grid=2204W   (delta +1759)
#   18:41:32.332  1775W -> 3000W | grid=2207W   (delta +1225, hit the ceiling)
#
# 1.637 s apart, so the settle window had passed, and the grid had moved 3 W,
# so the unchanged-value test passed too. But 3 W is not a response to a
# 1759 W command: the write had not landed. The loop doubled its correction
# and wound the limit up to the device ceiling for a 2200 W load.
#
# So the loop also waits for a response proportional to what it asked for: the
# grid must have moved by at least this fraction of the last correction.
# Small corrections resume almost at once, large ones wait as long as they
# need to. The ceiling below stops that becoming a deadlock when the device
# genuinely cannot answer — at a power limit, or with the load moving to
# cancel the correction exactly.
TRIM_RESPONSE_FRACTION = 0.25
TRIM_RESPONSE_MAX_WAIT = 5.0    # seconds

# Meter data older than this is not trusted for closed-loop control. Until
# v1.1.0 this constant was declared and never read: the controller ran on the
# battery poll and had no timestamp to compare against. The trim loop runs on
# the meter itself, so the age is now both knowable and load-bearing.
METER_MAX_AGE = 60              # seconds

