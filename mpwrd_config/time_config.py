from __future__ import annotations

from dataclasses import dataclass

from mpwrd_config.system import CommandResult, _run


@dataclass
class TimeResult:
    returncode: int
    stdout: str


def current_timezone() -> TimeResult:
    result = _run(["timedatectl", "show", "-p", "Timezone", "--value"])
    return TimeResult(returncode=result.returncode, stdout=result.stdout.strip())


def set_timezone(timezone: str) -> CommandResult:
    return _run(["timedatectl", "set-timezone", timezone])


def set_time(timespec: str) -> CommandResult:
    return _run(["timedatectl", "set-time", timespec])


def status() -> TimeResult:
    result = _run(["timedatectl", "status"])
    return TimeResult(returncode=result.returncode, stdout=result.stdout.strip())
