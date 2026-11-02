"""A simulated Zendure battery: the device model behind a clone (v1.7.1).

The model copies the report of a real battery once — product, firmware,
maxima, SOC limits, every static property — and from then on moves the
dynamic fields itself. It answers what the local API answers, so the
integration's own coordinator and controller run on it unchanged.

Timings, as measured on a SolarFlow 3000 Mix AC+ and in Zendure's own replay
(15 Hz):

    a written limit is in the device after          1.8 s
    from standstill the first power flows after     ~8 s from the write
    the power rises at                              200 W/s
    a stop is complete after                        ~3 s from the write
    conversion, both ways                           95 %
    draw at rest                                    29 W with smartMode 1, 5 W with 0

The model needs no task of its own: it advances on every read and write by
the time that has passed.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable

SETPOINT_DELAY_S = 1.8      # a write is in the device after this
START_DELAY_S = 6.2         # from standstill: first power this long after the setpoint
RAMP_UP_W_S = 200.0
RAMP_DOWN_W_S = 2500.0      # a 3 kW stop in ~1.2 s after the setpoint: ~3 s from the write
EFFICIENCY = 0.95
REST_DRAW_FLAG_W = 29.0     # cells at rest with smartMode 1 (backup closed)
REST_DRAW_W = 5.0           # cells at rest with smartMode 0 (backup closed)
FLOW_W = 20.0               # below this the inverter counts as standing still

SOC_LIMIT_UPPER = 1
SOC_LIMIT_LOWER = 2

_DYNAMIC = ("outputLimit", "inputLimit", "acMode", "smartMode")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class SimDevice:
    """One simulated battery, a copy of a real one."""

    def __init__(
        self,
        source_report: dict[str, Any],
        serial: str,
        soc: float,
        capacity_kwh: float,
        clock: Callable[[], float] = time.monotonic,
        extra_delay: Callable[[], float] = lambda: 0.0,
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
        if self.power > 0:
            dc = self.power / EFFICIENCY
        elif self.power < 0:
            dc = self.power * EFFICIENCY
        else:
            dc = REST_DRAW_FLAG_W if _int(self.props.get("smartMode")) == 1 else REST_DRAW_W
        self.soc -= dc * dt / 3600.0 / self.capacity_wh * 100.0
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
        if abs(t) < FLOW_W:
            self.flow_at = 0.0
        if self.flow_at and now < self.flow_at:
            now = min(until, self.flow_at)
        moving = until - now
        if moving > 0:
            rate = RAMP_UP_W_S if abs(t) > abs(self.power) else RAMP_DOWN_W_S
            delta = rate * moving
            self.power = min(t, self.power + delta) if self.power < t else max(t, self.power - delta)
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
        if self.power > 0:
            dc = self.power / EFFICIENCY
        elif self.power < 0:
            dc = -self.power * EFFICIENCY
        else:
            dc = 0.0
        share = dc / max(1, len(self.packs))
        for pack in self.packs:
            pack["socLevel"] = int(round(self.soc))
            pack["power"] = int(round(share))
            pack["state"] = p["packState"]
