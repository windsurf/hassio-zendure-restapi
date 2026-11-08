"""A simulated Zendure battery behind the zenSDK local API, on a bad link.

Serves what a SolarFlow serves on the LAN:

    GET  /properties/report
    POST /properties/write
    GET  /rpc?method=...      POST /rpc

The battery itself is the integration's own model (virtual/device.py), so a
written limit moves power, SOC and the pack values the way the real device
was measured to.

On top of that a share of requests fails the way a device at the edge of the
WiFi does, each one logged:

    drop      the connection closes without an answer
    hang      no answer for SIM_HANG_SECONDS, then the connection closes
    truncate  the answer stops halfway through the JSON

The rates come from the environment and can be changed while running:

    curl 'http://localhost:8080/sim?drop=0.5&hang=0&truncate=0'
    curl  http://localhost:8080/sim            # shows the current settings

Standard library only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The battery model: SIM_MODEL, or device.py next to this file, or the one in
# the checkout this file lives in.
_here = Path(__file__).resolve().parent
_model = next(
    (p for p in (
        Path(os.environ["SIM_MODEL"]) if os.environ.get("SIM_MODEL") else None,
        _here / "device.py",
        _here.parent.parent / "custom_components" / "zendure_restapi" / "virtual" / "device.py",
    ) if p is not None and p.is_file()),
    None,
)
if _model is None:
    sys.exit("battery model device.py not found; set SIM_MODEL")
_spec = importlib.util.spec_from_file_location("device", _model)
device_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(device_mod)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


SERIAL = os.environ.get("SIM_SERIAL", "HOA1SIM00000001")

# Static properties of a SolarFlow 2400 AC-like battery; the model moves the
# dynamic ones.
SOURCE_REPORT = {
    "product": "solarFlow2400AC",
    "version": "sim",
    "properties": {
        "messageId": 1,
        "acMode": 2,
        "inputLimit": 0,
        "outputLimit": 0,
        "smartMode": 0,
        "minSoc": 100,
        "socSet": 1000,
        "socStatus": 0,
        "faultLevel": 0,
        "chargeMaxLimit": 2400,
        "inverseMaxPower": 2400,
        "gridReverse": 1,
        "gridStandard": 0,
        "heatState": 0,
        "hyperTmp": 2981,
        "remainOutTime": 0,
        "BatVolt": 5120,
    },
    "packData": [{"sn": "CO4ESIM0001", "packType": 300, "maxVol": 334, "minVol": 332, "batcur": 0}],
}

faults = {
    "drop": _env_float("SIM_DROP_RATE", 0.15),
    "hang": _env_float("SIM_HANG_RATE", 0.05),
    "truncate": _env_float("SIM_TRUNCATE_RATE", 0.05),
}
HANG_SECONDS = _env_float("SIM_HANG_SECONDS", 10)
counts = {"requests": 0, "drop": 0, "hang": 0, "truncate": 0, "writes": 0}

lock = threading.Lock()
battery = device_mod.VirtualDevice(
    SOURCE_REPORT,
    SERIAL,
    soc=_env_float("SIM_SOC", 60),
    capacity_kwh=_env_float("SIM_CAPACITY_KWH", 8.0),
)


def log(message: str, *args) -> None:
    print(time.strftime("%H:%M:%S"), message % args, file=sys.stderr, flush=True)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: D102 - quieter than the default
        pass

    # ── Faults ───────────────────────────────────────────────────────────

    def _fault(self) -> str | None:
        """Pick a fault for this request, or None to answer normally."""
        roll = random.random()
        edge = 0.0
        for name in ("drop", "hang", "truncate"):
            edge += max(0.0, faults[name])
            if roll < edge:
                return name
        return None

    def _answer(self, body: dict, fault: str | None) -> None:
        data = json.dumps(body, separators=(",", ":")).encode()
        if fault == "drop":
            self.close_connection = True
            return
        if fault == "hang":
            time.sleep(HANG_SECONDS)
            self.close_connection = True
            return
        if fault == "truncate":
            # No Content-Length: the client reads until the close and gets half.
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data[: max(1, len(data) // 2)])
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method: str, body: dict | None) -> None:
        url = urlparse(self.path)

        if url.path == "/sim":
            # The control endpoint never fails.
            for key, values in parse_qs(url.query).items():
                if key in faults:
                    try:
                        faults[key] = float(values[-1])
                    except ValueError:
                        pass
            log("faults now %s", faults)
            self._answer({"faults": faults, "counts": counts}, None)
            return

        with lock:
            counts["requests"] += 1
        fault = self._fault()
        what = f"{method} {url.path}"
        if body and "properties" in body:
            what += " " + json.dumps(body["properties"], separators=(",", ":"))

        if url.path == "/properties/report" and method == "GET":
            with lock:
                answer = battery.report()
        elif url.path == "/properties/write" and method == "POST":
            if body is None or body.get("sn") != SERIAL or not isinstance(body.get("properties"), dict):
                self.send_error(400, "bad write")
                return
            # Every fault here loses the reply, not the request: the write is
            # applied, and the integration's retry sends the same value again.
            # That is the harder case on a real link, and harmless only if a
            # repeated write is.
            with lock:
                battery.write(body["properties"])
                counts["writes"] += 1
            answer = {"code": 200, "success": True}
        elif url.path == "/rpc":
            answer = {}
        else:
            self.send_error(404)
            return

        if fault:
            with lock:
                counts[fault] += 1
            log("FAULT %-8s %s", fault, what)
        else:
            log("ok       %s", what)
        self._answer(answer, fault)

    def do_GET(self):  # noqa: N802
        self._handle("GET", None)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            self.send_error(400, "invalid JSON")
            return
        self._handle("POST", body if isinstance(body, dict) else None)


if __name__ == "__main__":
    port = int(os.environ.get("SIM_LISTEN_PORT", 80))
    log("Zendure simulator %s on port %d, faults %s", SERIAL, port, faults)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
