from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


WPA_SUPPLICANT_PATH = Path("/etc/wpa_supplicant/wpa_supplicant.conf")
WIFI_STATE_PATH = Path("/etc/wifi_state.txt")
HOSTS_PATH = Path("/etc/hosts")
TTYD_KEY_PATH = Path("/etc/ssl/private/ttyd.key")
TTYD_CERT_PATH = Path("/etc/ssl/certs/ttyd.crt")
WEB_KEY_PATH = Path("/etc/ssl/private/mpwrd-config-web.key")
WEB_CERT_PATH = Path("/etc/ssl/certs/mpwrd-config-web.crt")


@dataclass
class CommandResult:
    returncode: int
    stdout: str


def _run(command: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return CommandResult(returncode=result.returncode, stdout=result.stdout)
    except FileNotFoundError:
        return CommandResult(returncode=127, stdout=f"command not found: {command[0]}")
    except PermissionError:
        return CommandResult(returncode=126, stdout=f"permission denied: {command[0]}")


def _list_interfaces() -> list[str]:
    try:
        return sorted(os.listdir("/sys/class/net"))
    except FileNotFoundError:
        return []


def _is_wireless(interface: str) -> bool:
    if not interface or interface == "lo":
        return False
    if Path(f"/sys/class/net/{interface}/wireless").exists():
        return True
    if interface.startswith("wl"):
        return True
    if shutil.which("iw"):
        info = _run(["iw", "dev"]).stdout
        return bool(re.search(rf"Interface\\s+{re.escape(interface)}\\b", info))
    return False


def list_wifi_interfaces() -> list[str]:
    return [iface for iface in _list_interfaces() if _is_wireless(iface) and is_physical_interface(iface)]


def list_ethernet_interfaces() -> list[str]:
    return [
        iface
        for iface in _list_interfaces()
        if iface != "lo" and not _is_wireless(iface) and is_physical_interface(iface)
    ]


def is_physical_interface(interface: str) -> bool:
    return Path(f"/sys/class/net/{interface}/device").exists()


def _resolve_wifi_interface(preferred: str | None = None) -> tuple[str | None, str | None]:
    interfaces = list_wifi_interfaces()
    if preferred:
        if preferred in interfaces:
            return preferred, None
        return None, f"Wi-Fi interface '{preferred}' not found. Available: {', '.join(interfaces) or 'none'}."
    if len(interfaces) == 1:
        return interfaces[0], None
    if not interfaces:
        return None, "No Wi-Fi interface detected."
    return None, f"Multiple Wi-Fi interfaces detected: {', '.join(interfaces)}. Select one."


def _resolve_ethernet_interface(preferred: str | None = None) -> tuple[str | None, str | None]:
    interfaces = list_ethernet_interfaces()
    if preferred:
        if preferred in interfaces:
            return preferred, None
        return None, f"Ethernet interface '{preferred}' not found. Available: {', '.join(interfaces) or 'none'}."
    physical = [iface for iface in interfaces if is_physical_interface(iface)]
    if len(physical) == 1:
        return physical[0], None
    if len(interfaces) == 1:
        return interfaces[0], None
    if not interfaces:
        return None, "No ethernet interface detected."
    if physical:
        return None, f"Multiple ethernet interfaces detected: {', '.join(physical)}. Select one."
    return None, f"Multiple ethernet interfaces detected: {', '.join(interfaces)}. Select one."


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _update_hosts(old_hostname: str, new_hostname: str) -> None:
    if not HOSTS_PATH.exists():
        return
    content = _read_text(HOSTS_PATH)
    updated = content
    if old_hostname:
        updated = re.sub(rf"\\b{re.escape(old_hostname)}\\b", new_hostname, updated)
    if new_hostname not in updated:
        updated = updated.rstrip() + f"\n127.0.1.1 {new_hostname}\n"
    if updated != content:
        _write_text(HOSTS_PATH, updated)


def _regenerate_ttyd_cert(hostname: str) -> CommandResult | None:
    if shutil.which("openssl") is None:
        return None
    TTYD_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    TTYD_CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:4096",
            "-days",
            "3650",
            "-nodes",
            "-x509",
            "-keyout",
            str(TTYD_KEY_PATH),
            "-out",
            str(TTYD_CERT_PATH),
            "-subj",
            f"/CN={hostname}",
            "-addext",
            f"subjectAltName=DNS:{hostname}",
        ]
    )
    if result.returncode == 0:
        try:
            os.chmod(TTYD_KEY_PATH, 0o600)
            os.chmod(TTYD_CERT_PATH, 0o644)
        except PermissionError:
            pass
        if _run(["systemctl", "is-enabled", "ttyd"]).returncode == 0:
            _run(["systemctl", "restart", "ttyd"])
    return result


def _regenerate_web_cert(hostname: str) -> CommandResult | None:
    if shutil.which("openssl") is None:
        return None
    WEB_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_CERT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:4096",
            "-days",
            "3650",
            "-nodes",
            "-x509",
            "-keyout",
            str(WEB_KEY_PATH),
            "-out",
            str(WEB_CERT_PATH),
            "-subj",
            f"/CN={hostname}",
            "-addext",
            f"subjectAltName=DNS:{hostname}",
        ]
    )
    if result.returncode == 0:
        try:
            os.chmod(WEB_KEY_PATH, 0o600)
            os.chmod(WEB_CERT_PATH, 0o644)
        except PermissionError:
            pass
    return result


def ensure_web_ssl() -> CommandResult:
    hostname = socket.gethostname()
    if WEB_KEY_PATH.exists() and WEB_CERT_PATH.exists():
        return CommandResult(returncode=0, stdout="Web UI SSL cert already present.")
    result = _regenerate_web_cert(hostname)
    if result is None:
        return CommandResult(returncode=1, stdout="openssl not found")
    return result


def set_hostname(hostname: str) -> CommandResult:
    old_hostname = socket.gethostname()
    result = _run(["hostnamectl", "set-hostname", hostname])
    _update_hosts(old_hostname, hostname)
    _run(["systemctl", "restart", "avahi-daemon"])
    _regenerate_ttyd_cert(hostname)
    _regenerate_web_cert(hostname)
    message = result.stdout.strip() or f"Hostname set to {hostname}."
    return CommandResult(returncode=result.returncode, stdout=message)


def set_wifi_credentials(
    ssid: str,
    psk: str,
    country: str | None,
    apply: bool = True,
    interface: str | None = None,
) -> CommandResult:
    content = _read_text(WPA_SUPPLICANT_PATH)
    if not content:
        content = "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev\nupdate_config=1\n\n"
    if country:
        if re.search(r"^country=", content, flags=re.MULTILINE):
            content = re.sub(r"^country=.*$", f"country={country}", content, flags=re.MULTILINE)
        else:
            content = f"country={country}\n{content}"
    if "network=" not in content:
        content += (
            "network={\n"
            f'    ssid="{ssid}"\n'
            f'    psk="{psk}"\n'
            "}\n"
        )
    else:
        content = re.sub(r'^\s*ssid=".*"$', f'    ssid="{ssid}"', content, flags=re.MULTILINE)
        content = re.sub(r'^\s*psk=".*"$', f'    psk="{psk}"', content, flags=re.MULTILINE)
    _write_text(WPA_SUPPLICANT_PATH, content)
    messages = ["Wi-Fi credentials updated."]
    returncode = 0
    if apply:
        state_result = wifi_state("up", interface=interface)
        returncode = max(returncode, state_result.returncode)
        if state_result.stdout.strip():
            messages.append(state_result.stdout.strip())
        iface, error = _resolve_wifi_interface(interface)
        if error:
            messages.append(error)
            return CommandResult(returncode=1, stdout="\n".join(line for line in messages if line))
        if iface and shutil.which("wpa_cli"):
            reconfig = _run(["wpa_cli", "-i", iface, "reconfigure"])
            if reconfig.stdout.strip():
                messages.append(reconfig.stdout.strip())
            returncode = max(returncode, reconfig.returncode)
    return CommandResult(returncode=returncode, stdout="\n".join(line for line in messages if line))


def _read_wifi_state() -> str | None:
    if not WIFI_STATE_PATH.exists():
        return None
    state = WIFI_STATE_PATH.read_text(encoding="utf-8").strip()
    return state if state in {"up", "down"} else None


def _write_wifi_state(state: str) -> None:
    if state not in {"up", "down"}:
        return
    _write_text(WIFI_STATE_PATH, state)


def _ensure_wifi_state(interface: str) -> str:
    state = _read_wifi_state()
    if state:
        return state
    status = _run(["ip", "link", "show", interface])
    state = "up" if "state UP" in status.stdout else "down"
    _write_wifi_state(state)
    return state


def wifi_state(state: str, interface: str | None = None) -> CommandResult:
    if state not in {"up", "down"}:
        return CommandResult(returncode=1, stdout=f"Invalid Wi-Fi state: {state}")
    iface, error = _resolve_wifi_interface(interface)
    if error:
        return CommandResult(returncode=1, stdout=error)
    result = _run(["ip", "link", "set", iface, state])
    if result.returncode == 0:
        _write_wifi_state(state)
        if not result.stdout.strip():
            return CommandResult(returncode=0, stdout=f"Wi-Fi {iface} set to {state}.")
    return result


def wifi_toggle(interface: str | None = None) -> CommandResult:
    iface, error = _resolve_wifi_interface(interface)
    if error:
        return CommandResult(returncode=1, stdout=error)
    state = _ensure_wifi_state(iface)
    desired = "down" if state == "up" else "up"
    return wifi_state(desired, interface=iface)


def wifi_restart(interface: str | None = None) -> CommandResult:
    result_lines = []
    returncode = 0
    state_result = wifi_state("up", interface=interface)
    returncode = max(returncode, state_result.returncode)
    if state_result.stdout.strip():
        result_lines.append(state_result.stdout.strip())
    iface, error = _resolve_wifi_interface(interface)
    if error:
        result_lines.append(error)
        return CommandResult(returncode=1, stdout="\n".join(line for line in result_lines if line))
    if iface and shutil.which("wpa_cli"):
        reconfig = _run(["wpa_cli", "-i", iface, "reconfigure"])
        if reconfig.stdout.strip():
            result_lines.append(reconfig.stdout.strip())
        returncode = max(returncode, reconfig.returncode)
    return CommandResult(returncode=returncode, stdout="\n".join(line for line in result_lines if line))


def wifi_status(interface: str | None = None) -> CommandResult:
    config_ssid = ""
    country = ""
    content = _read_text(WPA_SUPPLICANT_PATH)
    ssid_match = re.search(r'^\\s*ssid=\"([^\"]+)\"', content, flags=re.MULTILINE)
    if ssid_match:
        config_ssid = ssid_match.group(1)
    country_match = re.search(r"^country=([A-Za-z]{2})", content, flags=re.MULTILINE)
    if country_match:
        country = country_match.group(1)

    config_lines = [
        f"SSID:{config_ssid or 'unknown'}",
        "Password:(hidden)",
        f"Country:{country or 'unknown'}",
    ]

    iface, error = _resolve_wifi_interface(interface)
    if error:
        return CommandResult(
            returncode=1,
            stdout=f"Wi-Fi status:{error}\n\n" + "\n".join(config_lines),
        )

    state = _read_wifi_state() or ("up" if "state UP" in _run(["ip", "link", "show", iface]).stdout else "down")
    if not _read_wifi_state():
        _write_wifi_state(state)
    if state != "up":
        return CommandResult(
            returncode=0,
            stdout="Wi-Fi status:disabled\n"
            f"Interface:{iface}\n\n"
            + "\n".join(config_lines),
        )

    ssid = "none"
    signal = "unknown"
    if shutil.which("iw"):
        info = _run(["iw", "dev", iface, "link"]).stdout
        if "Not connected" not in info:
            for line in info.splitlines():
                if line.strip().startswith("SSID:"):
                    ssid = line.split("SSID:", 1)[1].strip() or "none"
                if line.strip().startswith("signal:"):
                    signal = line.split("signal:", 1)[1].strip()
    elif shutil.which("iwconfig"):
        info = _run(["iwconfig", iface]).stdout
        ssid_match = re.search(r'ESSID:\"([^\"]*)\"', info)
        if ssid_match:
            ssid = ssid_match.group(1) or "none"
        signal_match = re.search(r"Signal level=([^ ]+)", info)
        if signal_match:
            signal = signal_match.group(1)

    ip_addr = ""
    ip_result = _run(["ip", "-4", "addr", "show", "dev", iface])
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ip_result.stdout)
    if match:
        ip_addr = match.group(1)
    mac = _read_text(Path(f"/sys/class/net/{iface}/address")).strip() or "unknown"

    lines = [
        "Wi-Fi status:enabled",
        f"Interface:{iface}",
        f"Connected to:{ssid}",
        f"Sig. strength:{signal}",
        f"Current IP:{ip_addr or 'none'}",
        f"Hostname:{socket.gethostname()}.local",
        f"MAC address:{mac}",
        "",
        *config_lines,
    ]
    return CommandResult(returncode=0, stdout="\n".join(lines))


def ethernet_status(interface: str | None = None) -> CommandResult:
    iface, error = _resolve_ethernet_interface(interface)
    if error:
        return CommandResult(returncode=1, stdout=f"Ethernet status:{error}")
    status = _run(["ip", "link", "show", iface])
    if status.returncode != 0:
        return status
    state = "connected" if "state UP" in status.stdout else "disconnected"
    ip_addr = ""
    ip_result = _run(["ip", "-4", "addr", "show", "dev", iface])
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ip_result.stdout)
    if match:
        ip_addr = match.group(1)
    ipv6_addr = ""
    ip6_result = _run(["ip", "-6", "addr", "show", "dev", iface])
    match6 = re.search(r"inet6 ([0-9a-f:]+)", ip6_result.stdout)
    if match6:
        ipv6_addr = match6.group(1)
    mac = _read_text(Path(f"/sys/class/net/{iface}/address")).strip() or "unknown"
    lines = [
        f"Eth status:{state}",
        f"Interface:{iface}",
        f"IPv4 Address:{ip_addr or 'none'}",
        f"IPv6 Address:{ipv6_addr or 'none'}",
        f"MAC Address:{mac}",
        f"Hostname:{socket.gethostname()}.local",
    ]
    return CommandResult(returncode=0, stdout="\n".join(lines))


def ip_addresses() -> CommandResult:
    if shutil.which("ip"):
        result = _run(["ip", "-o", "addr", "show"])
        if result.returncode != 0:
            return result
        by_iface: dict[str, list[str]] = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            iface = parts[1]
            family = parts[2]
            if iface == "lo" or not is_physical_interface(iface):
                continue
            addr = parts[3]
            label = "IPv4" if family == "inet" else "IPv6" if family == "inet6" else family
            by_iface.setdefault(iface, []).append(f"{label} {addr}")
        if not by_iface:
            return CommandResult(returncode=0, stdout="none")
        lines = []
        for iface in sorted(by_iface):
            lines.append(f"{iface}: " + ", ".join(by_iface[iface]))
        return CommandResult(returncode=0, stdout="\n".join(lines))
    result = _run(["hostname", "-I"])
    if result.returncode != 0:
        return result
    addresses = [part for part in result.stdout.split() if part]
    return CommandResult(returncode=0, stdout=", ".join(addresses) if addresses else "none")


def test_internet(targets: Iterable[str] = ("1.1.1.1", "8.8.8.8")) -> CommandResult:
    success = 0
    total = 0
    errors: list[str] = []
    ping_available = shutil.which("ping") is not None
    if ping_available:
        for target in targets:
            result = _run(["ping", "-c", "5", "-W", "1", target])
            total += 5
            if result.returncode == 127:
                errors.append(result.stdout.strip())
                continue
            success += len(re.findall(r"time=", result.stdout))
    else:
        errors.append("ping: unavailable")

    dns_host = "connectivitycheck.gstatic.com"
    dns_ok = False
    if shutil.which("getent"):
        dns_ok = _run(["getent", "hosts", dns_host]).returncode == 0

    http_url = f"http://{dns_host}/generate_204"
    http_ok = False
    if shutil.which("curl"):
        http_ok = _run(["curl", "-fsS", "--max-time", "5", http_url]).returncode == 0
    elif shutil.which("wget"):
        http_ok = _run(["wget", "-q", "--timeout=5", "--spider", http_url]).returncode == 0

    ping_ok = success > 0
    status_lines = [
        f"Ping: {success}/{total} responses" if ping_available else "Ping: unavailable",
        f"DNS: {'ok' if dns_ok else 'failed'}" if shutil.which("getent") else "DNS: unavailable",
        f"HTTP: {'ok' if http_ok else 'failed'}" if (shutil.which("curl") or shutil.which("wget")) else "HTTP: unavailable",
    ]

    if http_ok or (ping_ok and dns_ok):
        target_list = ", ".join(targets)
        summary = (
            "Internet connection is up.\n\n"
            f"Pinged {target_list}.\n"
            f"Received {success}/{total} responses.\n\n"
            + "\n".join(status_lines)
        )
        return CommandResult(returncode=0, stdout=summary)

    if ping_ok and not dns_ok:
        return CommandResult(
            returncode=1,
            stdout="Network reachable but DNS failed.\n\n" + "\n".join(status_lines),
        )

    if errors and success == 0 and http_ok is False:
        return CommandResult(returncode=1, stdout="\n".join(errors + status_lines))
    return CommandResult(returncode=1, stdout="No internet connection detected.\n\n" + "\n".join(status_lines))


def system_reboot() -> CommandResult:
    return _run(["reboot"])


def system_shutdown() -> CommandResult:
    return _run(["shutdown", "-h", "now"])
