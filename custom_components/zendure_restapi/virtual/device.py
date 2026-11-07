"""A simulated Zendure battery: the device model behind a clone (v1.7.1).

The model copies the report of a real battery once — product, firmware,
maxima, SOC limits, every static property — and from then on moves the
dynamic fields itself. It answers what the local API answers, so the
integration's own coordinator and controller run on it unchanged.

Timings, as measured on a SolarFlow 3000 Mix AC+ and in Zendure's own replay
(15 Hz):

    a written limit is in the device after          1.8 s
    from standstill the first power flows after     ~8 s from the write
    from standstill full power follows within       ~2 s after that (v1.7.2)
    a running battery changes its power at          200 W/s
    a stop is complete after                        ~3 s from the write
    conversion, discharging (v1.7.4)           DC = 1.027 x AC + 32.9 W
    conversion, charging (v1.7.4)              DC = 0.967 x AC - 44 W
    draw at rest                                    29 W with smartMode 1, 5 W with 0

Energy per percent (v1.7.2), measured on the same device over full
cycles: discharging takes the usable capacity over the range it was measured
on (7.97 kWh over 100 -> 5 %, 84 Wh per %), charging that divided by the
round trip efficiency of the cells (0.96, so 87.5 Wh per %). Before v1.7.2 the
model took the capacity over 100 % both ways and ran 4 % fast discharging and
8 % fast charging. Without a measurement it still does.

The model needs no task of its own: it advances on every read and write by
the time that has passed.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

SETPOINT_DELAY_S = 1.8      # a write is in the device after this
START_DELAY_S = 6.2         # from standstill: first power this long after the setpoint
RAMP_UP_W_S = 200.0         # a running battery
RAMP_UP_COLD_W_S = 1500.0   # from standstill: 0 to 3 kW in ~2 s, measured (v1.7.2)
RAMP_DOWN_W_S = 2500.0      # a 3 kW stop in ~1.2 s after the setpoint: ~3 s from the write
# Conversion as a fixed plus a proportional part (v1.7.4); until then one
# 95 % both ways, which at 150 W gave ~8 W of loss against ~37 W measured.
# Discharging: least squares over 33 still pairs on a 3000 Mix AC+ (README2,
# "Separating the fixed and proportional parts"): 37 W at 31 W AC, 39 W at
# 131 W, ~111 W at 3 kW. Charging: a fixed ~44 W from a second, independent
# series on the same model (published measurements; at 50 W AC next to nothing reached
# the pack, measured on the DC side), the proportional part fitted to our own
# 3 kW point (2856 W DC of 2999 W AC, three windows). Under ~46 W AC the
# converter takes more than comes in, and the cells give the rest.
DISCHARGE_FACTOR = 1.027
DISCHARGE_FIXED_W = 32.9
CHARGE_FACTOR = 0.967
CHARGE_FIXED_W = 44.0
REST_DRAW_FLAG_W = 29.0     # cells at rest with smartMode 1 (backup closed)
REST_DRAW_W = 5.0           # cells at rest with smartMode 0 (backup closed)
FLOW_W = 20.0               # below this the inverter counts as standing still

SOC_LIMIT_UPPER = 1
SOC_LIMIT_LOWER = 2

_DYNAMIC = ("outputLimit", "inputLimit", "acMode", "smartMode")


def cells(ac: float) -> float:
    """Power at the cells for a power at the AC port (v1.7.4).

    Positive while discharging (drawn from the cells), negative while charging
    (stored in them); a charge too small for the converter's own draw comes out
    positive: the cells give the difference.
    """
    if ac > 0:
        return DISCHARGE_FACTOR * ac + DISCHARGE_FIXED_W
    if ac < 0:
        return -(CHARGE_FACTOR * -ac - CHARGE_FIXED_W)
    return 0.0


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class VirtualDevice:
    """One simulated battery, a copy of a real one."""

    def __init__(
        self,
        source_report: dict[str, Any],
        serial: str,
        soc: float,
        capacity_kwh: float,
        clock: Callable[[], float] = time.monotonic,
        extra_delay: Callable[[], float] = lambda: 0.0,
        wh_per_percent: float | None = None,
        charge_efficiency: float = 1.0,
    ) -> None:
        self._clock = clock
        # Slower than the measured device by this many seconds per write: a
        # clone that answers later, as another unit or firmware might.
        self._extra_delay = extra_delay
        report = copy.deepcopy(source_report or {})
        props = report.get("properties")
        if not isinstance(props, dict):
            props = {}
        self.props: dict[str, Any] = dict(props)
        packs = report.get("packData")
        self.packs: list[dict[str, Any]] = [dict(p) for p in packs if isinstance(p, dict)] if isinstance(packs, list) else []
        if not self.packs:
            self.packs = [{"sn": "B" + serial[-9:], "packType": 70}]
        for index, pack in enumerate(self.packs, start=1):
            pack["sn"] = f"{str(pack.get('sn') or 'B')[:4]}SIM{serial[-4:]}{index}"
        self.serial = serial
        self.product = report.get("product") or "solarFlow3000MixAC+"
        self.version = report.get("version")
        self.capacity_wh = max(100.0, float(capacity_kwh) * 1000.0)
        # Wh of the cells per percent (v1.7.2): discharging, and charging,
        # which needs more for the same percent by the loss in the cells.
        per = float(wh_per_percent) if wh_per_percent else self.capacity_wh / 100.0
        self.wh_per_percent_out = max(1.0, per)
        efficiency = max(0.5, min(1.0, float(charge_efficiency or 1.0)))
        self.wh_per_percent_in = self.wh_per_percent_out / efficiency

        # Start still, in the state a battery is in after standby.
        for key, value in (("outputLimit", 0), ("inputLimit", 0), ("smartMode", 0)):
            self.props[key] = value
        self.props.setdefault("acMode", 2)
        self.props.setdefault("minSoc", 50)
        self.props.setdefault("socSet", 1000)
        self.props["solarInputPower"] = 0
        self.soc = max(0.0, min(100.0, float(soc)))
        self.power = 0.0                 # AC, positive while discharging
        self.pending: list[tuple[float, dict[str, Any]]] = []
        self.flow_at = 0.0
        self.cold = False                # rising from standstill
        self.writes = 0
        self._last = self._clock()
        self._refresh()

    # ── What the local API offers ───────────────────────────────────────

    def report(self) -> dict[str, Any]:
        self.step()
        # A real device counts its reports; one that starts again has restarted.
        if isinstance(self.props.get("messageId"), int):
            self.props["messageId"] += 1
        return {
            "sn": self.serial,
            "product": self.product,
            "version": self.version,
            "properties": dict(self.props),
            "packData": [dict(p) for p in self.packs],
        }

    def write(self, properties: dict[str, Any]) -> None:
        self.step()
        self.writes += 1
        accepted = {k: v for k, v in properties.items() if k in self.props or k in _DYNAMIC}
        try:
            extra = max(0.0, float(self._extra_delay()))
        except (TypeError, ValueError):
            extra = 0.0
        self.pending.append((self._clock() + SETPOINT_DELAY_S + extra, accepted))

    # ── The model ───────────────────────────────────────────────────────

    def reset(self, soc: float) -> None:
        """Back to the start: still, both limits 0, at this state of charge.

        smartMode is left as it is: a flag that drops without a write is how
        the integration recognises a restart of a real device (v1.6.8), and a
        reset is not one.
        """
        self.pending.clear()
        self.props.update({"outputLimit": 0, "inputLimit": 0})
        self.power = 0.0
        self.flow_at = 0.0
        self.cold = False
        self.soc = max(0.0, min(100.0, float(soc)))
        self._last = self._clock()
        self._refresh()

    def target(self) -> float:
        p = self.props
        out, inp = _int(p.get("outputLimit")), _int(p.get("inputLimit"))
        wanted = float(out) if out > 0 else (-float(inp) if inp > 0 else 0.0)
        if wanted > 0 and self.soc <= _int(p.get("minSoc"), 50) / 10:
            return 0.0
        if wanted < 0 and self.soc >= _int(p.get("socSet"), 1000) / 10:
            return 0.0
        cap_out = _int(p.get("inverseMaxPower"), 0)
        cap_in = _int(p.get("chargeMaxLimit"), 0)
        if wanted > 0 and cap_out > 0:
            wanted = min(wanted, float(cap_out))
        if wanted < 0 and cap_in > 0:
            wanted = max(wanted, -float(cap_in))
        return wanted

    def step(self) -> None:
        now = self._clock()
        # In slices, so a long gap between two reads still applies writes in order.
        while True:
            due = self.pending[0][0] if self.pending else None
            until = min(now, due) if due is not None else now
            self._advance(until)
            if due is not None and due <= now:
                self.props.update(self.pending.pop(0)[1])
                continue
            break
        self._refresh()

    def _advance(self, until: float) -> None:
        dt = until - self._last
        if dt <= 0:
            return
        # Cells over this slice, at the power it starts with: discharge draws
        # more than the house gets, charge stores less.
        if self.power != 0:
            dc = cells(self.power)
        else:
            dc = REST_DRAW_FLAG_W if _int(self.props.get("smartMode")) == 1 else REST_DRAW_W
        per = self.wh_per_percent_out if dc > 0 else self.wh_per_percent_in
        self.soc -= dc * dt / 3600.0 / per
        self.soc = max(0.0, min(100.0, self.soc))

        t = self.target()
        now = self._last
        # The other way round: down to zero first, then a start from standstill.
        if self.power * t < 0:
            need = abs(self.power) / RAMP_DOWN_W_S
            if now + need >= until:
                self.power -= (1 if self.power > 0 else -1) * RAMP_DOWN_W_S * (until - now)
                self._last = until
                return
            now += need
            self.power = 0.0
            self.flow_at = 0.0
        if abs(self.power) < FLOW_W and abs(t) >= FLOW_W and self.flow_at == 0.0:
            self.flow_at = now + START_DELAY_S
            self.cold = True
        if abs(t) < FLOW_W:
            self.flow_at = 0.0
            self.cold = False
        if self.flow_at and now < self.flow_at:
            now = min(until, self.flow_at)
        moving = until - now
        if moving > 0:
            if abs(t) > abs(self.power):
                rate = RAMP_UP_COLD_W_S if self.cold else RAMP_UP_W_S
            else:
                rate = RAMP_DOWN_W_S
            delta = rate * moving
            self.power = min(t, self.power + delta) if self.power < t else max(t, self.power - delta)
            # A start from standstill ends once it has reached what was asked;
            # a later rise is a running battery's.
            if self.cold and abs(self.power) >= abs(t) - 1:
                self.cold = False
        if abs(t) < 1 and abs(self.power) < 30:
            self.power = 0.0
        self._last = until

    def _refresh(self) -> None:
        p = self.props
        p["electricLevel"] = int(round(self.soc))
        if self.soc >= _int(p.get("socSet"), 1000) / 10:
            p["socLimit"] = SOC_LIMIT_UPPER
        elif self.soc <= _int(p.get("minSoc"), 50) / 10:
            p["socLimit"] = SOC_LIMIT_LOWER
        else:
            p["socLimit"] = 0
        ac = round(self.power)
        p["outputHomePower"] = max(0, ac)
        p["gridInputPower"] = max(0, -ac)
        p["packInputPower"] = max(0, ac)
        p["outputPackPower"] = max(0, -ac)
        p["packState"] = 2 if ac > 5 else (1 if ac < -5 else 0)
        dc = abs(cells(self.power)) if self.power != 0 else 0.0
        share = dc / max(1, len(self.packs))
        for pack in self.packs:
            pack["socLevel"] = int(round(self.soc))
            pack["power"] = int(round(share))
            pack["state"] = p["packState"]
