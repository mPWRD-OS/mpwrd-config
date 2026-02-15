from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
import os

from mpwrd_config.core import DEFAULT_CONFIG_PATH, load_config
from mpwrd_config.system import CommandResult, _run, list_wifi_interfaces


LOG_FILE = Path("/var/log/meshtastic_wifi.log")
WIFI_STATE_FILE = Path("/etc/wifi_state.txt")
PROTO_FILE = Path("/root/.portduino/default/prefs/config.proto")


@dataclass
class WifiMeshResult:
    returncode: int
    stdout: str


def _log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as handle:
        handle.write(f"{time.ctime()} - {message}\n")


def _config_path() -> Path:
    return Path(os.getenv("MPWRD_CONFIG_PATH") or DEFAULT_CONFIG_PATH)


def _select_wifi_interface() -> str:
    interfaces = list_wifi_interfaces()
    config = load_config(_config_path())
    preferred = config.networking.wifi_interface
    if preferred and preferred in interfaces:
        return preferred
    if len(interfaces) == 1:
        return interfaces[0]
    return ""


def get_mobile_wifi_state() -> str:
    if not PROTO_FILE.exists():
        return "down"
    result = _run(["/bin/sh", "-c", f"cat {PROTO_FILE} | protoc --decode_raw"])
    state = "down"
    in_section = False
    for line in result.stdout.splitlines():
        if re.match(r"\s*4\s*\{", line):
            in_section = True
            continue
        if in_section and line.strip().startswith("}"):
            in_section = False
        if in_section:
            match = re.match(r"\s*1:\s*(\d+)", line)
            if match:
                state = "up" if match.group(1) == "1" else "down"
                break
    return state


def set_mobile_wifi_state(state: str) -> None:
    if state == "up":
        result = _run(["meshtastic", "--host", "127.0.0.1", "--set", "network.wifi_enabled", "true"])
    else:
        result = _run(["meshtastic", "--host", "127.0.0.1", "--set", "network.wifi_enabled", "false"])
    _log(f"Set Meshtastic Wi-Fi state to {state}. Output: {result.stdout.strip()}")


def set_wlan_state(state: str) -> None:
    iface = _select_wifi_interface()
    if not iface:
        _log("No Wi-Fi interface selected; skipping Wi-Fi state change.")
        return
    if state == "up":
        _run(["ip", "link", "set", iface, "up"])
        _log(f"Set {iface} UP.")
    else:
        _run(["ip", "link", "set", iface, "down"])
        _log(f"Set {iface} DOWN.")


def validate_wifi_state_file() -> None:
    if not WIFI_STATE_FILE.exists():
        WIFI_STATE_FILE.write_text("up", encoding="utf-8")
        return
    state = WIFI_STATE_FILE.read_text(encoding="utf-8").strip()
    if state not in {"up", "down"}:
        WIFI_STATE_FILE.write_text("up", encoding="utf-8")
        _log("Invalid wifi_state.txt content. Defaulting to up.")


def _current_wlan_state() -> str:
    iface = _select_wifi_interface()
    if not iface:
        return "down"
    path = Path(f"/sys/class/net/{iface}/operstate")
    if not path.exists():
        return "down"
    return path.read_text(encoding="utf-8").strip()


def sync_states() -> None:
    text_state = WIFI_STATE_FILE.read_text(encoding="utf-8").strip()
    mobile_state = get_mobile_wifi_state()
    if text_state != mobile_state:
        set_mobile_wifi_state(text_state)
        _log(f"Synced mobile Wi-Fi state to {text_state}.")
    current_wlan_state = _current_wlan_state()
    if text_state != current_wlan_state:
        set_wlan_state(text_state)
        _log(f"Synced Wi-Fi state to {text_state}.")


def monitor_changes() -> None:
    previous_mobile_state = get_mobile_wifi_state()
    previous_wlan_state = _current_wlan_state()
    while True:
        pid_result = _run(["/bin/sh", "-c", "ps -C meshtasticd -o pid="])
        pid = pid_result.stdout.strip()
        if pid:
            lsof_result = _run(["/bin/sh", "-c", "lsof /dev/spidev0.0"])
            has_lora = pid in lsof_result.stdout
        else:
            has_lora = False
        iface = _select_wifi_interface()
        wlan_exists = bool(iface) and Path(f"/sys/class/net/{iface}").exists()
        meshtastic_running = _run(["systemctl", "is-active", "--quiet", "meshtasticd"]).returncode == 0
        if pid and has_lora and wlan_exists and meshtastic_running:
            current_mobile_state = get_mobile_wifi_state()
            current_wlan_state = _current_wlan_state()
            if current_mobile_state != previous_mobile_state:
                _log(f"Detected mobile Wi-Fi state change: {previous_mobile_state} -> {current_mobile_state}")
                WIFI_STATE_FILE.write_text(current_mobile_state, encoding="utf-8")
                set_wlan_state(current_mobile_state)
                previous_mobile_state = current_mobile_state
            if current_wlan_state != previous_wlan_state:
                _log(f"Detected Wi-Fi state change: {previous_wlan_state} -> {current_wlan_state}")
                WIFI_STATE_FILE.write_text(current_wlan_state, encoding="utf-8")
                set_mobile_wifi_state(current_wlan_state)
                previous_wlan_state = current_wlan_state
        time.sleep(5)


def run() -> WifiMeshResult:
    validate_wifi_state_file()
    sync_states()
    monitor_changes()
    return WifiMeshResult(returncode=0, stdout="running")


def sync_once() -> WifiMeshResult:
    validate_wifi_state_file()
    sync_states()
    return WifiMeshResult(returncode=0, stdout="synced")
