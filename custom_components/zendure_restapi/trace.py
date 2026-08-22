"""One CSV row per meter sample, for offline comparison of control regimes.

The recorder is deliberately dumb. It scores nothing, averages nothing and
decides nothing: it writes down what was measured and what was commanded, once
per meter sample, and leaves every judgement to be made afterwards on the file.

That split is the point. A band, an error in watt-hours, a recovery time after
a load step — those definitions are still being argued about, and a definition
baked into Python is one that costs a release to change. A definition applied
to a recorded trace costs a re-run of a script.

Because it records regardless of who is steering, the same file format covers
both regimes worth comparing: this controller in a smart mode, and the device's
own energy manager while the operation mode sits in ``standby``. Whoever moved
a limit is a column, not a separate feature.

Recording is started and stopped by one switch, and by nothing else. An
earlier draft hung it on the debug log level, which needed no new entity but
turned out to have no off: the level cannot be lowered again without a restart
or a reload, so every recording ran until the next restart and the file was
never closed. A second mechanism would also be a second answer to the same
question, which is the shape of bug this codebase keeps finding.

The switch is off after a restart, whatever it was before. A measurement that
silently resumes is worse than one you have to start yourself.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import TRACE_DIR, TRACE_FLUSH_ROWS, TRACE_MAX_ROWS

_LOGGER = logging.getLogger(__name__)

# Every column is either measured or commanded. Nothing here is derived, and
# nothing is rounded beyond what the device itself reports, so a mistake made
# while scoring can always be corrected without re-running the night.
COLUMNS = (
    "ts",                # local time, milliseconds
    "mode",              # operation mode, 'standby' while the device steers
    "status",            # controller status
    "reason",            # why that status
    "grid_w",            # meter reading accepted for control, empty if rejected
    "grid_raw_w",        # the same reading before the sanity and age checks
    "battery_ac_w",      # outputHomePower - gridInputPower, positive discharging
    "pack_dc_w",         # packInputPower - outputPackPower, positive discharging
    "soc",               # electricLevel
    "input_limit",       # charge limit as the device reports it
    "output_limit",      # discharge limit as the device reports it
    "ac_mode",           # 1 charge, 2 discharge
    "smart_mode",        # RAM storage flag
    "battery_age_s",     # age of the battery payload at this sample
    "writer",            # none | mode | trim | foreign
    "trim_direction",    # direction the trim loop is allowed to work in
    "pending_w",         # ordered and not yet seen back
    "trim_writes",       # cumulative, this controller
    "foreign_writes",    # cumulative, everything else that moved a limit
)


class ZendureTrace:
    """Buffers trace rows and flushes them to a CSV file."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._path: Path | None = None
        self._buffer: list[list[Any]] = []
        self._rows = 0
        self._enabled = False
        # A write failure stops the recorder rather than logging once a second.
        # Switching off and on again is the retry.
        self._failed = False
        self._listeners: list[Any] = []

    @property
    def wanted(self) -> bool:
        """Whether a recording should be running right now."""
        return self._enabled and not self._failed

    @property
    def active(self) -> bool:
        """Whether a file is currently open."""
        return self._path is not None

    # ── Switch ───────────────────────────────────────────────────────────

    async def async_start(self) -> None:
        """Begin a new recording. Always a new file, never an append."""
        self._failed = False
        self._enabled = True
        if self.active:
            # Switching on while a file is open would silently continue it.
            await self.async_close()
        await self._async_open()
        self._notify()

    async def async_stop(self) -> None:
        """End the recording: flush what is buffered and release the file."""
        self._enabled = False
        await self.async_close()
        self._notify()

    @callback
    def add_listener(self, listener: Any) -> Any:
        """Let the switch entity follow the recorder's own state."""
        self._listeners.append(listener)

        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    @property
    def path(self) -> str | None:
        """The file being written, for the status attributes."""
        return str(self._path) if self._path is not None else None

    @property
    def rows(self) -> int:
        """Rows written to the current file, flushed ones only."""
        return self._rows

    # ── Recording ────────────────────────────────────────────────────────

    async def async_sample(self, values: dict[str, Any]) -> None:
        """Record one row. Never raises: a recorder must not stop a loop."""
        if not self.wanted:
            if self.active:
                await self.async_close()
            return

        if not self.active:
            # Only reachable after a rollover; a switch-on opens the file
            # itself so that a failure is reported on the action that caused it.
            if not await self._async_open():
                return

        self._buffer.append([values.get(column, "") for column in COLUMNS])
        if len(self._buffer) >= TRACE_FLUSH_ROWS:
            await self._async_flush()

        if self._rows >= TRACE_MAX_ROWS:
            # Roll over rather than grow without bound. The next sample opens a
            # fresh file, so a recording left on for a week is a series of
            # files instead of one that no spreadsheet will open.
            await self.async_close()
            self._notify()

    async def async_close(self) -> None:
        """Flush and close the current file, if there is one."""
        if not self.active:
            return
        await self._async_flush()
        _LOGGER.info("Trace recording closed: %s rows in %s", self._rows, self._path)
        self._path = None

    # ── File handling ────────────────────────────────────────────────────

    async def _async_open(self) -> bool:
        started = dt_util.now()
        directory = Path(self.hass.config.path(TRACE_DIR))
        path = directory / f"trace_{started:%Y%m%d_%H%M%S}.csv"
        try:
            await self.hass.async_add_executor_job(self._create, directory, path)
        except OSError as err:
            _LOGGER.warning("Trace recording disabled, cannot write %s: %s", path, err)
            self._failed = True
            self._enabled = False
            return False
        self._path = path
        self._rows = 0
        _LOGGER.info("Trace recording to %s", path)
        return True

    async def _async_flush(self) -> None:
        if not self._buffer or self._path is None:
            return
        rows = self._buffer
        self._buffer = []
        try:
            await self.hass.async_add_executor_job(self._append, self._path, rows)
        except OSError as err:
            _LOGGER.warning("Trace recording stopped, cannot append: %s", err)
            self._failed = True
            self._enabled = False
            self._path = None
            self._notify()
            return
        self._rows += len(rows)

    @staticmethod
    def _create(directory: Path, path: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(COLUMNS)

    @staticmethod
    def _append(path: Path, rows: list[list[Any]]) -> None:
        with path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerows(rows)
