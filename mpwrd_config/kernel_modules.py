from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mpwrd_config.system import CommandResult, _run


DEFAULT_MODULE_DIR = Path("/lib/modules")
BOOT_MODULES_PATH = Path("/etc/modules")


@dataclass
class KernelModuleResult:
    returncode: int
    stdout: str


@dataclass
class KernelModuleOverview:
    name: str
    loaded: bool
    boot: bool
    blacklisted: bool


def _resolve_module_dir() -> Path:
    release = _run(["uname", "-r"]).stdout.strip()
    if not release:
        return DEFAULT_MODULE_DIR
    return DEFAULT_MODULE_DIR / release


def list_boot_modules() -> KernelModuleResult:
    if not BOOT_MODULES_PATH.exists():
        return KernelModuleResult(returncode=0, stdout="none")
    lines = [
        line.strip()
        for line in BOOT_MODULES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return KernelModuleResult(returncode=0, stdout="none")
    return KernelModuleResult(returncode=0, stdout="\n".join(lines))


def list_active_modules() -> KernelModuleResult:
    result = _run(["lsmod"])
    if result.returncode != 0:
        return KernelModuleResult(returncode=1, stdout=result.stdout.strip())
    lines = [
        line.split()[0]
        for idx, line in enumerate(result.stdout.splitlines())
        if idx > 0 and line.strip()
    ]
    if not lines:
        return KernelModuleResult(returncode=0, stdout="none")
    return KernelModuleResult(returncode=0, stdout="\n".join(lines))


def module_info(name: str) -> KernelModuleResult:
    result = _run(["modinfo", name])
    output = result.stdout.strip()
    if not output:
        output = f"No module info found for {name}."
    return KernelModuleResult(returncode=result.returncode, stdout=output)


def enable_module(name: str) -> CommandResult:
    result = _run(["modprobe", name])
    if result.returncode != 0:
        return result
    if BOOT_MODULES_PATH.exists():
        lines = BOOT_MODULES_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    if name not in lines:
        lines.append(name)
        BOOT_MODULES_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return CommandResult(returncode=0, stdout=f"{name} enabled and set to load at boot.")


def disable_module(name: str) -> CommandResult:
    _run(["rmmod", name])
    if BOOT_MODULES_PATH.exists():
        lines = BOOT_MODULES_PATH.read_text(encoding="utf-8").splitlines()
        lines = [line for line in lines if line.strip() != name]
        BOOT_MODULES_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return CommandResult(returncode=0, stdout=f"{name} disabled and removed from boot list.")


def list_blacklisted_modules() -> KernelModuleResult:
    module_dir = _resolve_module_dir()
    if not module_dir.exists():
        return KernelModuleResult(returncode=1, stdout="module directory missing")
    paths = list(module_dir.glob("*.ko.blacklisted"))
    if not paths:
        paths = list(module_dir.rglob("*.ko.blacklisted"))
    modules = [path.name.replace(".ko.blacklisted", "") for path in paths]
    if not modules:
        return KernelModuleResult(returncode=0, stdout="none")
    return KernelModuleResult(returncode=0, stdout="\n".join(sorted(modules)))


def _parse_module_list(output: str) -> set[str]:
    return {
        line.strip()
        for line in output.splitlines()
        if line.strip() and line.strip().lower() != "none"
    }


def list_module_overview() -> list[KernelModuleOverview]:
    module_dir = _resolve_module_dir()
    paths = list(module_dir.glob("*.ko*"))
    if not paths:
        paths = list(module_dir.rglob("*.ko*"))
    names: list[str] = []
    seen: set[str] = set()
    for path in paths:
        name = path.name
        if ".ko" in name:
            name = name.split(".ko")[0]
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    names.sort()
    boot_set = _parse_module_list(list_boot_modules().stdout)
    active_set = _parse_module_list(list_active_modules().stdout)
    blacklist_set = _parse_module_list(list_blacklisted_modules().stdout)
    return [
        KernelModuleOverview(
            name=name,
            loaded=name in active_set,
            boot=name in boot_set,
            blacklisted=name in blacklist_set,
        )
        for name in names
    ]


def blacklist_module(name: str) -> CommandResult:
    module_dir = _resolve_module_dir()
    source = module_dir / f"{name}.ko"
    target = module_dir / f"{name}.ko.blacklisted"
    if source.exists():
        source.rename(target)
    disable_module(name)
    return CommandResult(returncode=0, stdout=f"{name} blacklisted.")


def unblacklist_module(name: str) -> CommandResult:
    module_dir = _resolve_module_dir()
    source = module_dir / f"{name}.ko.blacklisted"
    target = module_dir / f"{name}.ko"
    if source.exists():
        source.rename(target)
    return CommandResult(returncode=0, stdout=f"{name} un-blacklisted.")
