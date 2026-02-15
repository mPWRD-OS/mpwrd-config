from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from mpwrd_config.system import CommandResult, _run
from mpwrd_config.software_packages import (
    PackageActionResult,
    get_package_spec,
    list_package_specs,
    manage_full_control_conflicts as _manage_full_control_conflicts,
    package_license_text,
)


@dataclass
class PackageInfo:
    key: str
    name: str
    installed: bool
    options: str
    author: str | None = None
    description: str | None = None
    url: str | None = None
    service_name: str | None = None
    location: str | None = None
    license_name: str | None = None
    conflicts: str | None = None
    extra_actions: tuple[tuple[str, str], ...] = ()


def list_package_keys(package_dir=None) -> list[str]:
    return [spec.key for spec in list_package_specs()]


def package_installed(key: str, package_dir=None) -> bool:
    spec = get_package_spec(key)
    if spec.check_installed is None:
        return False
    return spec.check_installed()


def package_name(key: str, package_dir=None) -> str:
    return get_package_spec(key).name


def package_options(key: str, package_dir=None) -> str:
    return get_package_spec(key).options


def package_info(key: str, package_dir=None) -> PackageInfo:
    spec = get_package_spec(key)
    installed = spec.check_installed() if spec.check_installed else False
    service_name = " ".join(spec.service_names) if spec.service_names else None
    extra_actions: list[tuple[str, str]] = []
    for extra in spec.extra_actions:
        if extra.requires_installed and not installed:
            continue
        extra_actions.append((extra.key, extra.label))
    return PackageInfo(
        key=spec.key,
        name=spec.name,
        installed=installed,
        options=spec.options,
        author=spec.author,
        description=spec.description,
        url=spec.url,
        service_name=service_name,
        location=str(spec.location) if spec.location else None,
        license_name=spec.license_name,
        conflicts=spec.conflicts,
        extra_actions=tuple(extra_actions),
    )


def list_packages(package_dir=None) -> list[PackageInfo]:
    return [package_info(spec.key) for spec in list_package_specs()]


def run_action(
    key: str,
    action: str,
    package_dir=None,
    interactive: bool = True,
) -> PackageActionResult:
    spec = get_package_spec(key)
    action_map = {
        "-i": spec.install,
        "-u": spec.uninstall,
        "-g": spec.upgrade,
        "-a": spec.init,
        "-l": spec.run,
    }
    handler = action_map.get(action)
    if handler is None:
        for extra in spec.extra_actions:
            if action == f"-{extra.key}":
                return extra.handler(interactive)
        return PackageActionResult(returncode=1, output="Unsupported action.", user_message=None)
    return handler(interactive)


def service_action(key: str, action: str, package_dir=None) -> CommandResult:
    spec = get_package_spec(key)
    if not spec.service_names:
        return CommandResult(returncode=1, stdout="No service defined for this package.")
    action_map = {
        "-e": "enable",
        "-d": "disable",
        "-s": "stop",
        "-r": "restart",
    }
    outputs: list[str] = []
    returncode = 0
    if action == "-S":
        for service in spec.service_names:
            result = _run(["systemctl", "status", service])
            returncode = max(returncode, result.returncode)
            if result.stdout.strip():
                outputs.append(result.stdout.strip())
        return CommandResult(returncode=returncode, stdout="\n".join(outputs).strip())

    systemctl_action = action_map.get(action)
    if systemctl_action is None:
        return CommandResult(returncode=1, stdout="Unsupported service action.")
    for service in spec.service_names:
        result = _run(["systemctl", systemctl_action, service])
        returncode = max(returncode, result.returncode)
        if result.stdout.strip():
            outputs.append(result.stdout.strip())
    return CommandResult(returncode=returncode, stdout="\n".join(outputs).strip())


def license_text(key: str, package_dir=None) -> str:
    spec = get_package_spec(key)
    return package_license_text(spec)


def manage_full_control_conflicts(action: str) -> CommandResult:
    return _manage_full_control_conflicts(action)
