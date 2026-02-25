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
NETPLAN_WIFI_PATH = Path("/etc/netplan/90-mpwrd-config.yaml")
HOSTS_PATH = Path("/etc/hosts")
TTYD_KEY_PATH = Path("/etc/ssl/private/ttyd.key")
TTYD_CERT_PATH = Path("/etc/ssl/certs/ttyd.crt")
WEB_KEY_PATH = Path("/etc/ssl/private/mpwrd-config-web.key")
WEB_CERT_PATH = Path("/etc/ssl/certs/mpwrd-config-web.crt")
EXTRA_BIN_PATHS = ("/usr/local/sbin", "/usr/sbin", "/sbin")


@dataclass
class CommandResult:
    returncode: int
    stdout: str


@dataclass
class WifiScanNetwork:
    ssid: str
    signal_dbm: float | None = None
    signal_percent: int | None = None
    security: str = "unknown"

    def signal_label(self) -> str:
        if self.signal_percent is not None:
            return f"{self.signal_percent}%"
        if self.signal_dbm is not None:
            return f"{self.signal_dbm:.0f} dBm"
        return "unknown"


def _netplan_other_configs() -> list[Path]:
    netplan_dir = Path("/etc/netplan")
    if not netplan_dir.exists():
        return []
    configs: list[Path] = []
    for path in sorted(netplan_dir.iterdir()):
        if path == NETPLAN_WIFI_PATH:
            continue
        if path.suffix.lower() in {".yaml", ".yml"}:
            configs.append(path)
    return configs


def _normalize_wifi_networks(
    ssid: str,
    psk: str,
    networks: Sequence[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    if networks:
        for entry_ssid, entry_psk in networks:
            cleaned_ssid = str(entry_ssid or "").strip()
            if not cleaned_ssid:
                continue
            entries.append((cleaned_ssid, str(entry_psk or "")))
    else:
        cleaned_ssid = str(ssid or "").strip()
        if cleaned_ssid:
            entries.append((cleaned_ssid, str(psk or "")))
    primary = str(ssid or "").strip()
    if primary and not any(item[0] == primary for item in entries):
        entries.insert(0, (primary, str(psk or "")))
    deduped: dict[str, str] = {}
    for entry_ssid, entry_psk in entries:
        if entry_ssid not in deduped:
            deduped[entry_ssid] = entry_psk
        else:
            deduped[entry_ssid] = entry_psk
    return list(deduped.items())


def _resolve_wifi_ip_config(
    dhcp4: bool | None,
    address: str | None,
    gateway: str | None,
    nameservers: Sequence[str] | None,
) -> tuple[bool, str, str, list[str]]:
    resolved_dhcp4 = dhcp4 if dhcp4 is not None else True
    resolved_address = address if address is not None else ""
    resolved_gateway = gateway if gateway is not None else ""
    resolved_nameservers = list(nameservers) if nameservers is not None else []

    if dhcp4 is None or address is None or gateway is None or nameservers is None:
        try:
            from mpwrd_config.core import DEFAULT_CONFIG_PATH, load_config

            cfg_path = Path(os.getenv("MPWRD_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
            cfg = load_config(cfg_path)
            if dhcp4 is None:
                resolved_dhcp4 = cfg.networking.wifi_dhcp4
            if address is None:
                resolved_address = cfg.networking.wifi_address
            if gateway is None:
                resolved_gateway = cfg.networking.wifi_gateway
            if nameservers is None:
                resolved_nameservers = list(cfg.networking.wifi_nameservers)
        except Exception:
            pass

    resolved_nameservers = [entry for entry in resolved_nameservers if entry]
    return resolved_dhcp4, resolved_address, resolved_gateway, resolved_nameservers

def _find_command(name: str) -> str | None:
    path_entries = [entry for entry in os.environ.get("PATH", "").split(os.pathsep) if entry]
    for extra in EXTRA_BIN_PATHS:
        if extra not in path_entries:
            path_entries.append(extra)
    return shutil.which(name, path=os.pathsep.join(path_entries))


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


def _run_live(command: Sequence[str]) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            check=False,
            text=True,
        )
        return CommandResult(returncode=result.returncode, stdout="")
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
    iw_cmd = _find_command("iw")
    if iw_cmd:
        info = _run([iw_cmd, "dev"]).stdout
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


def _service_is_active(service: str) -> bool:
    return _run(["systemctl", "is-active", "--quiet", service]).returncode == 0


def _detect_network_backend() -> str:
    has_netplan = Path("/etc/netplan").exists() and _find_command("netplan") is not None
    has_nm = _find_command("nmcli") is not None
    if has_netplan and _service_is_active("systemd-networkd"):
        return "netplan"
    if has_nm and _service_is_active("NetworkManager"):
        return "networkmanager"
    if has_netplan:
        return "netplan"
    if has_nm:
        return "networkmanager"
    return "legacy"


def _yaml_quote(value: str) -> str:
    sanitized = value.replace("\r", " ").replace("\n", " ")
    return "'" + sanitized.replace("'", "''") + "'"


def _write_wpa_supplicant_config(networks: Sequence[tuple[str, str]], country: str | None) -> None:
    lines = ["ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev", "update_config=1"]
    if country:
        lines.insert(0, f"country={country}")
    for ssid, psk in networks:
        if not ssid:
            continue
        lines.append("")
        lines.append("network={")
        lines.append(f'    ssid="{ssid}"')
        if psk:
            lines.append(f'    psk="{psk}"')
        else:
            lines.append("    key_mgmt=NONE")
        lines.append("}")
    _write_text(WPA_SUPPLICANT_PATH, "\n".join(lines).rstrip() + "\n")


def _write_netplan_wifi_config(
    interface: str,
    networks: Sequence[tuple[str, str]],
    country: str | None,
    dhcp4: bool,
    address: str,
    gateway: str,
    nameservers: Sequence[str],
) -> CommandResult:
    iface = _yaml_quote(interface)
    lines = [
        "network:",
        "  version: 2",
        "  renderer: networkd",
        "  wifis:",
        f"    {iface}:",
        "      optional: true",
    ]
    if dhcp4:
        lines.append("      dhcp4: true")
    else:
        lines.append("      dhcp4: false")
        if not address:
            return CommandResult(returncode=1, stdout="Static IP requires an address (CIDR).")
        lines.append(f"      addresses: [{address}]")
        if nameservers:
            joined = ", ".join(nameservers)
            lines.append("      nameservers:")
            lines.append(f"        addresses: [{joined}]")
        if gateway:
            lines.append("      routes:")
            lines.append("        - to: default")
            lines.append(f"          via: {gateway}")
    if country:
        lines.append(f"      regulatory-domain: {_yaml_quote(country)}")
    lines.append("      access-points:")
    for ssid, psk in networks:
        if not ssid:
            continue
        ssid_key = _yaml_quote(ssid)
        if psk:
            lines.append(f"        {ssid_key}:")
            lines.append(f"          password: {_yaml_quote(psk)}")
        else:
            lines.append(f"        {ssid_key}: {{}}")
    content = "\n".join(lines) + "\n"
    _write_text(NETPLAN_WIFI_PATH, content)
    warning = ""
    others = _netplan_other_configs()
    if others:
        names = ", ".join(path.name for path in others)
        warning = f"Warning: other netplan configs detected: {names}."
    message = f"Netplan Wi-Fi config updated: {NETPLAN_WIFI_PATH}"
    if warning:
        message = f"{message}\n{warning}"
    return CommandResult(returncode=0, stdout=message)


def _nm_profile_name(interface: str) -> str:
    return f"mpwrd-config-{interface}"


def _configure_networkmanager_wifi(
    interface: str,
    ssid: str,
    psk: str,
    dhcp4: bool,
    address: str,
    gateway: str,
    nameservers: Sequence[str],
) -> CommandResult:
    nmcli_cmd = _find_command("nmcli")
    if not nmcli_cmd:
        return CommandResult(returncode=127, stdout="nmcli not found.")
    profile = _nm_profile_name(interface)
    exists = _run([nmcli_cmd, "-g", "NAME", "connection", "show", profile])
    if exists.returncode != 0:
        created = _run([nmcli_cmd, "connection", "add", "type", "wifi", "ifname", interface, "con-name", profile, "ssid", ssid])
        if created.returncode != 0:
            return created
    modify_cmd = [
        nmcli_cmd,
        "connection",
        "modify",
        profile,
        "connection.interface-name",
        interface,
        "802-11-wireless.ssid",
        ssid,
    ]
    if psk:
        modify_cmd.extend(
            [
                "802-11-wireless-security.key-mgmt",
                "wpa-psk",
                "802-11-wireless-security.psk",
                psk,
            ]
        )
    else:
        modify_cmd.extend(["802-11-wireless-security.key-mgmt", "none", "802-11-wireless-security.psk", ""])
    if dhcp4:
        modify_cmd.extend(["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""])
    else:
        if not address:
            return CommandResult(returncode=1, stdout="Static IP requires an address (CIDR).")
        modify_cmd.extend(["ipv4.method", "manual", "ipv4.addresses", address])
        if gateway:
            modify_cmd.extend(["ipv4.gateway", gateway])
        if nameservers:
            modify_cmd.extend(["ipv4.dns", ",".join(nameservers)])
    modified = _run(modify_cmd)
    if modified.returncode != 0:
        return modified
    return CommandResult(returncode=0, stdout=f"NetworkManager profile updated: {profile}")


def _wifi_state_from_nmcli(interface: str) -> str | None:
    nmcli_cmd = _find_command("nmcli")
    if not nmcli_cmd:
        return None
    status = _run([nmcli_cmd, "-t", "-f", "DEVICE,STATE", "device", "status"])
    if status.returncode != 0:
        return None
    for line in status.stdout.splitlines():
        if ":" not in line:
            continue
        device, state = line.split(":", 1)
        if device != interface:
            continue
        state = state.strip().lower()
        if state in {"connected", "connecting"} or state.startswith("connected"):
            return "up"
        return "down"
    return None


def _current_wifi_state(interface: str, backend: str) -> str:
    if backend == "networkmanager":
        nm_state = _wifi_state_from_nmcli(interface)
        if nm_state in {"up", "down"}:
            return nm_state
    status = _run(["ip", "link", "show", interface])
    return "up" if "state UP" in status.stdout else "down"


def _freq_to_band_channel(freq_mhz: float) -> tuple[str | None, int | None]:
    if 2400 <= freq_mhz <= 2499:
        if freq_mhz == 2484:
            return "2.4GHz", 14
        return "2.4GHz", int(round((freq_mhz - 2407) / 5))
    if 5000 <= freq_mhz <= 5899:
        return "5GHz", int(round((freq_mhz - 5000) / 5))
    if 5925 <= freq_mhz <= 7125:
        return "6GHz", int(round((freq_mhz - 5950) / 5))
    return None, None


def _wifi_radio_info(interface: str) -> dict[str, str]:
    details: dict[str, str] = {}
    iw_cmd = _find_command("iw")
    if iw_cmd:
        info = _run([iw_cmd, "dev", interface, "info"]).stdout
        for raw in info.splitlines():
            line = raw.strip()
            if line.startswith("wiphy "):
                details["PHY"] = line.split("wiphy", 1)[1].strip()
            elif line.startswith("type "):
                details["Mode"] = line.split("type", 1)[1].strip()
            elif line.startswith("txpower "):
                details["Tx power"] = line.split("txpower", 1)[1].strip()
            elif line.startswith("channel ") and "width:" in line:
                match = re.search(r"width:\s*([0-9]+)\s*MHz", line)
                if match:
                    details["Channel width"] = f"{match.group(1)} MHz"
    ethtool_cmd = _find_command("ethtool")
    if ethtool_cmd:
        info = _run([ethtool_cmd, "-i", interface]).stdout
        for raw in info.splitlines():
            if ":" not in raw:
                continue
            key, value = raw.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if not value:
                continue
            if key == "driver":
                details["Driver"] = value
            elif key == "firmware-version":
                details["Firmware"] = value
            elif key == "bus-info":
                details["Bus info"] = value
    return details


def _connected_wifi_info(interface: str, backend: str) -> tuple[str, str, dict[str, str]]:
    nmcli_cmd = _find_command("nmcli")
    details: dict[str, str] = {}
    ssid = "none"
    signal = "unknown"
    if backend == "networkmanager" and nmcli_cmd:
        scan = _run([nmcli_cmd, "-t", "-f", "IN-USE,SSID,SIGNAL", "device", "wifi", "list", "ifname", interface])
        if scan.returncode == 0:
            for line in scan.stdout.splitlines():
                match = re.match(r"^([^:]*):(.*):([^:]*)$", line)
                if not match:
                    continue
                marker, ssid, signal = match.groups()
                if marker.strip() != "*":
                    continue
                ssid = ssid.strip() or "none"
                signal = f"{signal.strip() or 'unknown'}%"
                break
    iw_cmd = _find_command("iw")
    iwconfig_cmd = _find_command("iwconfig")
    if iw_cmd:
        info = _run([iw_cmd, "dev", interface, "link"]).stdout
        if "Not connected" not in info:
            for line in info.splitlines():
                stripped = line.strip()
                if stripped.startswith("Connected to"):
                    parts = stripped.split()
                    if len(parts) >= 3:
                        details["BSSID"] = parts[2]
                if line.strip().startswith("SSID:"):
                    if ssid == "none":
                        ssid = line.split("SSID:", 1)[1].strip() or "none"
                if stripped.startswith("signal:"):
                    if signal.startswith("unknown"):
                        signal = stripped.split("signal:", 1)[1].strip()
                if stripped.startswith("freq:"):
                    try:
                        freq = float(stripped.split("freq:", 1)[1].strip())
                    except ValueError:
                        continue
                    band, channel = _freq_to_band_channel(freq)
                    if band:
                        details["Band"] = band
                    if channel:
                        details["Channel"] = str(channel)
                if stripped.startswith("tx bitrate:"):
                    details["Bitrate"] = stripped.split("tx bitrate:", 1)[1].strip()
    elif iwconfig_cmd:
        info = _run([iwconfig_cmd, interface]).stdout
        ssid_match = re.search(r'ESSID:\"([^\"]*)\"', info)
        if ssid_match:
            ssid = ssid_match.group(1) or "none"
        signal_match = re.search(r"Signal level=([^ ]+)", info)
        if signal_match:
            if signal.startswith("unknown"):
                signal = signal_match.group(1)
        bssid_match = re.search(r"Access Point: ([0-9A-Fa-f:]{17}|Not-Associated)", info)
        if bssid_match and bssid_match.group(1) != "Not-Associated":
            details["BSSID"] = bssid_match.group(1)
        freq_match = re.search(r"Frequency:([0-9.]+) GHz", info)
        if freq_match:
            try:
                freq = float(freq_match.group(1)) * 1000
            except ValueError:
                freq = 0
            if freq:
                band, channel = _freq_to_band_channel(freq)
                if band:
                    details["Band"] = band
                if channel:
                    details["Channel"] = str(channel)
        rate_match = re.search(r"Bit Rate=([^ ]+ ?[A-Za-z/]+)", info)
        if rate_match:
            details["Bitrate"] = rate_match.group(1)
    return ssid, signal, details


def _dbm_to_percent(dbm: float) -> int:
    if dbm <= -100:
        return 0
    if dbm >= -50:
        return 100
    return int(round(2 * (dbm + 100)))


def _signal_score(network: WifiScanNetwork) -> int:
    if network.signal_percent is not None:
        return network.signal_percent
    if network.signal_dbm is not None:
        return _dbm_to_percent(network.signal_dbm)
    return -1


def _dedupe_scan_results(networks: list[WifiScanNetwork]) -> list[WifiScanNetwork]:
    by_ssid: dict[str, WifiScanNetwork] = {}
    for network in networks:
        if not network.ssid:
            continue
        existing = by_ssid.get(network.ssid)
        if not existing or _signal_score(network) > _signal_score(existing):
            by_ssid[network.ssid] = network
    return list(by_ssid.values())


def _parse_iw_scan(output: str) -> list[WifiScanNetwork]:
    networks: list[WifiScanNetwork] = []
    current: dict[str, object] | None = None

    def commit() -> None:
        nonlocal current
        if not current:
            return
        ssid = str(current.get("ssid") or "").strip()
        if not ssid:
            return
        signal_dbm = current.get("signal_dbm")
        security = "secure" if current.get("secure") else "open"
        networks.append(
            WifiScanNetwork(
                ssid=ssid,
                signal_dbm=signal_dbm if isinstance(signal_dbm, float) else None,
                security=security,
            )
        )

    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("BSS "):
            commit()
            current = {"ssid": "", "signal_dbm": None, "secure": False}
            continue
        if current is None:
            continue
        if line.startswith("SSID:"):
            current["ssid"] = line.split("SSID:", 1)[1].strip()
        elif line.startswith("signal:"):
            match = re.search(r"signal:\s*([-\d.]+)", line)
            if match:
                try:
                    current["signal_dbm"] = float(match.group(1))
                except ValueError:
                    pass
        elif line.startswith("capability:") and "Privacy" in line:
            current["secure"] = True
        elif line.startswith("RSN:") or line.startswith("WPA:"):
            current["secure"] = True

    commit()
    return networks


def _parse_iwlist_scan(output: str) -> list[WifiScanNetwork]:
    networks: list[WifiScanNetwork] = []
    current: dict[str, object] | None = None

    def commit() -> None:
        nonlocal current
        if not current:
            return
        ssid = str(current.get("ssid") or "").strip()
        if not ssid:
            return
        signal_dbm = current.get("signal_dbm")
        signal_percent = current.get("signal_percent")
        security = "secure" if current.get("secure") else "open"
        networks.append(
            WifiScanNetwork(
                ssid=ssid,
                signal_dbm=signal_dbm if isinstance(signal_dbm, float) else None,
                signal_percent=signal_percent if isinstance(signal_percent, int) else None,
                security=security,
            )
        )

    for raw in output.splitlines():
        line = raw.strip()
        if line.startswith("Cell "):
            commit()
            current = {"ssid": "", "signal_dbm": None, "signal_percent": None, "secure": False}
            continue
        if current is None:
            continue
        if "ESSID:" in line:
            ssid = line.split("ESSID:", 1)[1].strip().strip('"')
            current["ssid"] = ssid
        if "Encryption key:" in line and "on" in line:
            current["secure"] = True
        quality_match = re.search(r"Quality=([0-9]+)/([0-9]+)", line)
        if quality_match:
            try:
                quality = int(quality_match.group(1))
                total = int(quality_match.group(2))
                if total > 0:
                    current["signal_percent"] = int(round(quality * 100 / total))
            except ValueError:
                pass
        signal_match = re.search(r"Signal level=([-\d]+)\s*dBm", line)
        if signal_match:
            try:
                current["signal_dbm"] = float(signal_match.group(1))
            except ValueError:
                pass

    commit()
    return networks


def _parse_nmcli_scan(output: str) -> list[WifiScanNetwork]:
    networks: list[WifiScanNetwork] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([^:]*):([^:]*):?(.*)$", line)
        if not match:
            continue
        ssid, signal, security = match.groups()
        ssid = ssid.strip()
        if not ssid:
            continue
        signal_percent: int | None = None
        if signal.strip().isdigit():
            signal_percent = int(signal.strip())
        secure = bool(security.strip()) and security.strip() != "--"
        networks.append(
            WifiScanNetwork(
                ssid=ssid,
                signal_percent=signal_percent,
                security="secure" if secure else "open",
            )
        )
    return networks


def scan_wifi_networks(interface: str | None = None) -> tuple[list[WifiScanNetwork], str | None]:
    iface, error = _resolve_wifi_interface(interface)
    if error:
        return [], error
    backend = _detect_network_backend()
    iw_cmd = _find_command("iw")
    iwlist_cmd = _find_command("iwlist")
    nmcli_cmd = _find_command("nmcli")
    errors: list[str] = []
    results: list[WifiScanNetwork] = []

    if backend == "networkmanager" and nmcli_cmd:
        radio_state = _run([nmcli_cmd, "radio", "wifi"])
        if radio_state.returncode == 0:
            state = radio_state.stdout.strip().lower()
            if state in {"disabled", "off", "no"}:
                return [], "Wi-Fi radio is disabled. Enable Wi-Fi first."
        else:
            errors.append(radio_state.stdout.strip() or "nmcli radio query failed.")
    else:
        state = _current_wifi_state(iface, backend)
        if state != "up":
            return [], f"Wi-Fi interface {iface} is down. Enable Wi-Fi first."

    if backend == "networkmanager" and nmcli_cmd:
        scan = _run([nmcli_cmd, "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", iface])
        if scan.returncode == 0:
            results = _parse_nmcli_scan(scan.stdout)
        else:
            errors.append(scan.stdout.strip() or "nmcli scan failed.")
    else:
        if iw_cmd:
            scan = _run([iw_cmd, "dev", iface, "scan"])
            if scan.returncode == 0:
                results = _parse_iw_scan(scan.stdout)
            else:
                errors.append(scan.stdout.strip() or "iw scan failed.")
        if not results and iwlist_cmd:
            scan = _run([iwlist_cmd, iface, "scan"])
            if scan.returncode == 0:
                results = _parse_iwlist_scan(scan.stdout)
            else:
                errors.append(scan.stdout.strip() or "iwlist scan failed.")

    if not results:
        if not (iw_cmd or iwlist_cmd or nmcli_cmd):
            return [], "Wi-Fi scan tools not available. Use manual SSID entry."
        if errors:
            return [], "\n".join(error for error in errors if error)
    results = _dedupe_scan_results(results)
    results.sort(key=_signal_score, reverse=True)
    return results, None


def set_wifi_credentials(
    ssid: str,
    psk: str,
    country: str | None,
    apply: bool = True,
    interface: str | None = None,
    networks: Sequence[tuple[str, str]] | None = None,
    dhcp4: bool | None = None,
    address: str | None = None,
    gateway: str | None = None,
    nameservers: Sequence[str] | None = None,
) -> CommandResult:
    messages = ["Wi-Fi credentials updated."]
    returncode = 0
    backend = _detect_network_backend()
    normalized = _normalize_wifi_networks(ssid, psk, networks)
    resolved_dhcp4, resolved_address, resolved_gateway, resolved_nameservers = _resolve_wifi_ip_config(
        dhcp4,
        address,
        gateway,
        nameservers,
    )
    if backend == "legacy":
        _write_wpa_supplicant_config(normalized, country)
    iface: str | None = None
    iface_error: str | None = None
    if backend in {"netplan", "networkmanager"} or apply:
        iface, iface_error = _resolve_wifi_interface(interface)
        if iface_error:
            messages.append(iface_error)
            if apply:
                return CommandResult(returncode=1, stdout="\n".join(line for line in messages if line))
    if backend == "netplan" and iface:
        netplan_result = _write_netplan_wifi_config(
            iface,
            normalized,
            country,
            resolved_dhcp4,
            resolved_address,
            resolved_gateway,
            resolved_nameservers,
        )
        if netplan_result.stdout.strip():
            messages.append(netplan_result.stdout.strip())
        returncode = max(returncode, netplan_result.returncode)
    elif backend == "networkmanager" and iface:
        primary = next((entry for entry in normalized if entry[0] == ssid), None)
        if not primary and normalized:
            primary = normalized[0]
        if len(normalized) > 1:
            messages.append("Warning: NetworkManager backend applies only the primary SSID.")
        if primary:
            primary_ssid, primary_psk = primary
        else:
            primary_ssid, primary_psk = ssid, psk
        nm_result = _configure_networkmanager_wifi(
            iface,
            primary_ssid,
            primary_psk,
            resolved_dhcp4,
            resolved_address,
            resolved_gateway,
            resolved_nameservers,
        )
        if nm_result.stdout.strip():
            messages.append(nm_result.stdout.strip())
        returncode = max(returncode, nm_result.returncode)
        if nm_result.returncode != 0:
            return CommandResult(returncode=returncode, stdout="\n".join(line for line in messages if line))
    if apply:
        state_result = wifi_state("up", interface=iface or interface)
        returncode = max(returncode, state_result.returncode)
        if state_result.stdout.strip():
            messages.append(state_result.stdout.strip())
        if backend == "legacy" and iface:
            wpa_cli_cmd = _find_command("wpa_cli")
            if wpa_cli_cmd:
                reconfig = _run([wpa_cli_cmd, "-i", iface, "reconfigure"])
            else:
                reconfig = CommandResult(returncode=127, stdout="wpa_cli not found.")
            if reconfig.stdout.strip():
                messages.append(reconfig.stdout.strip())
            returncode = max(returncode, reconfig.returncode)
    return CommandResult(returncode=returncode, stdout="\n".join(line for line in messages if line))


def _write_wifi_state(state: str) -> None:
    if state not in {"up", "down"}:
        return
    _write_text(WIFI_STATE_PATH, state)


def _ensure_wifi_state(interface: str) -> str:
    backend = _detect_network_backend()
    state = _current_wifi_state(interface, backend)
    _write_wifi_state(state)
    return state


def wifi_state(state: str, interface: str | None = None) -> CommandResult:
    if state not in {"up", "down"}:
        return CommandResult(returncode=1, stdout=f"Invalid Wi-Fi state: {state}")
    iface, error = _resolve_wifi_interface(interface)
    if error:
        return CommandResult(returncode=1, stdout=error)
    backend = _detect_network_backend()
    if backend == "networkmanager":
        nmcli_cmd = _find_command("nmcli")
        if not nmcli_cmd:
            return CommandResult(returncode=127, stdout="nmcli not found.")
        if state == "up":
            radio = _run([nmcli_cmd, "radio", "wifi", "on"])
            if radio.returncode != 0:
                return radio
            message = radio.stdout.strip() or "Wi-Fi radio enabled."
            return CommandResult(returncode=0, stdout=message)
        else:
            radio = _run([nmcli_cmd, "radio", "wifi", "off"])
            if radio.returncode != 0:
                return radio
            message = radio.stdout.strip() or "Wi-Fi radio disabled."
            return CommandResult(returncode=0, stdout=message)
    elif backend == "netplan":
        result = _run(["ip", "link", "set", iface, state])
        if state == "up":
            netplan_cmd = _find_command("netplan")
            if netplan_cmd:
                applied = _run([netplan_cmd, "apply"])
            else:
                applied = CommandResult(returncode=127, stdout="netplan command not found.")
            if applied.returncode != 0:
                return applied
            if applied.stdout.strip():
                combined = "\n".join(part for part in (result.stdout.strip(), applied.stdout.strip()) if part)
                result = CommandResult(returncode=result.returncode, stdout=combined)
    else:
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
    backend = _detect_network_backend()
    if backend in {"networkmanager", "netplan"}:
        down = wifi_state("down", interface=interface)
        up = wifi_state("up", interface=interface)
        output = "\n".join(line for line in (down.stdout.strip(), up.stdout.strip()) if line)
        return CommandResult(returncode=max(down.returncode, up.returncode), stdout=output or "Done.")
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
    wpa_cli_cmd = _find_command("wpa_cli")
    if iface and wpa_cli_cmd:
        reconfig = _run([wpa_cli_cmd, "-i", iface, "reconfigure"])
        if reconfig.stdout.strip():
            result_lines.append(reconfig.stdout.strip())
        returncode = max(returncode, reconfig.returncode)
    return CommandResult(returncode=returncode, stdout="\n".join(line for line in result_lines if line))


def _read_wpa_supplicant_summary() -> tuple[list[str], str]:
    content = _read_text(WPA_SUPPLICANT_PATH)
    if not content:
        return [], ""
    ssids = re.findall(r'^\s*ssid="([^"]+)"', content, flags=re.MULTILINE)
    country_match = re.search(r"^country=([A-Za-z]{2})", content, flags=re.MULTILINE)
    country = country_match.group(1) if country_match else ""
    deduped = list(dict.fromkeys(ssids))
    return deduped, country


def _read_netplan_wifi_summary() -> tuple[list[str], str]:
    if not NETPLAN_WIFI_PATH.exists():
        return [], ""
    content = _read_text(NETPLAN_WIFI_PATH)
    ssids: list[str] = []
    country = ""
    access_indent: int | None = None
    in_access_points = False
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped.startswith("regulatory-domain:"):
            country = stripped.split("regulatory-domain:", 1)[1].strip().strip("'\"")
        if stripped == "access-points:":
            access_indent = len(line) - len(line.lstrip())
            in_access_points = True
            continue
        if in_access_points:
            indent = len(line) - len(line.lstrip())
            if access_indent is None or indent <= access_indent:
                in_access_points = False
                continue
            if access_indent is not None and indent == access_indent + 2 and ":" in stripped:
                key = stripped.split(":", 1)[0].strip().strip("'\"")
                if key:
                    ssids.append(key)
    deduped = list(dict.fromkeys(ssids))
    return deduped, country


def wifi_status(interface: str | None = None) -> CommandResult:
    backend = _detect_network_backend()
    config_ssids: list[str] = []
    country = ""
    if backend == "legacy":
        config_ssids, country = _read_wpa_supplicant_summary()
    elif backend == "netplan":
        config_ssids, country = _read_netplan_wifi_summary()
    if not config_ssids or not country:
        try:
            from mpwrd_config.core import DEFAULT_CONFIG_PATH, load_config

            cfg_path = Path(os.getenv("MPWRD_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
            cfg = load_config(cfg_path)
            if not config_ssids and cfg.networking.wifi:
                config_ssids = [net.ssid for net in cfg.networking.wifi if net.ssid]
            if not country and cfg.networking.country_code:
                country = cfg.networking.country_code
        except Exception:
            pass

    config_lines = [
        f"Configured SSIDs:{', '.join(config_ssids) if config_ssids else 'unknown'}",
        "Password:(hidden)",
        f"Country:{country or 'unknown'}",
    ]

    iface, error = _resolve_wifi_interface(interface)
    if error:
        return CommandResult(
            returncode=1,
            stdout=f"Wi-Fi status:{error}\nBackend:{backend}\n\n" + "\n".join(config_lines),
        )

    state = _current_wifi_state(iface, backend)
    _write_wifi_state(state)
    if state != "up":
        return CommandResult(
            returncode=0,
            stdout="Wi-Fi status:disabled\n"
            f"Interface:{iface}\nBackend:{backend}\n\n"
            + "\n".join(config_lines),
        )

    ssid, signal, details = _connected_wifi_info(iface, backend)
    radio_details = _wifi_radio_info(iface)

    ip_addr = ""
    ip_result = _run(["ip", "-4", "addr", "show", "dev", iface])
    match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", ip_result.stdout)
    if match:
        ip_addr = match.group(1)
    mac = _read_text(Path(f"/sys/class/net/{iface}/address")).strip() or "unknown"

    lines = [
        "Wi-Fi status:enabled",
        f"Interface:{iface}",
        f"Backend:{backend}",
        f"Connected to:{ssid}",
        f"Sig. strength:{signal}",
        f"BSSID:{details.get('BSSID', 'unknown')}" if details.get("BSSID") else None,
        f"Band:{details.get('Band', 'unknown')}" if details.get("Band") else None,
        f"Channel:{details.get('Channel', 'unknown')}" if details.get("Channel") else None,
        f"Bitrate:{details.get('Bitrate', 'unknown')}" if details.get("Bitrate") else None,
        f"PHY:{radio_details.get('PHY', 'unknown')}" if radio_details.get("PHY") else None,
        f"Mode:{radio_details.get('Mode', 'unknown')}" if radio_details.get("Mode") else None,
        f"Tx power:{radio_details.get('Tx power', 'unknown')}" if radio_details.get("Tx power") else None,
        f"Channel width:{radio_details.get('Channel width', 'unknown')}"
        if radio_details.get("Channel width")
        else None,
        f"Driver:{radio_details.get('Driver', 'unknown')}" if radio_details.get("Driver") else None,
        f"Firmware:{radio_details.get('Firmware', 'unknown')}" if radio_details.get("Firmware") else None,
        f"Bus info:{radio_details.get('Bus info', 'unknown')}" if radio_details.get("Bus info") else None,
        f"Current IP:{ip_addr or 'none'}",
        f"Hostname:{socket.gethostname()}.local",
        f"MAC address:{mac}",
        "",
        *config_lines,
    ]
    return CommandResult(returncode=0, stdout="\n".join(line for line in lines if line is not None))


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
    probe_targets = tuple(targets)
    success = 0
    total = 0
    errors: list[str] = []
    ping_available = shutil.which("ping") is not None
    if ping_available:
        for target in probe_targets:
            # Keep diagnostics responsive: one quick probe per target.
            result = _run(["ping", "-c", "1", "-W", "1", target])
            total += 1
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
        http_ok = _run(["curl", "-fsS", "--connect-timeout", "1", "--max-time", "2", http_url]).returncode == 0
    elif shutil.which("wget"):
        http_ok = _run(["wget", "-q", "--timeout=2", "--spider", http_url]).returncode == 0

    ping_ok = success > 0
    status_lines = [
        f"Ping: {success}/{total} responses" if ping_available else "Ping: unavailable",
        f"DNS: {'ok' if dns_ok else 'failed'}" if shutil.which("getent") else "DNS: unavailable",
        f"HTTP: {'ok' if http_ok else 'failed'}" if (shutil.which("curl") or shutil.which("wget")) else "HTTP: unavailable",
    ]

    if http_ok or (ping_ok and dns_ok):
        target_list = ", ".join(probe_targets)
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
