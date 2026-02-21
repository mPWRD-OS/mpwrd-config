from __future__ import annotations

import base64
import copy
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from google.protobuf.json_format import MessageToDict
from meshtastic import util as meshtastic_util
from meshtastic.mesh_interface import MeshInterface
from meshtastic.protobuf import channel_pb2, config_pb2, module_config_pb2
from meshtastic.tcp_interface import TCPInterface

from mpwrd_config.system import CommandResult, _run, _run_live

MESHTASTIC_HOST = os.environ.get("MESHTASTIC_HOST", "127.0.0.1")
MESHTASTIC_TIMEOUT_SEC = float(os.environ.get("MESHTASTIC_TIMEOUT_SEC", "60"))


MESHTASTIC_CONFIG_DIR = Path("/etc/meshtasticd/config.d")
MESHTASTIC_AVAILABLE_DIR = Path("/etc/meshtasticd/available.d")
MESHTASTIC_CONFIG_PATH = MESHTASTIC_CONFIG_DIR / "mpwrd_config.yaml"
MESHTASTIC_MAIN_CONFIG_PATH = Path("/etc/meshtasticd/config.yaml")
MESHTASTIC_REPO_LIST_DIR = Path("/etc/apt/sources.list.d")
MESHTASTIC_REPO_KEY_DIR = Path("/etc/apt/trusted.gpg.d")
MESHTASTIC_REPO_CHANNELS = {
    "beta": "network:Meshtastic:beta",
    "alpha": "network:Meshtastic:alpha",
    "daily": "network:Meshtastic:daily",
}
MESHTASTIC_PPA_CHANNELS = {
    "beta": "ppa:meshtastic/beta",
    "alpha": "ppa:meshtastic/alpha",
    "daily": "ppa:meshtastic/daily",
}

RADIO_CONFIG_MAP = {
    "lr1121_tcxo": "femtofox_LR1121_TCXO.yaml",
    "sx1262_tcxo": "femtofox_SX1262_TCXO.yaml",
    "sx1262_xtal": "femtofox_SX1262_XTAL.yaml",
    "lora-meshstick-1262": "lora-meshstick-1262.yaml",
}
RADIO_CONFIG_REVERSE = {value.lower(): key for key, value in RADIO_CONFIG_MAP.items()}

OPENSUSE_REPO_PATTERN = re.compile(r"download\.opensuse\.org/repositories/network:/Meshtastic:/([a-z]+)/", re.I)
PPA_REPO_PATTERN = re.compile(r"ppa\.launchpadcontent\.net/meshtastic/([a-z]+)/ubuntu", re.I)


def _needs_reset_failed(result: CommandResult) -> bool:
    if result.returncode == 0:
        return False
    text = (result.stdout or "").lower()
    markers = (
        "start request repeated too quickly",
        "start-limit-hit",
        "start of the service was attempted too often",
        "too often",
        "systemctl reset-failed",
    )
    return any(marker in text for marker in markers)


def _service_action_with_recovery(action: str) -> CommandResult:
    result = _run(["systemctl", action, "meshtasticd"])
    if action not in {"start", "restart"} or not _needs_reset_failed(result):
        return result

    reset = _run(["systemctl", "reset-failed", "meshtasticd"])
    retry = _run(["systemctl", action, "meshtasticd"])
    parts: list[str] = []
    if result.stdout.strip():
        parts.append(result.stdout.strip())
    if reset.stdout.strip():
        parts.append(reset.stdout.strip())
    if retry.stdout.strip():
        parts.append(retry.stdout.strip())
    if retry.returncode == 0:
        parts.append("Cleared failed state and retried.")
    return CommandResult(returncode=retry.returncode, stdout="\n".join(parts))


def _configured_lora_module() -> str:
    if not MESHTASTIC_MAIN_CONFIG_PATH.exists():
        return ""
    lines = MESHTASTIC_MAIN_CONFIG_PATH.read_text(encoding="utf-8").splitlines()
    in_lora = False
    lora_indent = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if re.match(r"^Lora\s*:\s*$", stripped):
            in_lora = True
            lora_indent = indent
            continue
        if in_lora and indent <= lora_indent:
            in_lora = False
        if in_lora and stripped.startswith("Module:"):
            return stripped.split(":", 1)[1].strip().lower()
    return ""


def _set_lora_module(module: str) -> CommandResult:
    if not MESHTASTIC_MAIN_CONFIG_PATH.exists():
        return CommandResult(returncode=1, stdout="Meshtastic config.yaml not found.")
    text = MESHTASTIC_MAIN_CONFIG_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    in_lora = False
    lora_indent = 0
    lora_idx: int | None = None
    module_idx: int | None = None
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if re.match(r"^Lora\s*:\s*$", stripped):
            in_lora = True
            lora_indent = indent
            lora_idx = idx
            continue
        if in_lora and indent <= lora_indent:
            in_lora = False
        if in_lora and stripped.startswith("Module:"):
            module_idx = idx
            break

    if module_idx is not None:
        lines[module_idx] = f"{' ' * (lora_indent + 2)}Module: {module}"
    elif lora_idx is not None:
        lines.insert(lora_idx + 1, f"{' ' * (lora_indent + 2)}Module: {module}")
    else:
        if lines and lines[-1].strip():
            lines.append("")
        lines.append("Lora:")
        lines.append(f"  Module: {module}")

    updated = "\n".join(lines)
    if text.endswith("\n"):
        updated += "\n"
    MESHTASTIC_MAIN_CONFIG_PATH.write_text(updated, encoding="utf-8")
    return CommandResult(returncode=0, stdout=f"Lora.Module set to {module}.")


def _render_qr_text_python(data: str) -> str | None:
    try:
        import qrcode
    except Exception:
        return None

    try:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=1,
            border=1,
        )
        qr.add_data(data)
        qr.make(fit=True)
        matrix = qr.get_matrix()
    except Exception:
        return None

    if not matrix:
        return None

    width = len(matrix[0])
    if len(matrix) % 2 == 1:
        matrix.append([False] * width)

    lines: list[str] = []
    for row in range(0, len(matrix), 2):
        top = matrix[row]
        bottom = matrix[row + 1]
        chars: list[str] = []
        for top_pixel, bottom_pixel in zip(top, bottom):
            if top_pixel and bottom_pixel:
                chars.append("█")
            elif top_pixel and not bottom_pixel:
                chars.append("▀")
            elif not top_pixel and bottom_pixel:
                chars.append("▄")
            else:
                chars.append(" ")
        lines.append("".join(chars).rstrip())

    return "\n".join(lines).rstrip()


def _bluetooth_controller_detected() -> bool:
    path = Path("/sys/class/bluetooth")
    if not path.exists():
        return False
    return any(path.glob("hci*"))


def _cpu_serial() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("serial"):
                _, value = line.split(":", 1)
                return value.strip()
    except FileNotFoundError:
        pass
    serial_path = Path("/sys/firmware/devicetree/base/serial-number")
    if serial_path.exists():
        return serial_path.read_text(encoding="utf-8").strip()
    return ""


def _machine_id() -> str:
    path = Path("/etc/machine-id")
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _generated_cpu_mac() -> str | None:
    identifier = _cpu_serial() or _machine_id()
    if not identifier:
        return None
    hex_only = re.sub(r"[^0-9a-fA-F]", "", identifier).lower()
    if len(hex_only) < 10:
        return None
    suffix = hex_only[-10:]
    raw = f"a2{suffix}"
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def mac_address_source_options() -> list[tuple[str, str]]:
    from mpwrd_config.system import list_ethernet_interfaces, list_wifi_interfaces

    options: list[tuple[str, str]] = []
    options.append(("auto", "Auto (eth → wifi → bluetooth → cpu)"))
    for iface in list_ethernet_interfaces():
        options.append((iface, f"Ethernet ({iface})"))
    for iface in list_wifi_interfaces():
        options.append((iface, f"Wi-Fi ({iface})"))
    label = "Bluetooth" if _bluetooth_controller_detected() else "Bluetooth (controller not detected)"
    options.append(("bluetooth", label))
    generated = _generated_cpu_mac()
    if generated:
        options.append(("cpu", f"CPU/machine-id ({generated})"))
    else:
        options.append(("cpu", "CPU/machine-id (unavailable)"))
    return options


def _config_value(key: str) -> str:
    if not MESHTASTIC_MAIN_CONFIG_PATH.exists():
        return ""
    value = ""
    for line in MESHTASTIC_MAIN_CONFIG_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith(f"{key}:"):
            value = stripped.split(":", 1)[1].strip()
            break
    return value


def mac_address_source() -> CommandResult:
    if not MESHTASTIC_MAIN_CONFIG_PATH.exists():
        return CommandResult(returncode=1, stdout="Meshtastic config.yaml not found.")
    source = _config_value("MACAddressSource")
    if source:
        return CommandResult(returncode=0, stdout=source)
    mac = _config_value("MACAddress")
    if mac:
        generated = _generated_cpu_mac()
        if generated and mac.lower() == generated.lower():
            return CommandResult(returncode=0, stdout="cpu")
        return CommandResult(returncode=0, stdout=mac)
    return CommandResult(returncode=1, stdout="MAC address source not set.")


def set_mac_address_source(source: str) -> CommandResult:
    if os.geteuid() != 0:
        return CommandResult(returncode=1, stdout="Must be run as root.")
    if not MESHTASTIC_MAIN_CONFIG_PATH.exists():
        return CommandResult(returncode=1, stdout="Meshtastic config.yaml not found.")
    source = source.strip()
    if not source:
        return CommandResult(returncode=1, stdout="MACAddressSource cannot be empty.")

    text = MESHTASTIC_MAIN_CONFIG_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    def _set_key(key: str, value: str | None) -> bool:
        pattern = re.compile(rf"^(\s*){re.escape(key)}\s*:")
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            match = pattern.match(line)
            if match:
                if value is None:
                    del lines[idx]
                else:
                    lines[idx] = f"{match.group(1)}{key}: {value}"
                return True
        return False

    def _insert_key(key: str, value: str) -> None:
        for idx, line in enumerate(lines):
            if re.match(r"^\s*General\s*:", line):
                indent = re.match(r"^(\s*)", line).group(1)
                lines.insert(idx + 1, f"{indent}  {key}: {value}")
                return
        lines.append("")
        lines.append("General:")
        lines.append(f"  {key}: {value}")

    if source == "auto":
        from mpwrd_config.system import list_ethernet_interfaces, list_wifi_interfaces

        chosen_source: str | None = None
        chosen_mac: str | None = None
        eth = list_ethernet_interfaces()
        if eth:
            chosen_source = eth[0]
        else:
            wifi = list_wifi_interfaces()
            if wifi:
                chosen_source = wifi[0]
            elif _bluetooth_controller_detected():
                chosen_source = "bluetooth"
            else:
                chosen_mac = _generated_cpu_mac()
        if not chosen_source and not chosen_mac:
            return CommandResult(returncode=1, stdout="No MAC source available.")
        if chosen_mac:
            source = "cpu"
        else:
            source = chosen_source or ""

    if source == "cpu":
        generated = _generated_cpu_mac()
        if not generated:
            return CommandResult(returncode=1, stdout="Unable to generate MAC from CPU or machine-id.")
        _set_key("MACAddressSource", None)
        if not _set_key("MACAddress", generated):
            _insert_key("MACAddress", generated)
        source_label = f"generated CPU MAC {generated}"
    else:
        _set_key("MACAddress", None)
        if not _set_key("MACAddressSource", source):
            _insert_key("MACAddressSource", source)
        source_label = source

    new_text = "\n".join(lines)
    if text.endswith("\n"):
        new_text += "\n"
    MESHTASTIC_MAIN_CONFIG_PATH.write_text(new_text, encoding="utf-8")
    restart = _service_action_with_recovery("restart")
    if restart.returncode != 0:
        return CommandResult(returncode=restart.returncode, stdout=restart.stdout.strip() or "Failed to restart meshtasticd.")
    return CommandResult(returncode=0, stdout=f"MAC address source set to {source_label}.")


def _clean_meshtastic_output(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Connected to radio") or stripped.startswith("Completed"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _load_os_release() -> dict[str, str]:
    data: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    except FileNotFoundError:
        return {}
    return data


def _iter_repo_sources() -> list[tuple[Path, str]]:
    paths: list[Path] = []
    if MESHTASTIC_REPO_LIST_DIR.exists():
        paths.extend(sorted(MESHTASTIC_REPO_LIST_DIR.glob("*.list")))
        paths.extend(sorted(MESHTASTIC_REPO_LIST_DIR.glob("*.sources")))
    sources: list[tuple[Path, str]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            continue
        sources.append((path, text))
    return sources


def _detect_meshtastic_channel(text: str, filename: str) -> tuple[str | None, str | None]:
    lowered = text.lower()
    match = OPENSUSE_REPO_PATTERN.search(lowered)
    if match:
        channel = match.group(1)
        if channel in MESHTASTIC_REPO_CHANNELS:
            return channel, "opensuse"
    match = PPA_REPO_PATTERN.search(lowered)
    if match:
        channel = match.group(1)
        if channel in MESHTASTIC_PPA_CHANNELS:
            return channel, "ppa"
    filename_lower = filename.lower()
    if "meshtastic" in filename_lower:
        for channel in MESHTASTIC_REPO_CHANNELS:
            if channel in filename_lower:
                return channel, "filename"
    return None, None


def _detect_meshtastic_repos() -> list[tuple[str, str, Path]]:
    detected: list[tuple[str, str, Path]] = []
    for path, text in _iter_repo_sources():
        channel, source = _detect_meshtastic_channel(text, path.name)
        if channel and source:
            detected.append((channel, source, path))
    return detected


def _raspberry_pi_os_note() -> str:
    data = _load_os_release()
    name = data.get("NAME", "")
    if not (name.startswith("Raspbian") or data.get("ID") == "raspbian"):
        return ""
    arch = os.uname().machine.lower()
    if arch in {"aarch64", "arm64"}:
        return "Raspberry Pi OS 64-bit detected; using Debian repos."
    return "Raspberry Pi OS 32-bit detected; using Raspbian repos."


def _meshtastic_repo_distro() -> tuple[str | None, str | None, str | None]:
    override = os.getenv("MPWRD_MESHTASTIC_REPO_DISTRO")
    if override:
        return "opensuse", override, None
    data = _load_os_release()
    name = data.get("NAME", "")
    if name.startswith("Raspbian") or data.get("ID") == "raspbian":
        version = data.get("VERSION_ID", "").split(".")[0]
        if not version:
            return None, None, "Unable to determine Raspbian version."
        arch = os.uname().machine.lower()
        if arch in {"aarch64", "arm64"}:
            return "opensuse", f"Debian_{version}", None
        return "opensuse", f"Raspbian_{version}", None
    if data.get("ID") == "debian":
        version = data.get("VERSION_ID", "").split(".")[0]
        if version:
            return "opensuse", f"Debian_{version}", None
    if data.get("ID") == "ubuntu" or "ubuntu" in data.get("ID_LIKE", "").lower():
        return "ppa", data.get("VERSION_CODENAME") or data.get("UBUNTU_CODENAME") or None, None
    return None, None, "Unsupported OS for Meshtastic repos. Set MPWRD_MESHTASTIC_REPO_DISTRO to override."


def meshtastic_repo_status() -> CommandResult:
    detected = _detect_meshtastic_repos()
    note = _raspberry_pi_os_note()
    if not detected:
        message = "No Meshtastic repo configured."
        if note:
            message = f"{message}\n{note}"
        return CommandResult(returncode=0, stdout=message)
    channels = sorted({channel for channel, _, _ in detected})
    if len(channels) == 1:
        channel = channels[0]
        sources = ", ".join(sorted({source for ch, source, _ in detected if ch == channel}))
        details = f" ({sources})" if sources else ""
        message = f"Meshtastic repo: {channel}{details}"
        if note:
            message = f"{message}\n{note}"
        return CommandResult(returncode=0, stdout=message)
    summary = ", ".join(f"{channel}" for channel in channels)
    message = f"Multiple Meshtastic repos configured: {summary}"
    if note:
        message = f"{message}\n{note}"
    return CommandResult(returncode=0, stdout=message)


def set_meshtastic_repo(channel: str, install: bool = True, stream: bool = False) -> CommandResult:
    if os.geteuid() != 0:
        return CommandResult(returncode=1, stdout="Must be run as root.")
    runner = _run_live if stream else _run
    channel = channel.strip().lower()
    repo_id = MESHTASTIC_REPO_CHANNELS.get(channel)
    if not repo_id or channel not in MESHTASTIC_PPA_CHANNELS:
        return CommandResult(returncode=1, stdout=f"Unknown repo channel: {channel}")
    repo_kind, distro, error = _meshtastic_repo_distro()
    if error:
        return CommandResult(returncode=1, stdout=error)
    if repo_kind == "ppa":
        if not shutil.which("add-apt-repository"):
            runner(["apt", "update"])
            install_result = runner(["apt", "install", "-y", "software-properties-common"])
            if install_result.returncode != 0:
                return CommandResult(returncode=install_result.returncode, stdout=install_result.stdout)
        for _, _, path in _detect_meshtastic_repos():
            if path.exists():
                path.unlink(missing_ok=True)
        add_result = runner(["add-apt-repository", "-y", MESHTASTIC_PPA_CHANNELS[channel]])
        if add_result.returncode != 0:
            return CommandResult(returncode=add_result.returncode, stdout=add_result.stdout)
        update_result = runner(["apt", "update"])
        outputs = [add_result.stdout.strip(), update_result.stdout.strip()]
        if install:
            install_result = runner(["apt", "install", "-y", "meshtasticd"])
            outputs.append(install_result.stdout.strip())
            code = install_result.returncode
        else:
            code = update_result.returncode
        output = "\n".join(part for part in outputs if part)
        return CommandResult(returncode=code, stdout=output or f"Repo set to {channel}.")
    if repo_kind != "opensuse" or not distro:
        return CommandResult(returncode=1, stdout="Unable to determine repo distro.")
    for other_channel, other_repo in MESHTASTIC_REPO_CHANNELS.items():
        if other_channel == channel:
            continue
        (MESHTASTIC_REPO_LIST_DIR / f"{other_repo}.list").unlink(missing_ok=True)
        (MESHTASTIC_REPO_KEY_DIR / f"network_Meshtastic_{other_channel}.gpg").unlink(missing_ok=True)
    for _, _, path in _detect_meshtastic_repos():
        if path.suffix in {".list", ".sources"} and path.exists():
            path.unlink(missing_ok=True)
    list_path = MESHTASTIC_REPO_LIST_DIR / f"{repo_id}.list"
    key_path = MESHTASTIC_REPO_KEY_DIR / f"network_Meshtastic_{channel}.gpg"
    list_line = f"deb http://download.opensuse.org/repositories/{repo_id}/{distro}/ /\n"
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text(list_line, encoding="utf-8")
    key_url = f"https://download.opensuse.org/repositories/{repo_id}/{distro}/Release.key"
    if not shutil.which("gpg"):
        return CommandResult(returncode=1, stdout="gpg is required to install the repo key.")
    key_cmd = [
        "bash",
        "-c",
        f"curl -fsSL {key_url} | gpg --dearmor | tee {key_path} >/dev/null",
    ]
    key_result = runner(key_cmd)
    if key_result.returncode != 0:
        return CommandResult(returncode=key_result.returncode, stdout=key_result.stdout)
    update_result = runner(["apt", "update"])
    outputs = [update_result.stdout.strip()]
    if install:
        install_result = runner(["apt", "install", "-y", "meshtasticd"])
        outputs.append(install_result.stdout.strip())
        code = install_result.returncode
    else:
        code = update_result.returncode
    output = "\n".join(part for part in outputs if part)
    return CommandResult(returncode=code, stdout=output or f"Repo set to {channel}.")


@dataclass
class MeshtasticResult:
    returncode: int
    stdout: str


class MeshtasticSession:
    """Reusable Meshtastic TCP session for interactive callers."""

    def __init__(self) -> None:
        self._interface: TCPInterface | None = None
        self._config_loaded = False

    def get_interface(
        self,
        *,
        wait_for_config: bool = False,
        reconnect: bool = False,
        attempts: int | None = None,
    ) -> tuple[MeshtasticResult | None, TCPInterface | None]:
        if reconnect:
            self.close()
        if self._interface is None:
            connect_attempts = 2 if attempts is None else max(1, int(attempts))
            error, interface = _connect_meshtastic(wait_for_config=False, attempts=connect_attempts)
            if error or not interface:
                return error, None
            self._interface = interface
            self._config_loaded = False
        if wait_for_config and not self._config_loaded:
            try:
                self._interface.waitForConfig()
                self._config_loaded = True
            except Exception:
                self.close()
                return (
                    MeshtasticResult(
                        returncode=124,
                        stdout=f"Meshtastic command timed out after {MESHTASTIC_TIMEOUT_SEC:.0f}s.",
                    ),
                    None,
                )
        return None, self._interface

    def close(self, *, wait: bool = True) -> None:
        interface = self._interface
        self._interface = None
        self._config_loaded = False
        if interface is None:
            return

        def _close_interface() -> None:
            try:
                interface.close()
            except Exception:
                pass

        if wait:
            _close_interface()
            return
        threading.Thread(target=_close_interface, name="meshtastic-close", daemon=True).start()


def _connect_meshtastic(
    wait_for_config: bool = False,
    attempts: int = 2,
) -> tuple[MeshtasticResult | None, TCPInterface | None]:
    attempts = max(1, int(attempts))
    last_error: Exception | None = None
    for attempt in range(attempts):
        interface: TCPInterface | None = None
        try:
            # Use staged connect so startup can connect without implicitly waiting
            # for full config download in TCPInterface.__init__.
            interface = TCPInterface(
                MESHTASTIC_HOST,
                timeout=int(MESHTASTIC_TIMEOUT_SEC),
                connectNow=False,
                noNodes=True,
            )
            interface.myConnect()
            interface.connect()
            if wait_for_config:
                interface.waitForConfig()
            return None, interface
        except Exception as exc:
            last_error = exc
            if interface is not None:
                try:
                    interface.close()
                except Exception:
                    pass
            if attempt + 1 < attempts:
                time.sleep(1)
                continue
    message = str(last_error or "").lower()
    if "timed out" in message:
        return (
            MeshtasticResult(
                returncode=124,
                stdout=f"Meshtastic command timed out after {MESHTASTIC_TIMEOUT_SEC:.0f}s.",
            ),
            None,
        )
    return MeshtasticResult(returncode=1, stdout=f"Meshtastic connect failed: {last_error}"), None


def _run_meshtastic_cli(command: Sequence[str], label: str | None = None) -> MeshtasticResult:
    args = list(command)
    cmd = [sys.executable, "-m", "meshtastic", "--host", MESHTASTIC_HOST, *args]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=MESHTASTIC_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        prefix = f"{label}: " if label else ""
        return MeshtasticResult(
            returncode=124,
            stdout=f"{prefix}Meshtastic command timed out after {MESHTASTIC_TIMEOUT_SEC:.0f}s.",
        )
    output = (result.stdout or "").strip()
    if result.returncode != 0 and not output:
        output = "Meshtastic command failed."
    return MeshtasticResult(returncode=result.returncode, stdout=output)


def _run_meshtastic_action(
    action: Callable[[TCPInterface], MeshtasticResult],
    *,
    wait_for_config: bool,
    session: MeshtasticSession | None = None,
) -> MeshtasticResult:
    close_interface = session is None
    if session is None:
        error, interface = _connect_meshtastic(wait_for_config=wait_for_config)
    else:
        error, interface = session.get_interface(wait_for_config=wait_for_config)
    if error or not interface:
        return error or MeshtasticResult(returncode=1, stdout="Unable to connect to Meshtastic.")

    try:
        return action(interface)
    except Exception as exc:
        if session is None:
            return MeshtasticResult(returncode=1, stdout=f"Meshtastic command failed: {exc}")
        retry_error, retry_interface = session.get_interface(wait_for_config=wait_for_config, reconnect=True)
        if retry_error or not retry_interface:
            return retry_error or MeshtasticResult(returncode=1, stdout="Unable to connect to Meshtastic.")
        try:
            return action(retry_interface)
        except Exception as retry_exc:
            return MeshtasticResult(returncode=1, stdout=f"Meshtastic command failed: {retry_exc}")
    finally:
        if close_interface:
            interface.close()


def _interface_info(interface: TCPInterface) -> str:
    owner = f"Owner: {interface.getLongName()} ({interface.getShortName()})"
    myinfo = f"\nMy info: {meshtastic_util.message_to_json(interface.myInfo)}" if interface.myInfo else ""
    metadata = f"\nMetadata: {meshtastic_util.message_to_json(interface.metadata)}" if interface.metadata else ""
    nodes: dict[str, Any] = {}
    if interface.nodes:
        for node in interface.nodes.values():
            node_copy = copy.deepcopy(node)
            keys_to_remove = ("raw", "decoded", "payload")
            meshtastic_util.remove_keys_from_dict(keys_to_remove, node_copy)
            if "user" in node_copy and isinstance(node_copy["user"], dict) and "macaddr" in node_copy["user"]:
                node_copy["user"]["macaddr"] = meshtastic_util.convert_mac_addr(node_copy["user"]["macaddr"])
            node_id = node_copy.get("user", {}).get("id")
            if node_id:
                nodes[node_id] = node_copy
    mesh = "\n\nNodes in mesh: "
    return owner + myinfo + metadata + mesh + json.dumps(nodes, indent=2)


def _get_node(interface: TCPInterface):
    return interface.localNode


def _format_public_key(security) -> str:
    if not security:
        return "Public key not found."
    key = getattr(security, "public_key", None)
    if not key:
        return "Public key not found."
    if isinstance(key, bytes):
        return base64.b64encode(key).decode("ascii")
    return str(key)


def _message_to_dict(message) -> dict[str, Any]:
    try:
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
            including_default_value_fields=True,
        )
    except TypeError:
        # Older protobuf versions do not support including_default_value_fields.
        return MessageToDict(
            message,
            preserving_proto_field_name=True,
        )


def _split_compound_name(comp_name: str) -> list[str]:
    name = comp_name.split(".")
    if len(name) < 2:
        name[0] = comp_name
        name.append(comp_name)
    return name


def _set_pref(config, comp_name: str, raw_val: str) -> tuple[bool, str | None]:
    name = _split_compound_name(comp_name)
    snake_name = meshtastic_util.camel_to_snake(name[-1])
    obj_desc = config.DESCRIPTOR
    config_part = config
    config_type = obj_desc.fields_by_name.get(name[0])
    if config_type and config_type.message_type is not None:
        for name_part in name[1:-1]:
            part_snake_name = meshtastic_util.camel_to_snake(name_part)
            config_part = getattr(config_part, config_type.name)
            config_type = config_type.message_type.fields_by_name.get(part_snake_name)
    pref = None
    if config_type and config_type.message_type is not None:
        pref = config_type.message_type.fields_by_name.get(snake_name)
    elif config_type:
        pref = config_type
    if not pref or not config_type:
        return False, f"{comp_name} not found."
    if isinstance(raw_val, str):
        val = meshtastic_util.fromStr(raw_val)
    else:
        val = raw_val
    enum_type = pref.enum_type
    if enum_type and isinstance(val, str):
        enum_val = enum_type.values_by_name.get(val)
        if enum_val:
            val = enum_val.number
        else:
            choices = ", ".join(sorted(enum_type.values_by_name.keys()))
            return False, f"{comp_name} enum must be one of: {choices}"
    if pref.label != pref.LABEL_REPEATED:
        if config_type.message_type is not None:
            config_values = getattr(config_part, config_type.name)
            setattr(config_values, pref.name, val)
        else:
            setattr(config_part, pref.name, val)
    else:
        getattr(config_part, pref.name).append(val)
    return True, None


def _get_pref_value(config, comp_name: str) -> tuple[bool, str]:
    name = _split_compound_name(comp_name)
    snake_name = meshtastic_util.camel_to_snake(name[-1])
    obj_desc = config.DESCRIPTOR
    config_part = config
    config_type = obj_desc.fields_by_name.get(name[0])
    if config_type and config_type.message_type is not None:
        for name_part in name[1:-1]:
            part_snake_name = meshtastic_util.camel_to_snake(name_part)
            config_part = getattr(config_part, config_type.name)
            config_type = config_type.message_type.fields_by_name.get(part_snake_name)
    pref = None
    if config_type and config_type.message_type is not None:
        pref = config_type.message_type.fields_by_name.get(snake_name)
    elif config_type:
        pref = config_type
    if not pref or not config_type:
        return False, f"{comp_name} not found."
    if config_type.message_type is not None:
        config_values = getattr(config_part, config_type.name)
        value = getattr(config_values, pref.name)
    else:
        value = getattr(config_part, pref.name)
    if pref.label == pref.LABEL_REPEATED:
        value = [meshtastic_util.toStr(v) for v in value]
    else:
        value = meshtastic_util.toStr(value)
    return True, f"{config_type.name}.{pref.name}:{value}"


def _select_config(node, section: str):
    if section in node.localConfig.DESCRIPTOR.fields_by_name:
        return node.localConfig
    if section in node.moduleConfig.DESCRIPTOR.fields_by_name:
        return node.moduleConfig
    return None


def _ensure_config_loaded(node, section: str) -> None:
    config = _select_config(node, section)
    if not config:
        return
    if len(config.ListFields()) == 0:
        descriptor = config.DESCRIPTOR.fields_by_name.get(section)
        if descriptor:
            node.requestConfig(descriptor)


def _apply_preferences(node, updates: list[tuple[str, str]]) -> MeshtasticResult:
    if not updates:
        return MeshtasticResult(returncode=1, stdout="No preferences provided.")
    sections: list[str] = []
    for field, value in updates:
        section = field.split(".", 1)[0]
        config = _select_config(node, section)
        if not config:
            return MeshtasticResult(returncode=1, stdout=f"Unknown preference section: {section}")
        _ensure_config_loaded(node, section)
        ok, error = _set_pref(config, field, value)
        if not ok:
            return MeshtasticResult(returncode=1, stdout=error or "Invalid preference.")
        if section not in sections:
            sections.append(section)
    if len(sections) > 1:
        node.beginSettingsTransaction()
    for section in sections:
        node.writeConfig(section)
    if len(sections) > 1:
        node.commitSettingsTransaction()
    return MeshtasticResult(returncode=0, stdout="Preferences updated.")


def meshtastic_info(session: MeshtasticSession | None = None) -> MeshtasticResult:
    return _run_meshtastic_action(
        lambda interface: MeshtasticResult(returncode=0, stdout=_interface_info(interface)),
        wait_for_config=True,
        session=session,
    )


def _extract_json_block(text: str, marker: str) -> dict[str, Any] | None:
    index = text.find(marker)
    if index < 0:
        return None
    start = text.find("{", index)
    if start < 0:
        return None
    level = 0
    end = None
    for offset, char in enumerate(text[start:], start=start):
        if char == "{":
            level += 1
        elif char == "}":
            level -= 1
            if level == 0:
                end = offset + 1
                break
    if end is None:
        return None
    block = text[start:end]
    try:
        return json.loads(block)
    except json.JSONDecodeError:
        return None


def _extract_block_lines(text: str, marker: str) -> list[str]:
    lines = text.splitlines()
    capture = False
    collected: list[str] = []
    for line in lines:
        if line.startswith(marker):
            capture = True
            continue
        if capture:
            if not line.strip():
                break
            collected.append(line)
    return collected


def _parse_meshtastic_info(text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    owner_match = re.search(r"^Owner:\s*(.+)$", text, re.MULTILINE)
    if owner_match:
        data["owner"] = owner_match.group(1).strip()

    data["my_info"] = _extract_json_block(text, "My info:") or {}
    data["metadata"] = _extract_json_block(text, "Metadata:") or {}
    data["preferences"] = _extract_json_block(text, "Preferences:") or {}
    data["module_preferences"] = _extract_json_block(text, "Module preferences:") or {}
    data["nodes"] = _extract_json_block(text, "Nodes in mesh:") or {}

    channels: list[dict[str, Any]] = []
    for line in _extract_block_lines(text, "Channels:"):
        match = re.search(r"(\\d+)", line)
        json_start = line.find("{")
        channel: dict[str, Any] = {}
        if json_start >= 0:
            try:
                channel = json.loads(line[json_start:])
            except json.JSONDecodeError:
                channel = {}
        if match:
            channel["index"] = int(match.group(1))
        psk_match = re.search(r"psk=([^ ]+)", line)
        if psk_match:
            channel["psk_type"] = psk_match.group(1)
        channels.append(channel)
    data["channels"] = channels

    url_match = re.search(r"^Primary channel URL:\s*(.+)$", text, re.MULTILINE)
    if url_match:
        data["url_primary_channel"] = url_match.group(1).strip()
    url_match = re.search(r"^Complete URL \\(includes all channels\\):\s*(.+)$", text, re.MULTILINE)
    if url_match:
        data["url_all_channels"] = url_match.group(1).strip()

    return data


def _flatten_blocks(prefix: str, data: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for block, values in data.items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            flattened[f"{prefix}{block}_{key}"] = value
    return flattened


def _flatten_recursive(prefix: str, data: Any, output: dict[str, Any]) -> None:
    if isinstance(data, dict):
        for key, value in data.items():
            _flatten_recursive(f"{prefix}{key}_", value, output)
        return
    output[prefix.rstrip("_")] = data


def meshtastic_config(
    categories: str,
    quiet: bool = False,
    session: MeshtasticSession | None = None,
) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        selected = {item.strip().lower() for item in categories.split(",") if item.strip()}
        if "all" in selected:
            selected = {"nodeinfo", "settings", "channels"}
        lines: list[str] = []
        node = _get_node(interface)

        if "nodeinfo" in selected:
            node_entry = interface.getMyNodeInfo() or {}
            node_flat: dict[str, Any] = {}
            _flatten_recursive("nodeinfo_", node_entry, node_flat)
            for key, value in node_flat.items():
                lines.append(f"{key}:{value}")
            nodes_in_db = len(interface.nodes or {})
            lines.append(f"metadata_nodedbCount:{nodes_in_db}")

        if "settings" in selected:
            local_dict = _message_to_dict(node.localConfig)
            module_dict = _message_to_dict(node.moduleConfig)
            for key, value in _flatten_blocks("", local_dict).items():
                lines.append(f"{key}:{value}")
            for key, value in _flatten_blocks("", module_dict).items():
                lines.append(f"{key}:{value}")

        if "channels" in selected:
            channels = node.channels or []
            for ch in channels:
                if ch is None:
                    continue
                index = ch.index
                settings_dict = _message_to_dict(ch.settings)
                try:
                    role_name = channel_pb2.Channel.Role.Name(ch.role)
                except Exception:
                    role_name = str(ch.role)
                lines.append(f"channel{index}_type:{role_name}")
                try:
                    lines.append(f"channel{index}_psk_type:{meshtastic_util.pskToString(ch.settings.psk)}")
                except Exception:
                    pass
                for key, value in settings_dict.items():
                    lines.append(f"channel{index}_{key}:{value}")
            try:
                lines.append(f"url_primary_channel:{node.getURL(includeAll=False)}")
                lines.append(f"url_all_channels:{node.getURL(includeAll=True)}")
            except SystemExit:
                pass
            except Exception:
                pass

        if quiet:
            return MeshtasticResult(returncode=0, stdout="")
        if not lines:
            return MeshtasticResult(returncode=1, stdout="No configuration data available.")
        return MeshtasticResult(returncode=0, stdout="\n".join(lines))

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def meshtastic_summary(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        lora = node.localConfig.lora
        device = node.localConfig.device
        security = node.localConfig.security
        meta_dict = _message_to_dict(interface.metadata) if interface.metadata else {}
        user = interface.getMyUser() or {}
        node_num = interface.myInfo.my_node_num if interface.myInfo else "unknown"
        public_key = _format_public_key(security)

        lines = [
            f"Service:{service_status().stdout.strip()}",
            f"Version:{meta_dict.get('firmware_version') or meta_dict.get('firmwareVersion', 'unknown')}",
            f"Node name:{interface.getLongName()}",
            f"NodeID:{user.get('id', 'unknown') if isinstance(user, dict) else 'unknown'}",
            f"Nodenum:{node_num}",
            f"TX enabled:{lora.tx_enabled}",
            f"Use preset:{lora.use_preset}",
            f"Preset:{lora.modem_preset}",
            f"Bandwidth:{lora.bandwidth}",
            f"Spread factor:{lora.spread_factor}",
            f"Coding rate:{lora.coding_rate}",
            f"Role:{device.role}",
            f"Freq offset:{lora.frequency_offset}",
            f"Region:{lora.region}",
            f"Hop limit:{lora.hop_limit}",
            f"Freq slot:{lora.channel_num}",
            f"Override freq:{lora.override_frequency}",
            f"Public key:{public_key}",
            f"Nodes in db:{len(interface.nodes or {})}",
        ]
        return MeshtasticResult(returncode=0, stdout="\n".join(lines))

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def meshtastic_snapshot() -> tuple[MeshtasticResult, dict[str, Any]]:
    error, interface = _connect_meshtastic(wait_for_config=True)
    if error or not interface:
        return error or MeshtasticResult(returncode=1, stdout="Unable to connect to Meshtastic."), {}
    try:
        node = _get_node(interface)
        lora = node.localConfig.lora
        device = node.localConfig.device
        security = node.localConfig.security
        meta_dict = _message_to_dict(interface.metadata) if interface.metadata else {}
        user = interface.getMyUser() or {}
        node_num = interface.myInfo.my_node_num if interface.myInfo else "unknown"
        public_key = _format_public_key(security)
        legacy_value = getattr(security, "admin_channel_enabled", None)
        if legacy_value is True:
            legacy_admin = "enabled"
        elif legacy_value is False:
            legacy_admin = "disabled"
        else:
            legacy_admin = "unknown"

        summary_lines = [
            f"Service:{service_status().stdout.strip()}",
            f"Version:{meta_dict.get('firmware_version') or meta_dict.get('firmwareVersion', 'unknown')}",
            f"Node name:{interface.getLongName()}",
            f"NodeID:{user.get('id', 'unknown') if isinstance(user, dict) else 'unknown'}",
            f"Nodenum:{node_num}",
            f"TX enabled:{lora.tx_enabled}",
            f"Use preset:{lora.use_preset}",
            f"Preset:{lora.modem_preset}",
            f"Bandwidth:{lora.bandwidth}",
            f"Spread factor:{lora.spread_factor}",
            f"Coding rate:{lora.coding_rate}",
            f"Role:{device.role}",
            f"Freq offset:{lora.frequency_offset}",
            f"Region:{lora.region}",
            f"Hop limit:{lora.hop_limit}",
            f"Freq slot:{lora.channel_num}",
            f"Override freq:{lora.override_frequency}",
            f"Public key:{public_key}",
            f"Nodes in db:{len(interface.nodes or {})}",
        ]

        config_url = ""
        try:
            config_url = node.getURL(includeAll=True)
        except Exception:
            config_url = ""

        snapshot = {
            "summary": "\n".join(summary_lines),
            "config_url": config_url,
            "legacy_admin": legacy_admin,
            "lora": {
                "lora_region": lora.region,
                "lora_usePreset": lora.use_preset,
                "lora_modemPreset": lora.modem_preset,
                "lora_bandwidth": lora.bandwidth,
                "lora_spreadFactor": lora.spread_factor,
                "lora_codingRate": lora.coding_rate,
                "lora_frequencyOffset": lora.frequency_offset,
                "lora_hopLimit": lora.hop_limit,
                "lora_txEnabled": lora.tx_enabled,
                "lora_txPower": lora.tx_power,
                "lora_channelNum": lora.channel_num,
                "lora_overrideDutyCycle": lora.override_duty_cycle,
                "lora_sx126xRxBoostedGain": lora.sx126x_rx_boosted_gain,
                "lora_overrideFrequency": lora.override_frequency,
                "lora_ignoreMqtt": lora.ignore_mqtt,
                "lora_configOkToMqtt": lora.config_ok_to_mqtt,
            },
        }
        info = _interface_info(interface)
        return MeshtasticResult(returncode=0, stdout=info), snapshot
    finally:
        interface.close()


def config_qr(session: MeshtasticSession | None = None) -> MeshtasticResult:
    url_result = get_config_url(session=session)
    if url_result.returncode != 0:
        return url_result
    url = url_result.stdout.strip()
    if not url:
        return MeshtasticResult(returncode=1, stdout="No configuration URL found.")
    python_qr = _render_qr_text_python(url)
    if python_qr:
        return MeshtasticResult(returncode=0, stdout=f"{python_qr}\n{url}")
    return MeshtasticResult(returncode=0, stdout=url)


def meshtastic_update(command: str | Sequence[str], attempts: int = 5, label: str | None = None) -> MeshtasticResult:
    if isinstance(command, str):
        args = shlex.split(command)
    else:
        args = list(command)
    # Custom command is the only path that shells out to the meshtastic CLI.
    return _run_meshtastic_cli(args, label=label)


def list_preference_fields() -> MeshtasticResult:
    def collect_fields(desc) -> list[str]:
        fields: list[str] = []
        for field in desc.fields:
            if field.name == "version":
                continue
            if field.message_type is None:
                fields.append(field.name)
                continue
            for sub in field.message_type.fields:
                if sub.message_type is not None and sub.message_type.fields:
                    for sub2 in sub.message_type.fields:
                        fields.append(f"{field.name}.{sub.name}.{sub2.name}")
                else:
                    fields.append(f"{field.name}.{sub.name}")
        return fields

    local_fields = collect_fields(config_pb2.Config.DESCRIPTOR)
    module_fields = collect_fields(module_config_pb2.ModuleConfig.DESCRIPTOR)
    lines = sorted(set(local_fields + module_fields))
    return MeshtasticResult(returncode=0, stdout="\n".join(lines))


def get_preference(field: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        section = field.split(".", 1)[0]
        config = _select_config(node, section)
        if not config:
            return MeshtasticResult(returncode=1, stdout=f"Unknown preference section: {section}")
        _ensure_config_loaded(node, section)
        ok, value = _get_pref_value(config, field)
        if not ok:
            return MeshtasticResult(returncode=1, stdout=value)
        return MeshtasticResult(returncode=0, stdout=value)

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def set_preference(field: str, value: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        return _apply_preferences(node, [(field, value)])

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_set(
    index: int,
    field: str,
    value: str,
    session: MeshtasticSession | None = None,
) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        if index < 0 or index >= len(node.channels):
            return MeshtasticResult(returncode=1, stdout="Invalid channel index.")
        ch = node.channels[index]
        if field == "psk":
            ch.settings.psk = meshtastic_util.fromPSK(value)
        else:
            ok, error_msg = _set_pref(ch.settings, field, value)
            if not ok:
                return MeshtasticResult(returncode=1, stdout=error_msg or "Invalid channel setting.")
        ch.role = channel_pb2.Channel.Role.PRIMARY if index == 0 else channel_pb2.Channel.Role.SECONDARY
        node.writeChannel(index)
        return MeshtasticResult(returncode=0, stdout="Channel updated.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_add(name: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        if len(name) > 10:
            return MeshtasticResult(returncode=1, stdout="Channel name must be shorter than 10 characters.")
        node = _get_node(interface)
        if node.getChannelByName(name):
            return MeshtasticResult(returncode=1, stdout=f"Channel '{name}' already exists.")
        ch = node.getDisabledChannel()
        if not ch:
            return MeshtasticResult(returncode=1, stdout="No free channels were found.")
        chs = channel_pb2.ChannelSettings()
        chs.psk = meshtastic_util.genPSK256()
        chs.name = name
        ch.settings.CopyFrom(chs)
        ch.role = channel_pb2.Channel.Role.SECONDARY
        node.writeChannel(ch.index)
        return MeshtasticResult(returncode=0, stdout="Channel added.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_delete(index: int, session: MeshtasticSession | None = None) -> MeshtasticResult:
    if index == 0:
        return MeshtasticResult(returncode=1, stdout="Cannot delete primary channel.")

    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        node.deleteChannel(index)
        return MeshtasticResult(returncode=0, stdout="Channel deleted.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_enable(index: int, session: MeshtasticSession | None = None) -> MeshtasticResult:
    if index == 0:
        return MeshtasticResult(returncode=1, stdout="Cannot enable primary channel.")

    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        if index < 0 or index >= len(node.channels):
            return MeshtasticResult(returncode=1, stdout="Invalid channel index.")
        ch = node.channels[index]
        ch.role = channel_pb2.Channel.Role.SECONDARY
        node.writeChannel(index)
        return MeshtasticResult(returncode=0, stdout="Channel enabled.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_disable(index: int, session: MeshtasticSession | None = None) -> MeshtasticResult:
    if index == 0:
        return MeshtasticResult(returncode=1, stdout="Cannot disable primary channel.")

    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        if index < 0 or index >= len(node.channels):
            return MeshtasticResult(returncode=1, stdout="Invalid channel index.")
        ch = node.channels[index]
        ch.role = channel_pb2.Channel.Role.DISABLED
        node.writeChannel(index)
        return MeshtasticResult(returncode=0, stdout="Channel disabled.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_set_url(url: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        try:
            node.setURL(url, addOnly=False)
            return MeshtasticResult(returncode=0, stdout="Channels updated.")
        except SystemExit as exc:
            return MeshtasticResult(returncode=1, stdout=str(exc))

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def channel_add_url(url: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        try:
            node.setURL(url, addOnly=True)
            return MeshtasticResult(returncode=0, stdout="Channels added from URL.")
        except SystemExit as exc:
            return MeshtasticResult(returncode=1, stdout=str(exc))

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def lora_settings(session: MeshtasticSession | None = None) -> tuple[MeshtasticResult, dict[str, Any]]:
    close_interface = session is None
    if session is None:
        error, interface = _connect_meshtastic(wait_for_config=True)
    else:
        error, interface = session.get_interface(wait_for_config=True)
    if error or not interface:
        return error or MeshtasticResult(returncode=1, stdout="Unable to connect to Meshtastic."), {}

    try:
        lora = interface.localNode.localConfig.lora
        info = _interface_info(interface)
        return MeshtasticResult(returncode=0, stdout=info), {
            "lora_region": lora.region,
            "lora_usePreset": lora.use_preset,
            "lora_modemPreset": lora.modem_preset,
            "lora_bandwidth": lora.bandwidth,
            "lora_spreadFactor": lora.spread_factor,
            "lora_codingRate": lora.coding_rate,
            "lora_frequencyOffset": lora.frequency_offset,
            "lora_hopLimit": lora.hop_limit,
            "lora_txEnabled": lora.tx_enabled,
            "lora_txPower": lora.tx_power,
            "lora_channelNum": lora.channel_num,
            "lora_overrideDutyCycle": lora.override_duty_cycle,
            "lora_sx126xRxBoostedGain": lora.sx126x_rx_boosted_gain,
            "lora_overrideFrequency": lora.override_frequency,
            "lora_ignoreMqtt": lora.ignore_mqtt,
            "lora_configOkToMqtt": lora.config_ok_to_mqtt,
        }
    except Exception:
        if session is None:
            return MeshtasticResult(returncode=1, stdout="Unable to connect to Meshtastic."), {}
        retry_error, retry_interface = session.get_interface(wait_for_config=True, reconnect=True)
        if retry_error or not retry_interface:
            return retry_error or MeshtasticResult(returncode=1, stdout="Unable to connect to Meshtastic."), {}
        lora = retry_interface.localNode.localConfig.lora
        info = _interface_info(retry_interface)
        return MeshtasticResult(returncode=0, stdout=info), {
            "lora_region": lora.region,
            "lora_usePreset": lora.use_preset,
            "lora_modemPreset": lora.modem_preset,
            "lora_bandwidth": lora.bandwidth,
            "lora_spreadFactor": lora.spread_factor,
            "lora_codingRate": lora.coding_rate,
            "lora_frequencyOffset": lora.frequency_offset,
            "lora_hopLimit": lora.hop_limit,
            "lora_txEnabled": lora.tx_enabled,
            "lora_txPower": lora.tx_power,
            "lora_channelNum": lora.channel_num,
            "lora_overrideDutyCycle": lora.override_duty_cycle,
            "lora_sx126xRxBoostedGain": lora.sx126x_rx_boosted_gain,
            "lora_overrideFrequency": lora.override_frequency,
            "lora_ignoreMqtt": lora.ignore_mqtt,
            "lora_configOkToMqtt": lora.config_ok_to_mqtt,
        }
    finally:
        if close_interface:
            interface.close()


def set_lora_settings(
    settings: dict[str, Any],
    attempts: int = 5,
    session: MeshtasticSession | None = None,
) -> MeshtasticResult:
    updates: list[tuple[str, str]] = []
    mapping = {
        "region": "lora.region",
        "use_preset": "lora.use_preset",
        "modem_preset": "lora.modem_preset",
        "bandwidth": "lora.bandwidth",
        "spread_factor": "lora.spread_factor",
        "coding_rate": "lora.coding_rate",
        "frequency_offset": "lora.frequency_offset",
        "hop_limit": "lora.hop_limit",
        "tx_enabled": "lora.tx_enabled",
        "tx_power": "lora.tx_power",
        "channel_num": "lora.channel_num",
        "override_duty_cycle": "lora.override_duty_cycle",
        "sx126x_rx_boosted_gain": "lora.sx126x_rx_boosted_gain",
        "override_frequency": "lora.override_frequency",
        "ignore_mqtt": "lora.ignore_mqtt",
        "config_ok_to_mqtt": "lora.config_ok_to_mqtt",
    }
    for key, value in settings.items():
        if value is None:
            continue
        meshtastic_key = mapping.get(key)
        if not meshtastic_key:
            continue
        updates.append((meshtastic_key, str(value)))
    if not updates:
        return MeshtasticResult(returncode=1, stdout="No LoRa settings provided.")

    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        return _apply_preferences(node, updates)

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def get_config_url(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        try:
            url = node.getURL(includeAll=True)
            return MeshtasticResult(returncode=0, stdout=url)
        except SystemExit as exc:
            return MeshtasticResult(returncode=1, stdout=str(exc))
        except Exception:
            return MeshtasticResult(returncode=1, stdout="Failed to extract configuration URL.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def set_config_url(url: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    return channel_set_url(url, session=session)


def get_public_key(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        security = interface.localNode.localConfig.security
        key = _format_public_key(security)
        if key == "Public key not found.":
            return MeshtasticResult(returncode=1, stdout=key)
        return MeshtasticResult(returncode=0, stdout=key)

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def set_public_key(key: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    return set_preference("security.public_key", f"base64:{key}", session=session)


def get_private_key(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        security = interface.localNode.localConfig.security
        key = getattr(security, "private_key", None)
        if not key:
            return MeshtasticResult(returncode=1, stdout="Private key not found.")
        if isinstance(key, bytes):
            return MeshtasticResult(returncode=0, stdout=base64.b64encode(key).decode("ascii"))
        return MeshtasticResult(returncode=0, stdout=str(key))

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def set_private_key(key: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    return set_preference("security.private_key", f"base64:{key}", session=session)


def list_admin_keys(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        security = interface.localNode.localConfig.security
        keys = list(getattr(security, "admin_key", []) or [])
        if not keys:
            return MeshtasticResult(returncode=0, stdout="none")
        formatted = "\n".join(
            f"{idx + 1}. {base64.b64encode(key).decode('ascii')}" for idx, key in enumerate(keys)
        )
        return MeshtasticResult(returncode=0, stdout=formatted)

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def add_admin_key(key: str, session: MeshtasticSession | None = None) -> MeshtasticResult:
    return set_preference("security.admin_key", f"base64:{key}", session=session)


def clear_admin_keys(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        node = _get_node(interface)
        section = "security"
        config = _select_config(node, section)
        if not config:
            return MeshtasticResult(returncode=1, stdout="Security section not found.")
        _ensure_config_loaded(node, section)
        security = getattr(config, "security", None)
        if not security or not hasattr(security, "admin_key"):
            return MeshtasticResult(returncode=1, stdout="Admin keys not available.")
        try:
            security.admin_key.clear()
        except AttributeError:
            del security.admin_key[:]
        node.writeConfig(section)
        return MeshtasticResult(returncode=0, stdout="Admin keys cleared.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def get_legacy_admin_state(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        security = interface.localNode.localConfig.security
        state = getattr(security, "admin_channel_enabled", None)
        if state is True:
            return MeshtasticResult(returncode=0, stdout="enabled")
        if state is False:
            return MeshtasticResult(returncode=0, stdout="disabled")
        return MeshtasticResult(returncode=1, stdout="error")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def set_legacy_admin_state(enabled: bool, session: MeshtasticSession | None = None) -> MeshtasticResult:
    value = "true" if enabled else "false"
    return set_preference("security.admin_channel_enabled", value, session=session)


def current_radio() -> MeshtasticResult:
    if _configured_lora_module() == "sim":
        return MeshtasticResult(returncode=0, stdout="sim")
    if not MESHTASTIC_CONFIG_DIR.exists():
        return MeshtasticResult(returncode=1, stdout="config directory missing")
    radios: list[str] = []
    for path in MESHTASTIC_CONFIG_DIR.glob("femtofox_*.yaml"):
        name = path.name
        if name == "femtofox_lora-meshstick-1262.yaml":
            radios.append("lora-meshstick-1262")
            continue
        key = RADIO_CONFIG_REVERSE.get(name.lower())
        if key:
            radios.append(key)
    if not radios:
        return MeshtasticResult(returncode=0, stdout="none")
    return MeshtasticResult(returncode=0, stdout=",".join(sorted(radios)))


def set_radio(model: str) -> CommandResult:
    for path in MESHTASTIC_CONFIG_DIR.glob("femtofox_*.yaml"):
        path.unlink()
    if model == "sim":
        result = _set_lora_module("sim")
        if result.returncode != 0:
            return result
        return _service_action_with_recovery("restart")
    if model == "none":
        result = _set_lora_module("auto")
        if result.returncode != 0:
            return result
        return _service_action_with_recovery("restart")
    if model not in RADIO_CONFIG_MAP:
        return CommandResult(returncode=1, stdout="Invalid radio model.")
    result = _set_lora_module("auto")
    if result.returncode != 0:
        return result
    if model == "lora-meshstick-1262":
        source = MESHTASTIC_AVAILABLE_DIR / RADIO_CONFIG_MAP[model]
    else:
        source = MESHTASTIC_AVAILABLE_DIR / "femtofox" / RADIO_CONFIG_MAP[model]
    destination_name = RADIO_CONFIG_MAP[model]
    if model == "lora-meshstick-1262":
        destination_name = "femtofox_lora-meshstick-1262.yaml"
    destination = MESHTASTIC_CONFIG_DIR / destination_name
    if source.exists():
        destination.write_bytes(source.read_bytes())
    return _service_action_with_recovery("restart")


def service_action(action: str) -> CommandResult:
    return _service_action_with_recovery(action)


def service_enable(enable: bool) -> CommandResult:
    action = "enable" if enable else "disable"
    result = _run(["systemctl", action, "meshtasticd"])
    if enable:
        restart = _service_action_with_recovery("restart")
        if restart.returncode != 0:
            parts = [entry.strip() for entry in (result.stdout, restart.stdout) if entry.strip()]
            return CommandResult(returncode=restart.returncode, stdout="\n".join(parts) or "Failed to restart meshtasticd.")
    else:
        _run(["systemctl", "stop", "meshtasticd"])
    return result


def service_status() -> CommandResult:
    return _run(["systemctl", "is-active", "meshtasticd"])


def mesh_test(session: MeshtasticSession | None = None) -> MeshtasticResult:
    def _build(interface: TCPInterface) -> MeshtasticResult:
        interface.sendText("test", channelIndex=0, wantAck=True)
        try:
            interface.waitForAckNak()
            return MeshtasticResult(returncode=0, stdout="Mesh connectivity confirmed.")
        except MeshInterface.MeshInterfaceError:
            return MeshtasticResult(returncode=1, stdout="Mesh connectivity failed.")

    return _run_meshtastic_action(
        _build,
        wait_for_config=True,
        session=session,
    )


def i2c_state(state: str) -> CommandResult:
    content = ""
    if MESHTASTIC_CONFIG_PATH.exists():
        content = MESHTASTIC_CONFIG_PATH.read_text(encoding="utf-8")
    if state == "enable":
        if "I2C:" not in content:
            content = content.rstrip() + "\nI2C:\n  I2CDevice: /dev/i2c-3\n"
            MESHTASTIC_CONFIG_PATH.write_text(content, encoding="utf-8")
        return _service_action_with_recovery("restart")
    if state == "disable":
        content = re.sub(r"I2C:.*?I2CDevice: /dev/i2c-3\\n", "", content, flags=re.DOTALL)
        MESHTASTIC_CONFIG_PATH.write_text(content, encoding="utf-8")
        return _service_action_with_recovery("restart")
    if state == "check":
        if "I2C:" in content and "I2CDevice: /dev/i2c-3" in content:
            return CommandResult(returncode=0, stdout="enabled")
        return CommandResult(returncode=1, stdout="disabled")
    return CommandResult(returncode=1, stdout="Invalid i2c state.")


def upgrade(stream: bool = False) -> CommandResult:
    runner = _run_live if stream else _run
    runner(["apt", "update"])
    return runner(["apt", "install", "--only-upgrade", "meshtasticd"])


def uninstall(stream: bool = False) -> CommandResult:
    runner = _run_live if stream else _run
    return runner(["apt", "remove", "-y", "meshtasticd"])
