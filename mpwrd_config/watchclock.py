from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

from mpwrd_config.system import CommandResult, _run


DEFAULT_THRESHOLD_SECONDS = 7 * 24 * 60 * 60
DEFAULT_LOGFILE = Path("/var/log/time_change.log")
DEFAULT_LAST_TIME_FILE = Path("/tmp/last_time")


@dataclass
class WatchclockResult:
    returncode: int
    stdout: str


def _log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.ctime()} - {message}\n")


def run_watchclock(
    threshold_seconds: int = DEFAULT_THRESHOLD_SECONDS,
    logfile: Path = DEFAULT_LOGFILE,
    last_time_file: Path = DEFAULT_LAST_TIME_FILE,
    interval_seconds: int = 30,
) -> WatchclockResult:
    logfile.parent.mkdir(parents=True, exist_ok=True)
    if not logfile.exists():
        logfile.write_text("", encoding="utf-8")
    if not last_time_file.exists():
        last_time_file.write_text(str(int(time.time())), encoding="utf-8")
    if not os.access(logfile, os.W_OK):
        return WatchclockResult(returncode=1, stdout=f"ERROR: Log file {logfile} is not writable")
    if not os.access(last_time_file, os.W_OK):
        return WatchclockResult(returncode=1, stdout=f"ERROR: Last time file {last_time_file} is not writable")

    while True:
        if _run(["systemctl", "is-active", "--quiet", "meshtasticd"]).returncode != 0:
            time.sleep(interval_seconds)
            continue
        new_time = int(time.time())
        if not os.access(last_time_file, os.R_OK):
            return WatchclockResult(returncode=1, stdout=f"ERROR: Last time file {last_time_file} is not readable")
        try:
            old_time = int(last_time_file.read_text(encoding="utf-8").strip())
        except ValueError:
            old_time = new_time
        time_diff = new_time - old_time
        if abs(time_diff) >= threshold_seconds:
            _log(logfile, f"Large time change detected ({time_diff} seconds), restarting meshtasticd")
            _run(["systemctl", "restart", "meshtasticd"])
        if os.access(last_time_file, os.W_OK):
            last_time_file.write_text(str(new_time), encoding="utf-8")
        else:
            return WatchclockResult(returncode=1, stdout=f"ERROR: Failed to write to {last_time_file}")
        time.sleep(interval_seconds)
