from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from mpwrd_config.core import DEFAULT_CONFIG_PATH, load_config
from mpwrd_config.kernel_modules import list_active_modules, list_blacklisted_modules, list_boot_modules
from mpwrd_config.meshtastic import (
    add_admin_key,
    clear_admin_keys,
    current_radio,
    i2c_state,
    meshtastic_info,
    meshtastic_update,
    set_config_url,
    set_legacy_admin_state,
    set_private_key,
    set_public_key,
    set_radio,
)
from mpwrd_config.software_manager import run_action as run_package_action
from mpwrd_config.system import CommandResult, _run, ethernet_status, ip_addresses, set_wifi_credentials, wifi_status
from mpwrd_config.time_config import set_timezone


FEMTO_CONF_PATH = Path("/etc/femto.conf")
ACT_LED_TRIGGER_PATH = Path("/sys/class/leds/work/trigger")
LUKFOX_CFG_PATH = Path("/etc/luckfox.cfg")
FOX_RELEASE_PATH = Path("/etc/foxbuntu-release")
LEGACY_BIN_DIRS = [Path("/usr/local/bin")]

_PINOUT_FEMTOFOX = """┌──────────┬────┬─────┬────┬───────────────┬───┬───────────────┐
│⚪:♥KILL ●│●   │USB-C│   ●│●       PWR-IN │✚ ▬│ 3.3-5V      ⚪│
├───────┐ ●│●   └─────┘   ●│●              └───┘               │
│ USB-C │ ●│●             ●│●  ┌─────────────────────────────┐ │
│ PWR ♥ │ ●│●   LUCKFOX   ●│●  │       ┌─────────────┐       │ │
│ DEBUG │ ●│●  PICO MINI  ●│●  │       │             │       │ │
├───────┘ ●│●             ●│●  │       │             │       │ │
├───┐     ●│●   FOXHOLE   ●│●  │   E   │             │       │ │
│ ● │GND  ●│●             ●│●  │   2   │ E22-900M22S │       │ │
│ ● │3V3  ●│●             ●│●  │   2   │             │       │ │
│ ● │TX4  ●│●             ●│●  │   |   │             │       │ │
│ ● │RX4  ●│●             ●│●  │   9   │             │       │ │
├───┘      └───●─●─●─●─●───┘   │   0   └─────────────┘       │ │
│⚪                            │   0    ┌───────────┐        │ │
├──────────────────┐           │   M    │           │        │ │
│ ● RX-            │ I2C GROVE │   3    │   SEEED   │        │ │
│ ● RX+            │ ┌───────┐ │   0    │WIO  SX1262│        │ │
│ ● GND  ETHERNET  │ │● ● ● ●│ │   S    │           │        │ │
│ ● TX-            │ ╞═══════╡ │        └───────────┘        │ │
│ ● TX+            │ │● ● ● ●│ │                             │ │
├──────────────────┘ └───────┘ └─────────────────────────────┘ │
│  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ■  │
│⚪●  ●  ●  ●  ●  ●  ♥  ♥  ♥  ●  ●  ♥  ♥  ●  ●  ●  ●  ●  ●  ●⚪│
└──────────────────────────────────────────────────────────────┘
               R              M  M                              
   G           X        G  C  I  O  3           G     S  S  3   
   N           E        N  L  S  S  V           N     C  D  V   
   D           N        D  K  O  I  3           D     L  A  3   
   ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ●  ■   
   ●  ●  ●  ●  ●  ●  ♥  ♥  ♥  ●  ●  ♥  ♥  ●  ●  ●  ●  ●  ●  ●   
   C  B  I  G  G  G  G  S  S     G  R  T  G  R  R  T  G  5  5   
   S  U  R  N  P  N  P  R  R     N  X  X  N  S  X  X  N  V  V   
   0  S  Q  D  I  D  I  A  A     D  2  2  D  T  4  4  D         
      Y        O     O  1  0                                    
♥ Denotes Pro features:                                         
On Community Edition, PWR/DEBUG USB-C replaced by 4pin UART2.   
PWR/DEBUG USB-C also carries serial with no added adapter.      
KILL: add PWR switch or thermal cutoff/fuse & remove resistor."""

_PINOUT_FEMTOFOX_ZERO = """         ┌────┐               ┌────┐         
         │⚪   \\             /   ⚪│        
         │      \\           /      │         
         │       └─────────┘       │         
         │   ┌─────────────────┐   │         
         │  ●│●     USB-C     ●│●  │         
         │  ●│●               ●│●  │         
         │  ●│●               ●│●  │         
         │  ●│●    LUCKFOX    ●│●  │         
         │  ●│●   PICO MINI   ●│●  │         
         │  ●│●               ●│●  │         
         │  ●│●    FOXHOLE    ●│●  │         
         │  ●│●               ●│●  │         
         │  ●│●               ●│●  │         
         │  ●│●               ●│●  │         
         │  ●│●               ●│●  │         
         │   └────●─●─●─●─●────┘   │         
         ├───┐ ┌─────────────┐ ┌───┤         
     GND │ ● │ │   HT-RA62   │ │ ● │ GND     
     3V3 │ ● │ │  ┌───────┐  │ │ ● │ 3V3     
UART4-RX │ ● │ │  │       │  │ │ ● │ I2C SDA 
UART4-TX │ ● │ │  │  WIO  │  │ │ ● │ I2C SCL 
         ├───┘ │  │SX 1262│  │ └───┤         
         │     │  └───────┘  │   ● │ UNUSED  
         │     └─────────────┘   ● │ GND     
         │⚪         ETH         ⚪│        
         └────────●─●─●─●─●────────┘         
                  R R G T T                  
                  X X N X X                  
                  - + D - +                  """

_PINOUT_FEMTOFOX_TINY = "coming soon"

_PINOUT_LUCKFOX = """                    ┌────┬───────┬────┐                      
       VBUS 3.3-5V ●│●   │ USB-C │   ●│● 1V8 OUT             
               GND ●│●   │       │   ●│● GND                 
        3V3 IN/OUT ●│●   └───────┘   ●│● 145, SARADC-IN1 1.8V
UART2-TX DEBUG, 42 ●│●               ●│● 144, SARADC-IN0 1.8V
UART2-RX DEBUG, 43 ●│●        [BTN]  ●│● 4                   
           CS0, 48 ●│●               ●│● 55, IRQ             
           CLK, 49 ●│●               ●│● 54, BUSY            
          MOSI, 50 ●│●               ●│● 59, I2C SCL         
          MISO, 51 ●│●               ●│● 58, I2C SDA         
      UART4-RX, 52 ●│●               ●│● 57, NRST, UART3-RX  
      UART4-TX, 53 ●│●      ETH      ●│● 56, RXEN, UART3-TX  
                    └──●──●──●──●──●──┘                      
                       R  R  G  T  T                         
                       X  X  N  X  X                         
                       -  +  D  -  +                         
GPIO BANK 0 (3.3v): 4                                        
GPIO BANK 1 (3.3v): 42 43 48 49 50 51 52 53 54 55 56 57 58 59
GPIO BANK 4 (1.8v): 144 145                                  """

_LICENSE_FEMTOFOX_SHORT = (
    "Femtofox is comprised of two projects, with two different licenses:\n"
    "1. Femtofox - the hardware, which is licensed \"CC BY-NC-ND - noncommercial\". Summary: you may copy and share and modify the hardware files, but cannot sell them without license from Femtofox, and must give attribution to Femtofox.\n"
    "2. Foxbuntu - refers to the modifications to Ubuntu made as part of the Femtofox project, which is licensed GNU GPLv3. Summary: you may use, modify and distribute (including for commercial purposes) Foxbuntu, but must give attribution to Femtofox and distribute this license with your project. Any modified version must remain open source.\n"
    "\n"
    "For more information, visit us at www.femtofox.com.\n"
    "\n"
    "View the long licenses for more information.\n"
    "Contact us to license Femtofox."
)

_LICENSE_MESHTASTIC = (
    "The Meshtastic firmware is licensed GPL3.\n"
    "\n"
    "Meshtastic is a registered trademark of Meshtastic LLC. Meshtastic software components are released under various licenses, see GitHub for details. No warranty is provided - use at your own risk.\n"
    "\n"
    "Some of the verbiage in the help-texts in the menus is sourced from the Meshtastic website, also licensed GPL3.\n"
    "\n"
    "For more information about Meshtastic, visit https://www.meshtastic.org"
)


def _legacy_tool_path(name: str) -> Path | None:
    for base in LEGACY_BIN_DIRS:
        candidate = base / name
        if candidate.exists():
            return candidate
    return None


def legacy_tool_command(names: list[str]) -> list[str] | None:
    for name in names:
        path = _legacy_tool_path(name)
        if path:
            return [str(path)]
        found = shutil.which(name)
        if found:
            return [found]
    return None


def run_legacy_tool(name: str, args: list[str] | None = None) -> CommandResult:
    path = _legacy_tool_path(name)
    if not path:
        return CommandResult(returncode=1, stdout=f"{name} not found.")
    command = [str(path)]
    if args:
        command.extend(args)
    return _run(command)


@dataclass
class InfoResult:
    returncode: int
    stdout: str


def _read_kv_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


def _set_kv_value(path: Path, key: str, value: str) -> None:
    lines = []
    updated = False
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                updated = True
            else:
                lines.append(line)
    if not updated:
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def act_led(state: str | None) -> CommandResult:
    if state not in ("enable", "disable", "check", None):
        return CommandResult(returncode=1, stdout="Invalid ACT LED state.")
    if state == "check":
        config = _read_kv_file(FEMTO_CONF_PATH)
        value = config.get("act_led")
        if value == "enable":
            return CommandResult(returncode=0, stdout="enabled")
        if value == "disable":
            return CommandResult(returncode=0, stdout="disabled")
        return CommandResult(returncode=1, stdout="unknown")
    if state is None:
        return CommandResult(returncode=1, stdout="Missing ACT LED state.")
    trigger_value = "activity" if state == "enable" else "none"
    if ACT_LED_TRIGGER_PATH.exists():
        ACT_LED_TRIGGER_PATH.write_text(trigger_value, encoding="utf-8")
    _set_kv_value(FEMTO_CONF_PATH, "act_led", state)
    return CommandResult(returncode=0, stdout=f"Activity LED {state}d.")


def logging_state(action: str) -> CommandResult:
    if action == "check":
        result = _run(["lsattr", "-d", "/var/log"])
        if "i" in result.stdout:
            return CommandResult(returncode=0, stdout="disabled")
        return CommandResult(returncode=0, stdout="enabled")
    if action == "disable":
        _run(["logger", "Disabling system logging by making /var/log immutable."])
        result = _run(["chattr", "+i", "/var/log"])
        message = result.stdout.strip() or "System logging disabled."
        return CommandResult(returncode=result.returncode, stdout=message)
    if action == "enable":
        _run(["logger", "Enabling system logging by making /var/log writable."])
        result = _run(["chattr", "-i", "/var/log"])
        message = result.stdout.strip() or "System logging enabled."
        return CommandResult(returncode=result.returncode, stdout=message)
    return CommandResult(returncode=1, stdout="Invalid logging action.")


def generate_ssh_keys() -> CommandResult:
    for path in Path("/etc/ssh").glob("ssh_host_*"):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
    _run(["ssh-keygen", "-t", "ed25519", "-f", "/etc/ssh/ssh_host_ed25519_key", "-N", ""])
    _run(["ssh-keygen", "-t", "rsa", "-b", "4096", "-f", "/etc/ssh/ssh_host_rsa_key", "-N", ""])
    for key_path in Path("/etc/ssh").glob("ssh_host_*_key"):
        _run(["chmod", "600", str(key_path)])
    for pub_path in Path("/etc/ssh").glob("ssh_host_*_key.pub"):
        _run(["chmod", "644", str(pub_path)])
    for host_path in Path("/etc/ssh").glob("ssh_host_*"):
        _run(["chown", "root:root", str(host_path)])
    _run(["systemctl", "restart", "ssh"])
    return CommandResult(returncode=0, stdout="SSH keys regenerated.")


def service_status(service: str) -> CommandResult:
    enabled = _run(["systemctl", "is-enabled", service])
    enabled_state = "enabled" if enabled.returncode == 0 else "disabled"
    active = _run(["systemctl", "is-active", service])
    if active.returncode == 0:
        running_state = "running"
        returncode = 0
    else:
        running_state = "not running"
        returncode = 1
    return CommandResult(returncode=returncode, stdout=f"{enabled_state}, {running_state}")


def service_action(service: str, action: str) -> CommandResult:
    if action == "status":
        return service_status(service)
    if action in {"start", "stop", "restart", "enable", "disable"}:
        return _run(["systemctl", action, service])
    return CommandResult(returncode=1, stdout="Invalid service action.")


def ttyd_action(action: str) -> CommandResult:
    if action == "check":
        return service_status("ttyd")
    if action in {"enable", "disable", "start", "stop", "restart"}:
        return _run(["systemctl", action, "ttyd"])
    return CommandResult(returncode=1, stdout="Invalid ttyd action.")


def foxbuntu_version() -> CommandResult:
    if not FOX_RELEASE_PATH.exists():
        return CommandResult(returncode=1, stdout="unknown")
    data = _read_kv_file(FOX_RELEASE_PATH)
    major = data.get("major", "0")
    minor = data.get("minor", "0")
    patch = data.get("patch", "0")
    hotfix = data.get("hotfix", "")
    return CommandResult(returncode=0, stdout=f"Foxbuntu v{major}.{minor}.{patch}{hotfix}")


def run_first_boot() -> CommandResult:
    messages: list[str] = []
    returncode = 0

    def log(msg: str) -> None:
        messages.append(msg)
        _run(["logger", f"First boot: {msg}"])

    def maybe_run(cmd: list[str], success_msg: str | None = None) -> None:
        nonlocal returncode
        result = _run(cmd)
        returncode = max(returncode, result.returncode)
        if result.returncode == 0 and success_msg:
            log(success_msg)
        elif result.stdout.strip():
            log(result.stdout.strip())

    log("Starting first boot steps.")
    if _run(["systemctl", "is-enabled", "femto-runonce"]).returncode != 0:
        log("femto-runonce not enabled; running first boot tasks anyway.")

    for device in ("/dev/mmcblk1p5", "/dev/mmcblk1p6", "/dev/mmcblk1p7"):
        if Path(device).exists():
            log(f"Resizing filesystem on {device}...")
            maybe_run(["resize2fs", device])
    if not any(Path(dev).exists() for dev in ("/dev/mmcblk1p5", "/dev/mmcblk1p6", "/dev/mmcblk1p7")):
        log("No mmcblk1p5-7 partitions found; skipping resize.")

    swap_path = Path("/swapfile")
    if not swap_path.exists():
        log("Allocating swapfile...")
        maybe_run(["fallocate", "-l", "1G", str(swap_path)])
        maybe_run(["chmod", "600", str(swap_path)])
        maybe_run(["mkswap", str(swap_path)])
        maybe_run(["swapon", str(swap_path)])
        fstab = Path("/etc/fstab")
        if fstab.exists():
            fstab_text = fstab.read_text(encoding="utf-8")
            if "/swapfile" not in fstab_text:
                fstab.write_text(fstab_text.rstrip() + "\n/swapfile none swap sw 0 0\n", encoding="utf-8")
        log("Swapfile allocated.")
    else:
        log("Swapfile already exists; skipping allocation.")

    mac = None
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("serial"):
                _, value = line.split(":", 1)
                serial = value.strip()
                if len(serial) >= 10:
                    raw = f"a2{serial[-10:]}"
                    mac = ":".join(raw[i : i + 2] for i in range(0, 12, 2))
                break
    except FileNotFoundError:
        pass
    interfaces_path = Path("/etc/network/interfaces")
    if mac and interfaces_path.exists():
        content = interfaces_path.read_text(encoding="utf-8")
        if f"hwaddress ether {mac}" not in content:
            lines = []
            insert_after = False
            for line in content.splitlines():
                lines.append(line)
                if line.strip() == "allow-hotplug eth0":
                    insert_after = True
                    continue
                if insert_after:
                    if line.strip().startswith("iface "):
                        lines.insert(len(lines) - 1, f"    hwaddress ether {mac}")
                        insert_after = False
            if insert_after:
                lines.append(f"    hwaddress ether {mac}")
            interfaces_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            log(f"Set eth0 MAC address to {mac}.")
        else:
            log("eth0 MAC address already configured; skipping.")
    else:
        log("Unable to determine CPU-derived MAC or interfaces file missing.")

    bashrc_path = Path("/home/femto/.bashrc")
    term_lines = "export NCURSES_NO_UTF8_ACS=1\nexport TERM=xterm-256color\nexport LANG=C.UTF-8"
    if bashrc_path.exists():
        bashrc_text = bashrc_path.read_text(encoding="utf-8")
        if term_lines not in bashrc_text:
            bashrc_path.write_text(bashrc_text.rstrip() + "\n" + term_lines + "\n", encoding="utf-8")
            log("Added terminal settings to /home/femto/.bashrc.")
        else:
            log("Terminal settings already present in /home/femto/.bashrc.")
        if "alias sfc='sudo mpwrd-config'" not in bashrc_text:
            bashrc_path.write_text(
                bashrc_path.read_text(encoding="utf-8").rstrip() + "\n" + "alias sfc='sudo mpwrd-config'\n",
                encoding="utf-8",
            )
            log("Added alias sfc='sudo mpwrd-config' to /home/femto/.bashrc.")
        else:
            log("Alias sfc already present in /home/femto/.bashrc.")
    else:
        log("/home/femto/.bashrc not found; skipping bashrc updates.")

    compiler_keep = Path("/usr/lib/arm-linux-gnueabihf/libc_nonshared.a.keep")
    compiler_target = Path("/usr/lib/arm-linux-gnueabihf/libc_nonshared.a")
    if compiler_keep.exists():
        compiler_target.write_bytes(compiler_keep.read_bytes())
        log("Compiler support updated.")

    log("Enabling meshtasticd service.")
    maybe_run(["systemctl", "enable", "meshtasticd"])

    log("Generating new SSH keys.")
    ssh_result = generate_ssh_keys()
    if ssh_result.stdout.strip():
        log(ssh_result.stdout.strip())
    returncode = max(returncode, ssh_result.returncode)

    log("Generating new ttyd SSL keys.")
    ttyd_result = run_package_action("ttyd", "-k", interactive=False)
    if ttyd_result.output:
        log(ttyd_result.output.strip())
    returncode = max(returncode, ttyd_result.returncode)
    maybe_run(["systemctl", "enable", "ttyd"])

    _run(["systemctl", "disable", "femto-runonce"])
    log("First boot steps complete.")
    return CommandResult(returncode=returncode, stdout="\n".join(messages).strip())


def run_usb_config_tool() -> CommandResult:
    mount_point = Path("/mnt/usb")
    log_path = Path("/tmp/femtofox-config.log")
    messages: list[str] = []
    partial_failure = False
    found_config = False

    def log(msg: str) -> None:
        messages.append(msg)
        _run(["logger", f"USB config: {msg}"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{msg}\n")

    if _run(["systemctl", "is-enabled", "femto-runonce"]).returncode == 0:
        log("First boot detected; skipping USB configuration tool.")
        return CommandResult(returncode=0, stdout="\n".join(messages).strip())

    lsblk = _run(["lsblk", "-o", "NAME,FSTYPE,TYPE,MOUNTPOINT", "-nr"])
    device_name = None
    device_fstype = None
    device_mount = None
    for line in lsblk.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        name, fstype = parts[0], parts[1]
        mount = parts[3] if len(parts) > 3 else ""
        if fstype.lower() in {"vfat", "ext4", "ntfs", "exfat"} and re.match(r"sd[a-z][0-9]*", name):
            device_name = name
            device_fstype = fstype.lower()
            device_mount = mount
            break

    if not device_name:
        log("No USB drive found.")
        return CommandResult(returncode=0, stdout="\n".join(messages).strip())

    full_device = f"/dev/{device_name}"
    mount_point.mkdir(parents=True, exist_ok=True)
    if not device_mount:
        mount_result = _run(["mount", full_device, str(mount_point)])
        if mount_result.returncode != 0:
            log("Failed to mount USB drive.")
            return CommandResult(returncode=1, stdout="\n".join(messages).strip())
        device_mount = str(mount_point)
        log(f"USB drive mounted at {mount_point}.")
    else:
        log("USB drive already mounted.")
        mount_point = Path(device_mount)

    config_path = mount_point / "femtofox-config.txt"
    log_exists = (mount_point / "femtofox-config.log").exists()
    if not config_path.exists():
        log("USB drive mounted but femtofox-config.txt not found.")
        return CommandResult(returncode=1, stdout="\n".join(messages).strip())

    config_text = config_path.read_text(encoding="utf-8").replace("\r", "")
    entries: dict[str, list[str]] = {}
    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"')
        entries.setdefault(key, []).append(value)

    dont_run = entries.get("dont_run_if_log_exists", ["false"])[0].lower() == "true"
    if log_exists and dont_run:
        log("dont_run_if_log_exists is true and log exists; aborting.")
        return CommandResult(returncode=1, stdout="\n".join(messages).strip())

    if "act_led" in entries:
        result = act_led(entries["act_led"][0])
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Updated ACT LED.")
        found_config = True

    wifi_ssid = entries.get("wifi_ssid", [None])[0]
    wifi_psk = entries.get("wifi_psk", [None])[0]
    wifi_country = entries.get("wifi_country", [None])[0]
    if wifi_ssid or wifi_psk or wifi_country:
        existing_ssid = ""
        existing_psk = ""
        try:
            content = Path("/etc/wpa_supplicant/wpa_supplicant.conf").read_text(encoding="utf-8")
            ssid_match = re.search(r'^\s*ssid="([^"]+)"', content, flags=re.MULTILINE)
            psk_match = re.search(r'^\s*psk="([^"]+)"', content, flags=re.MULTILINE)
            if ssid_match:
                existing_ssid = ssid_match.group(1)
            if psk_match:
                existing_psk = psk_match.group(1)
        except FileNotFoundError:
            pass
        ssid = wifi_ssid or existing_ssid
        psk = wifi_psk or existing_psk
        if ssid and psk:
            result = set_wifi_credentials(ssid, psk, wifi_country, apply=True)
            if result.returncode != 0:
                partial_failure = True
            log(result.stdout.strip() or "Wi-Fi credentials updated.")
            found_config = True
        else:
            log("Wi-Fi credentials incomplete; skipping.")

    if "meshtastic_lora_radio" in entries:
        raw = entries["meshtastic_lora_radio"][0].lower()
        mapping = {
            "ebyte-e22-900m30s": "sx1262_tcxo",
            "ebyte-e22-900m22s": "sx1262_tcxo",
            "heltec-ht-ra62": "sx1262_tcxo",
            "seeed-wio-sx1262": "sx1262_tcxo",
            "waveshare-sx126x-xxxm": "sx1262_xtal",
            "ai-thinker-ra-01sh": "sx1262_xtal",
            "ebyte-e80-900m22s": "lr1121_tcxo",
            "sx1262_tcxo": "sx1262_tcxo",
            "sx1262_xtal": "sx1262_xtal",
            "lr1121_tcxo": "lr1121_tcxo",
            "none": "none",
        }
        model = mapping.get(raw)
        if model:
            result = set_radio(model)
            if result.returncode != 0:
                partial_failure = True
            log(result.stdout.strip() or f"Set LoRa radio to {model}.")
            found_config = True
        else:
            partial_failure = True
            log(f"Invalid LoRa radio name: {raw}")

    if "timezone" in entries:
        tz = entries["timezone"][0].replace("\\", "")
        result = set_timezone(tz)
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or f"Timezone set to {tz}.")
        found_config = True

    if "meshtastic_url" in entries:
        url = entries["meshtastic_url"][0].replace("\\", "")
        result = set_config_url(url)
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Meshtastic URL updated.")
        found_config = True

    if "meshtastic_public_key" in entries:
        key = entries["meshtastic_public_key"][0].replace("\\", "")
        result = set_public_key(key)
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Meshtastic public key updated.")
        found_config = True

    if "meshtastic_private_key" in entries:
        key = entries["meshtastic_private_key"][0].replace("\\", "")
        result = set_private_key(key)
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Meshtastic private key updated.")
        found_config = True

    if "meshtastic_admin_key" in entries:
        key = entries["meshtastic_admin_key"][0].replace("\\", "")
        if key == "clear":
            result = clear_admin_keys()
        else:
            result = add_admin_key(key)
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Meshtastic admin key updated.")
        found_config = True

    if "meshtastic_legacy_admin" in entries:
        value = entries["meshtastic_legacy_admin"][0].replace("\\", "").lower()
        enabled = value in {"true", "1", "yes", "enable", "enabled"}
        result = set_legacy_admin_state(enabled)
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or f"Legacy admin set to {value}.")
        found_config = True

    for command in entries.get("meshtastic_cli", []):
        log("Meshtastic CLI command found.")
        result = meshtastic_update(command, attempts=3, label="Meshtastic CLI")
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Meshtastic CLI command complete.")
        found_config = True

    if "software_install" in entries:
        packages = [p.strip() for p in entries["software_install"][0].split(",") if p.strip()]
        for pkg in packages:
            log(f"Installing {pkg}...")
            result = run_package_action(pkg, "-i", interactive=False)
            if result.returncode != 0:
                partial_failure = True
            log(result.output.strip() or f"{pkg} install complete.")
        if packages:
            found_config = True

    if "meshtastic_i2c" in entries:
        result = i2c_state(entries["meshtastic_i2c"][0])
        if result.returncode != 0:
            partial_failure = True
        log(result.stdout.strip() or "Meshtastic I2C updated.")
        found_config = True

    if device_fstype != "ntfs" and log_path.exists():
        try:
            (mount_point / "femtofox-config.log").write_text(log_path.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception:
            log("Unable to write femtofox-config.log to USB drive.")

    if not found_config:
        log("femtofox-config.txt does not contain valid configuration info; ignoring.")
        partial_failure = True
    status = 1 if partial_failure else 0
    return CommandResult(returncode=status, stdout="\n".join(messages).strip())


def pinout_info(kind: str) -> CommandResult:
    pinouts = {
        "femtofox": _PINOUT_FEMTOFOX,
        "zero": _PINOUT_FEMTOFOX_ZERO,
        "tiny": _PINOUT_FEMTOFOX_TINY,
        "luckfox": _PINOUT_LUCKFOX,
    }
    text = pinouts.get(kind)
    if not text:
        return CommandResult(returncode=1, stdout="Unknown pinout selection.")
    return CommandResult(returncode=0, stdout=text)


def license_info(kind: str) -> CommandResult:
    if kind == "about":
        return CommandResult(
            returncode=0,
            stdout=(
                "mpwrd-config\n"
                "\n"
                "Written by Ruledo\n"
                "\n"
                "based on femto-config\n"
                "by noon92 aka nagu\n"
                "\"We really did something good didn't we?\" - nagu"
            ),
        )
    if kind == "short":
        return CommandResult(returncode=0, stdout=_LICENSE_FEMTOFOX_SHORT)
    if kind == "long":
        path = Path("/usr/share/doc/femtofox/long_license")
        if path.exists():
            return CommandResult(returncode=0, stdout=path.read_text(encoding="utf-8"))
        return CommandResult(returncode=1, stdout="Long license not found.")
    if kind == "meshtastic":
        return CommandResult(returncode=0, stdout=_LICENSE_MESHTASTIC)
    return CommandResult(returncode=1, stdout="Unknown license selection.")


def process_snapshot() -> CommandResult:
    if shutil.which("top"):
        return _run(["top", "-b", "-n", "1"])
    return _run(["ps", "aux"])


def _human_uptime() -> str:
    try:
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return "unknown"
    minutes, _ = divmod(int(uptime_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def cpu_info() -> InfoResult:
    compat = ""
    if Path("/proc/device-tree/compatible").exists():
        compat = Path("/proc/device-tree/compatible").read_text(errors="ignore").replace("\x00", " ").strip()
    core = "Luckfox Pico" if "luckfox" in compat.lower() else "Unknown"
    cpu_model = compat.split()[0] if compat else platform.processor() or "unknown"
    arch = f"{platform.machine()} ({platform.architecture()[0]})"
    temp = "unknown"
    if Path("/sys/class/thermal/thermal_zone0/temp").exists():
        try:
            raw = int(Path("/sys/class/thermal/thermal_zone0/temp").read_text())
            temp = f"{raw / 1000:.1f}°C"
        except Exception:
            temp = "unknown"
    speed = "unknown"
    min_freq = Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_min_freq")
    max_freq = Path("/sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")
    if min_freq.exists() and max_freq.exists():
        try:
            min_mhz = int(min_freq.read_text()) // 1000
            max_mhz = int(max_freq.read_text()) // 1000
            speed = f"{min_mhz}-{max_mhz}mhz"
        except Exception:
            speed = "unknown"
    serial = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("serial"):
                serial = line.split(":", 1)[1].strip()
                break
    except Exception:
        pass
    output = (
        f"Core:{core}\n"
        f"Model:{cpu_model}\n"
        f"Architecture:{arch}\n"
        f"Speed:{speed} x {os.cpu_count() or 1} cores\n"
        f"Temperature:{temp}\n"
        f"Serial #:{serial}"
    )
    return InfoResult(returncode=0, stdout=output)


def storage_info() -> InfoResult:
    total, used, free = shutil.disk_usage("/")
    total_gb = total / 1024 / 1024 / 1024
    free_pct = (free / total) * 100 if total else 0
    microsd = f"{total_gb:.2f} GB ({free_pct:.2f}% free)"
    meminfo = _read_kv_file(Path("/proc/meminfo"))
    mem_total = int(meminfo.get("MemTotal", "0 kB").split()[0])
    mem_avail = int(meminfo.get("MemAvailable", "0 kB").split()[0])
    mem_pct_free = (mem_avail / mem_total) * 100 if mem_total else 0
    memory = f"{mem_total // 1024} MB ({mem_pct_free:.2f}% free)"
    swap_total = int(meminfo.get("SwapTotal", "0 kB").split()[0])
    swap_free = int(meminfo.get("SwapFree", "0 kB").split()[0])
    if swap_total >= 1024 * 1024:
        swap_display = f"{swap_total / 1024 / 1024:.2f} GB"
    else:
        swap_display = f"{swap_total // 1024} MB"
    swap_pct_free = (swap_free / swap_total) * 100 if swap_total else 0
    swap = f"{swap_display} ({swap_pct_free:.2f}% free)"
    mounted = " ".join(str(path) for path in Path("/mnt").glob("*/") if path.is_dir())
    mounted = mounted.strip() if mounted else "none"
    output = (
        f"microSD size:{microsd}\n"
        f"Memory:{memory}\n"
        f"Swap:{swap}\n"
        f"Mnted drives:{mounted}"
    )
    return InfoResult(returncode=0, stdout=output)


def os_info() -> InfoResult:
    pretty = "unknown"
    codename = "unknown"
    if Path("/etc/os-release").exists():
        data = _read_kv_file(Path("/etc/os-release"))
        pretty = data.get("PRETTY_NAME", "unknown")
        codename = data.get("VERSION_CODENAME", "unknown")
    uptime = _human_uptime()
    kernel_version = platform.release()
    active_modules = list_active_modules().stdout.replace("\n", " ").strip()
    boot_modules = list_boot_modules().stdout.replace("\n", ", ").strip()
    blacklisted = list_blacklisted_modules().stdout.strip()
    ttyd_state = service_status("ttyd").stdout
    logging_state_text = logging_state("check").stdout
    act_led_state = act_led("check").stdout
    output = (
        f"OS:{pretty} ({codename})\n"
        f"Kernel ver:{kernel_version}\n"
        f"Uptime:{uptime}\n"
        f"System time:{subprocess.check_output(['date'], text=True).strip()}\n"
        f"K mods active:{active_modules or 'none'}\n"
        f"K boot mods:{boot_modules or 'none'}\n"
        f"K mod blcklst:{blacklisted}\n"
        f"Web terminal:{ttyd_state}\n"
        f"Logging:{logging_state_text}\n"
        f"Activity LED:{act_led_state}"
    )
    return InfoResult(returncode=0, stdout=output)


def networking_info() -> InfoResult:
    config_path = Path(os.getenv("MPWRD_CONFIG_PATH") or DEFAULT_CONFIG_PATH)
    config = load_config(config_path)
    wifi = wifi_status(config.networking.wifi_interface).stdout.strip()
    eth = ethernet_status(config.networking.ethernet_interface).stdout.strip()
    ips = ip_addresses().stdout.strip()
    output = f"Wi-Fi:\n{wifi}\n\nEthernet:\n{eth}\n\nIP addresses:\n{ips}"
    return InfoResult(returncode=0, stdout=output)


def _parse_luckfox_cfg() -> dict[str, str]:
    return _read_kv_file(LUKFOX_CFG_PATH)


def _read_spi_speed() -> str:
    path = Path("/sys/firmware/devicetree/base/spi@ff500000/spidev@0/spi-max-frequency")
    if not path.exists():
        return "unknown"
    try:
        raw = path.read_bytes()
        value = int.from_bytes(raw, byteorder="big")
        return str(value)
    except Exception:
        return "unknown"


def _i2c_addresses(bus: int = 3) -> str:
    if shutil.which("i2cdetect") is None:
        return "unavailable"
    timeout_cmd = shutil.which("timeout")
    try:
        if timeout_cmd:
            output = subprocess.check_output([timeout_cmd, "3", "i2cdetect", "-y", str(bus)], text=True, stderr=subprocess.DEVNULL)
        else:
            output = subprocess.check_output(["i2cdetect", "-y", str(bus)], text=True, stderr=subprocess.DEVNULL)
    except Exception:
        return "unavailable"
    addresses: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not re.match(r"^[0-9a-f]{2}:", line):
            continue
        parts = line.split()
        row = parts[0].replace(":", "")
        for idx, cell in enumerate(parts[1:], start=0):
            if cell == "--":
                continue
            try:
                address_value = int(row, 16) + idx
                address = f"0x{address_value:02x}"
            except Exception:
                continue
            addresses.append(address)
    return " ".join(addresses) if addresses else "none detected"


def peripherals_info() -> InfoResult:
    usb_mode_path = Path("/sys/devices/platform/ff3e0000.usb2-phy/otg_mode")
    usb_mode = usb_mode_path.read_text(encoding="utf-8").strip() if usb_mode_path.exists() else "unknown"
    cfg = _parse_luckfox_cfg()
    spi0_state = "unknown"
    if "SPI0_M0_STATUS" in cfg:
        spi0_state = "enabled" if cfg.get("SPI0_M0_STATUS") == "1" else "disabled"
    spi0_speed = _read_spi_speed()
    i2c3_state = "unknown"
    if "I2C3_M1_STATUS" in cfg:
        i2c3_state = "enabled" if cfg.get("I2C3_M1_STATUS") == "1" else "disabled"
    i2c3_speed = cfg.get("I2C3_M1_SPEED", "unknown") if cfg else "unknown"
    uart3_state = "unknown"
    if "UART3_M1_STATUS" in cfg:
        uart3_state = "enabled" if cfg.get("UART3_M1_STATUS") == "1" else "disabled"
    uart4_state = "unknown"
    if "UART4_M1_STATUS" in cfg:
        uart4_state = "enabled" if cfg.get("UART4_M1_STATUS") == "1" else "disabled"
    try:
        radio = current_radio().stdout
    except Exception:
        radio = "unknown"
    try:
        lsusb_output = subprocess.check_output(["lsusb"], text=True)
        devices = []
        for line in lsusb_output.splitlines():
            if "root hub" in line:
                continue
            parts = line.split()
            if len(parts) >= 7:
                devices.append("USB:" + " ".join(parts[6:]))
        usb_devices = "\n".join(devices) if devices else "USB:none detected"
    except Exception:
        usb_devices = "USB:unavailable"
    i2c_devices = _i2c_addresses(3)
    output = (
        f"LoRa radio:{radio}\n"
        f"{usb_devices}\n"
        f"i2c devices:{i2c_devices}\n"
        f"USB mode:{usb_mode}\n"
        f"SPI-0 state:{spi0_state}\n"
        f"SPI-0 speed:{spi0_speed}\n"
        f"i2c-3 state:{i2c3_state}\n"
        f"i2c-3 speed:{i2c3_speed}\n"
        f"UART-3 state:{uart3_state}\n"
        f"UART-4 state:{uart4_state}"
    )
    return InfoResult(returncode=0, stdout=output)


def all_system_info() -> InfoResult:
    sections = [
        "            Femtofox",
        "    CPU:",
        cpu_info().stdout,
        "",
        "    Operating System:",
        os_info().stdout,
        "",
        "    Storage:",
        storage_info().stdout,
        "",
        "    Networking (Wi-Fi & Ethernet):",
        networking_info().stdout,
        "",
        "    Peripherals:",
        peripherals_info().stdout,
        "",
        "    Meshtasticd:",
        meshtastic_info().stdout,
    ]
    return InfoResult(returncode=0, stdout="\n".join(sections))
