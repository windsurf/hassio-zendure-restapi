"""Constants for the Zendure RestAPI integration."""

DOMAIN = "zendure_restapi"
INTEGRATION_VERSION = "0.9.8"
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
OPT_START_DISCHARGE_ABOVE = "start_discharge_above"
OPT_START_CHARGE_BELOW = "start_charge_below"
OPT_MIN_CHARGE_POWER = "min_charge_power"
OPT_MIN_DISCHARGE_POWER = "min_discharge_power"
OPT_CHARGE_BUFFER = "charge_buffer"
OPT_DISCHARGE_BUFFER = "discharge_buffer"
OPT_SOC_PROTECTION = "soc_protection"

DEFAULTS = {
    OPT_OPERATION_MODE: MODE_STANDBY,   # hands off until a mode is chosen
    OPT_MANUAL_POWER: 0,
    OPT_MAX_CHARGE_POWER: 800,
    OPT_MAX_DISCHARGE_POWER: 800,
    OPT_START_DISCHARGE_ABOVE: 30,      # W of import before discharging starts
    OPT_START_CHARGE_BELOW: -50,        # W of export before charging starts
    OPT_MIN_CHARGE_POWER: 0,            # floor in every smart mode, 0 = off
    OPT_MIN_DISCHARGE_POWER: 0,         # floor in every smart mode, 0 = off
    OPT_CHARGE_BUFFER: 5,               # W of import to aim for while charging
    OPT_DISCHARGE_BUFFER: 5,            # W of import to aim for while discharging
    OPT_SOC_PROTECTION: True,
}

# ── Controller tuning ────────────────────────────────────────────────────
# The first step is deliberately undershot: the device's actual response is
# not yet known, so committing the full correction invites overshoot. Once a
# direction is established the controller balances at the full amount.
CONTROL_FACTOR_START = 0.75
CONTROL_FACTOR_BALANCE = 1.00

# Only start a new direction while the battery is genuinely idle. The measured
# power is the weaker of the two tests: an inverter draws its own standby power
# continuously, so the pack never reads zero. A 3000 Mix AC+ idles at roughly
# 41 W of discharge, which a 30 W band reads as "still running" forever.
# The primary test is therefore whether the controller is commanding anything.
CONTROL_DEADBAND = 60           # W

# Ignore adjustments smaller than this, to avoid pointless writes.
CONTROL_MIN_STEP = 10           # W

# Settling time after a direction change, before the controller acts again.
# Expressed in seconds rather than cycles: the reading that matters is how long
# the device has had to respond, which does not change when the polling
# interval does. Counting cycles instead made the wait 10 s at a 5 s interval
# and 20 s at a 10 s interval, for no reason related to the hardware.
CONTROL_DIRECTION_HOLD_SECONDS = 10

# Meter data older than this is not trusted for closed-loop control.
METER_MAX_AGE = 60              # seconds

