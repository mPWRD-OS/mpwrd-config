from __future__ import annotations

import argparse
import os
from pathlib import Path

from mpwrd_config.core import DEFAULT_CONFIG_PATH, Config, WifiNetwork, config_to_toml, load_config, save_config
from mpwrd_config.system import (
    ethernet_status,
    ip_addresses,
    list_ethernet_interfaces,
    list_wifi_interfaces,
    set_hostname,
    set_wifi_credentials,
    system_reboot,
    system_shutdown,
    test_internet,
    wifi_restart,
    wifi_state,
    wifi_status,
    wifi_toggle,
)
from mpwrd_config.meshtastic import (
    add_admin_key,
    clear_admin_keys,
    config_qr,
    current_radio,
    get_config_url,
    get_legacy_admin_state,
    get_private_key,
    get_public_key,
    i2c_state,
    lora_settings,
    list_admin_keys,
    mesh_test,
    meshtastic_info,
    meshtastic_config,
    meshtastic_summary,
    meshtastic_update,
    mac_address_source,
    set_mac_address_source,
    service_action as meshtastic_service_action,
    service_enable as meshtastic_service_enable,
    service_status as meshtastic_service_status,
    set_lora_settings,
    set_config_url,
    set_legacy_admin_state,
    set_private_key,
    set_public_key,
    set_radio,
    uninstall,
    upgrade,
)
from mpwrd_config.kernel_modules import (
    blacklist_module,
    disable_module,
    enable_module,
    list_active_modules,
    list_blacklisted_modules,
    list_boot_modules,
    unblacklist_module,
)
from mpwrd_config.software_manager import (
    license_text,
    list_packages,
    package_info,
    run_action,
    manage_full_control_conflicts,
    service_action as package_service_action,
)
from mpwrd_config.system_utils import (
    act_led,
    all_system_info,
    cpu_info,
    foxbuntu_version,
    generate_ssh_keys,
    logging_state,
    networking_info,
    os_info,
    peripherals_info,
    service_action as system_service_action,
    service_status as system_service_status,
    storage_info,
    ttyd_action,
)
from mpwrd_config.tui_dialog import main as tui_main
from mpwrd_config.time_config import current_timezone, set_time, set_timezone, status as time_status
from mpwrd_config.watchclock import run_watchclock
from mpwrd_config.wifi_mesh import run as wifi_mesh_run, sync_once as wifi_mesh_sync


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="mpwrd-config")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the canonical config file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize a default config.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite if exists.")

    subparsers.add_parser("show", help="Show the canonical config.")

    networking_parser = subparsers.add_parser("networking", help="Networking actions.")
    networking_sub = networking_parser.add_subparsers(dest="networking_command", required=True)

    hostname_parser = networking_sub.add_parser("hostname", help="Manage hostname.")
    hostname_sub = hostname_parser.add_subparsers(dest="hostname_command", required=True)
    hostname_set = hostname_sub.add_parser("set", help="Set hostname.")
    hostname_set.add_argument("--name", required=True)

    wifi_parser = networking_sub.add_parser("wifi", help="Manage Wi-Fi settings.")
    wifi_sub = wifi_parser.add_subparsers(dest="wifi_command", required=True)
    wifi_set = wifi_sub.add_parser("set", help="Set Wi-Fi credentials.")
    wifi_set.add_argument("--ssid", required=True)
    wifi_set.add_argument("--psk", required=True)
    wifi_set.add_argument("--country", default=None)

    wifi_sub.add_parser("enable", help="Enable Wi-Fi in config.")
    wifi_sub.add_parser("disable", help="Disable Wi-Fi in config.")
    wifi_sub.add_parser("status", help="Show Wi-Fi status (system).")
    wifi_sub.add_parser("up", help="Bring Wi-Fi up (system).")
    wifi_sub.add_parser("down", help="Bring Wi-Fi down (system).")
    wifi_sub.add_parser("toggle", help="Toggle Wi-Fi state (system).")
    wifi_sub.add_parser("restart", help="Restart Wi-Fi (system).")
    wifi_sub.add_parser("interfaces", help="List Wi-Fi interfaces.")
    wifi_iface_set = wifi_sub.add_parser("set-interface", help="Select Wi-Fi interface.")
    wifi_iface_set.add_argument("--name", required=True)
    wifi_sub.add_parser("clear-interface", help="Clear Wi-Fi interface selection.")

    networking_sub.add_parser("eth-status", help="Show ethernet status (system).")
    ethernet_parser = networking_sub.add_parser("ethernet", help="Manage ethernet settings.")
    ethernet_sub = ethernet_parser.add_subparsers(dest="ethernet_command", required=True)
    ethernet_sub.add_parser("status", help="Show ethernet status (system).")
    ethernet_iface_set = ethernet_sub.add_parser("set-interface", help="Select ethernet interface.")
    ethernet_iface_set.add_argument("--name", required=True)
    ethernet_sub.add_parser("clear-interface", help="Clear ethernet interface selection.")

    networking_sub.add_parser("ip", help="Show IP addresses (system).")
    networking_sub.add_parser("test", help="Test internet connection (system).")
    networking_sub.add_parser("apply", help="Apply canonical networking config to system.")
    networking_sub.add_parser("interfaces", help="List network interfaces.")

    system_parser = subparsers.add_parser("system", help="System actions.")
    system_sub = system_parser.add_subparsers(dest="system_command", required=True)
    system_sub.add_parser("reboot", help="Reboot the system.")
    system_sub.add_parser("shutdown", help="Shutdown the system.")

    services_parser = subparsers.add_parser("services", help="Manage systemd services.")
    services_parser.add_argument("service", help="Service name, e.g. avahi-daemon")
    services_parser.add_argument(
        "action",
        choices=["status", "start", "stop", "restart", "enable", "disable"],
        help="Service action",
    )

    meshtastic_parser = subparsers.add_parser("meshtastic", help="Meshtastic actions.")
    meshtastic_sub = meshtastic_parser.add_subparsers(dest="meshtastic_command", required=True)
    meshtastic_sub.add_parser("info", help="Show Meshtastic info.")
    meshtastic_sub.add_parser("summary", help="Show important node info.")
    config_dump = meshtastic_sub.add_parser("config", help="Show Meshtastic configuration by category.")
    config_dump.add_argument("--categories", default="all", help="Comma-delimited list: all,nodeinfo,settings,channels")
    config_dump.add_argument("--quiet", action="store_true", help="Suppress output (return code only).")
    meshtastic_sub.add_parser("config-qr", help="Show configuration URL + QR code.")
    meshtastic_sub.add_parser("config-url", help="Show config URL.")
    config_set = meshtastic_sub.add_parser("set-config-url", help="Set config URL.")
    config_set.add_argument("--url", required=True)

    meshtastic_sub.add_parser("public-key", help="Show public key.")
    public_set = meshtastic_sub.add_parser("set-public-key", help="Set public key.")
    public_set.add_argument("--key", required=True)
    meshtastic_sub.add_parser("private-key", help="Show private key.")
    private_set = meshtastic_sub.add_parser("set-private-key", help="Set private key.")
    private_set.add_argument("--key", required=True)

    meshtastic_sub.add_parser("admin-keys", help="List admin keys.")
    admin_add = meshtastic_sub.add_parser("add-admin-key", help="Add admin key.")
    admin_add.add_argument("--key", required=True)
    meshtastic_sub.add_parser("clear-admin-keys", help="Clear admin keys.")

    meshtastic_sub.add_parser("legacy-admin", help="Show legacy admin channel state.")
    legacy_set = meshtastic_sub.add_parser("set-legacy-admin", help="Set legacy admin channel state.")
    legacy_set.add_argument("--enabled", choices=["true", "false"], required=True)

    meshtastic_sub.add_parser("radio", help="Show radio selection.")
    radio_set = meshtastic_sub.add_parser("set-radio", help="Set radio selection.")
    radio_set.add_argument("--model", required=True)
    meshtastic_sub.add_parser("mac-source", help="Show MAC address source.")
    mac_set = meshtastic_sub.add_parser("set-mac-source", help="Set MAC address source.")
    mac_set.add_argument("--source", required=True)

    service_parser = meshtastic_sub.add_parser("service", help="Manage meshtasticd service.")
    service_sub = service_parser.add_subparsers(dest="service_command", required=True)
    service_sub.add_parser("status", help="Show service status.")
    service_sub.add_parser("start", help="Start service.")
    service_sub.add_parser("stop", help="Stop service.")
    service_sub.add_parser("restart", help="Restart service.")
    service_sub.add_parser("enable", help="Enable service.")
    service_sub.add_parser("disable", help="Disable service.")

    i2c_parser = meshtastic_sub.add_parser("i2c", help="Manage Meshtastic I2C state.")
    i2c_parser.add_argument("state", choices=["enable", "disable", "check"])

    meshtastic_sub.add_parser("mesh-test", help="Test mesh connectivity.")
    meshtastic_sub.add_parser("upgrade", help="Upgrade meshtasticd.")
    meshtastic_sub.add_parser("uninstall", help="Uninstall meshtasticd.")

    update_parser = meshtastic_sub.add_parser("update", help="Run a Meshtastic command with retries.")
    update_parser.add_argument(
        "--command",
        dest="update_command",
        required=True,
        help="Meshtastic CLI arguments, e.g. \"--set lora.region US\"",
    )
    update_parser.add_argument("--attempts", type=int, default=1)
    update_parser.add_argument("--label", default=None)

    lora_parser = meshtastic_sub.add_parser("lora", help="LoRa settings.")
    lora_sub = lora_parser.add_subparsers(dest="lora_command", required=True)
    lora_sub.add_parser("show", help="Show current LoRa settings.")
    lora_set = lora_sub.add_parser("set", help="Set LoRa settings.")
    lora_set.add_argument("--region")
    lora_set.add_argument("--use-preset", choices=["true", "false"])
    lora_set.add_argument("--preset")
    lora_set.add_argument("--bandwidth")
    lora_set.add_argument("--spread-factor")
    lora_set.add_argument("--coding-rate")
    lora_set.add_argument("--frequency-offset")
    lora_set.add_argument("--hop-limit")
    lora_set.add_argument("--tx-enabled", choices=["true", "false"])
    lora_set.add_argument("--tx-power")
    lora_set.add_argument("--channel-num")
    lora_set.add_argument("--override-duty-cycle", choices=["true", "false"])
    lora_set.add_argument("--sx126x-rx-boosted-gain", choices=["true", "false"])
    lora_set.add_argument("--override-frequency")
    lora_set.add_argument("--ignore-mqtt", choices=["true", "false"])
    lora_set.add_argument("--ok-to-mqtt", choices=["true", "false"])

    kernel_parser = subparsers.add_parser("kernel", help="Kernel module management.")
    kernel_sub = kernel_parser.add_subparsers(dest="kernel_command", required=True)
    kernel_sub.add_parser("boot", help="List modules set to load at boot.")
    kernel_sub.add_parser("active", help="List active modules.")
    kernel_sub.add_parser("blacklist", help="List blacklisted modules.")
    kernel_enable = kernel_sub.add_parser("enable", help="Enable a module.")
    kernel_enable.add_argument("--name", required=True)
    kernel_disable = kernel_sub.add_parser("disable", help="Disable a module.")
    kernel_disable.add_argument("--name", required=True)
    kernel_blacklist = kernel_sub.add_parser("blacklist-set", help="Blacklist a module.")
    kernel_blacklist.add_argument("--name", required=True)
    kernel_unblacklist = kernel_sub.add_parser("blacklist-clear", help="Un-blacklist a module.")
    kernel_unblacklist.add_argument("--name", required=True)

    subparsers.add_parser("tui", help="Launch dialog TUI.")

    time_parser = subparsers.add_parser("time", help="Time and timezone management.")
    time_sub = time_parser.add_subparsers(dest="time_command", required=True)
    time_sub.add_parser("status", help="Show timedatectl status.")
    time_sub.add_parser("timezone", help="Show current timezone.")
    tz_set = time_sub.add_parser("set-timezone", help="Set timezone.")
    tz_set.add_argument("--name", required=True)
    time_set = time_sub.add_parser("set-time", help="Set system time.")
    time_set.add_argument("--value", required=True)

    software_parser = subparsers.add_parser("software", help="Software package manager.")
    software_parser.add_argument("--package-dir", type=Path, default=None)
    software_sub = software_parser.add_subparsers(dest="software_command", required=True)
    software_sub.add_parser("list", help="List available packages.")
    software_info = software_sub.add_parser("info", help="Show package info.")
    software_info.add_argument("--name", required=True)
    software_install = software_sub.add_parser("install", help="Install a package.")
    software_install.add_argument("--name", required=True)
    software_uninstall = software_sub.add_parser("uninstall", help="Uninstall a package.")
    software_uninstall.add_argument("--name", required=True)
    software_upgrade = software_sub.add_parser("upgrade", help="Upgrade a package.")
    software_upgrade.add_argument("--name", required=True)
    software_init = software_sub.add_parser("init", help="Run interactive initialization.")
    software_init.add_argument("--name", required=True)
    software_run = software_sub.add_parser("run", help="Run a package command.")
    software_run.add_argument("--name", required=True)
    software_license = software_sub.add_parser("license", help="Show package license.")
    software_license.add_argument("--name", required=True)
    software_service = software_sub.add_parser("service", help="Manage package service.")
    software_service.add_argument("--name", required=True)
    software_service.add_argument(
        "--action",
        required=True,
        choices=["status", "enable", "disable", "start", "stop", "restart", "detailed"],
    )
    software_extra = software_sub.add_parser("extra", help="Run a package extra action.")
    software_extra.add_argument("--name", required=True)
    software_extra.add_argument("--action", required=True)
    software_conflicts = software_sub.add_parser("conflicts", help="Stop/start conflicting services.")
    software_conflicts.add_argument("--action", choices=["stop", "start"], required=True)

    utils_parser = subparsers.add_parser("utils", help="System utilities.")
    utils_sub = utils_parser.add_subparsers(dest="utils_command", required=True)
    utils_act = utils_sub.add_parser("act-led", help="Manage ACT LED.")
    utils_act.add_argument("--state", choices=["enable", "disable", "check"], required=True)
    utils_log = utils_sub.add_parser("logging", help="Manage system logging.")
    utils_log.add_argument("--state", choices=["enable", "disable", "check"], required=True)
    utils_ttyd = utils_sub.add_parser("ttyd", help="Manage ttyd service.")
    utils_ttyd.add_argument("--action", choices=["enable", "disable", "start", "stop", "restart", "check"], required=True)
    utils_sub.add_parser("ssh-keys", help="Regenerate SSH host keys.")
    utils_service = utils_sub.add_parser("service-status", help="Check service status.")
    utils_service.add_argument("--name", required=True)
    utils_info = utils_sub.add_parser("info", help="Show system info.")
    utils_info.add_argument("--section", choices=["all", "cpu", "os", "storage", "network", "peripherals"], default="all")
    utils_sub.add_parser("version", help="Show Foxbuntu version.")

    watchclock_parser = subparsers.add_parser("watchclock", help="Watchclock service.")
    watchclock_sub = watchclock_parser.add_subparsers(dest="watchclock_command", required=True)
    watchclock_run = watchclock_sub.add_parser("run", help="Run watchclock loop.")
    watchclock_run.add_argument("--threshold-seconds", type=int, default=7 * 24 * 60 * 60)
    watchclock_run.add_argument("--interval-seconds", type=int, default=30)

    wifi_mesh_parser = subparsers.add_parser("wifi-mesh", help="Wi-Fi mesh sync.")
    wifi_mesh_sub = wifi_mesh_parser.add_subparsers(dest="wifi_mesh_command", required=True)
    wifi_mesh_sub.add_parser("run", help="Run Wi-Fi mesh sync loop.")
    wifi_mesh_sub.add_parser("sync", help="Run a one-time sync.")

    subparsers.add_parser("wizard", help="Launch install wizard.")

    return parser.parse_args()


def _cmd_init(path: Path, force: bool) -> int:
    if path.exists() and not force:
        print(f"Config already exists at {path}. Use --force to overwrite.")
        return 1
    config = Config()
    save_config(config, path)
    print(f"Initialized config at {path}.")
    return 0


def _cmd_show(path: Path) -> int:
    config = load_config(path)
    print(config_to_toml(config).rstrip())
    return 0


def _cmd_hostname_set(path: Path, name: str) -> int:
    config = load_config(path)
    config.networking.hostname = name
    save_config(config, path)
    print(f"Hostname set to {name}.")
    return 0


def _cmd_wifi_set(path: Path, ssid: str, psk: str, country: str | None) -> int:
    config = load_config(path)
    updated = False
    for network in config.networking.wifi:
        if network.ssid == ssid:
            network.psk = psk
            updated = True
            break
    if not updated:
        config.networking.wifi.append(WifiNetwork(ssid=ssid, psk=psk))
    if country:
        config.networking.country_code = country
    config.networking.wifi_enabled = True
    save_config(config, path)
    print(f"Wi-Fi settings saved for SSID {ssid}.")
    return 0


def _cmd_wifi_toggle(path: Path, enable: bool) -> int:
    config = load_config(path)
    config.networking.wifi_enabled = enable
    save_config(config, path)
    result = wifi_state("up" if enable else "down", interface=config.networking.wifi_interface)
    state = "enabled" if enable else "disabled"
    lines = [f"Wi-Fi {state} in config."]
    if result.stdout.strip():
        lines.append(result.stdout.strip())
    print("\n".join(lines))
    return result.returncode


def _cmd_wifi_set_interface(path: Path, name: str | None) -> int:
    config = load_config(path)
    if not name:
        config.networking.wifi_interface = None
        save_config(config, path)
        print("Wi-Fi interface selection cleared.")
        return 0
    interfaces = list_wifi_interfaces()
    if name not in interfaces:
        print(f"Wi-Fi interface '{name}' not found. Available: {', '.join(interfaces) or 'none'}.")
        return 1
    config.networking.wifi_interface = name
    save_config(config, path)
    print(f"Wi-Fi interface set to {name}.")
    return 0


def _cmd_ethernet_set_interface(path: Path, name: str | None) -> int:
    config = load_config(path)
    if not name:
        config.networking.ethernet_interface = None
        save_config(config, path)
        print("Ethernet interface selection cleared.")
        return 0
    interfaces = list_ethernet_interfaces()
    if name not in interfaces:
        print(f"Ethernet interface '{name}' not found. Available: {', '.join(interfaces) or 'none'}.")
        return 1
    config.networking.ethernet_interface = name
    save_config(config, path)
    print(f"Ethernet interface set to {name}.")
    return 0


def _cmd_network_interfaces(path: Path) -> int:
    config = load_config(path)
    wifi_interfaces = list_wifi_interfaces()
    eth_interfaces = list_ethernet_interfaces()
    wifi_selected = config.networking.wifi_interface or "auto"
    eth_selected = config.networking.ethernet_interface or "auto"
    print(f"Wi-Fi interfaces: {', '.join(wifi_interfaces) or 'none'}")
    print(f"Wi-Fi selected: {wifi_selected}")
    print(f"Ethernet interfaces: {', '.join(eth_interfaces) or 'none'}")
    print(f"Ethernet selected: {eth_selected}")
    return 0


def _cmd_network_apply(path: Path) -> int:
    config = load_config(path)
    results = []
    results.append(set_hostname(config.networking.hostname))
    if config.networking.wifi:
        network = config.networking.wifi[0]
        results.append(
            set_wifi_credentials(
                network.ssid,
                network.psk,
                config.networking.country_code,
                apply=config.networking.wifi_enabled,
                interface=config.networking.wifi_interface,
            )
        )
    if config.networking.wifi_enabled and not config.networking.wifi:
        results.append(wifi_state("up", interface=config.networking.wifi_interface))
    if not config.networking.wifi_enabled:
        results.append(wifi_state("down", interface=config.networking.wifi_interface))
    return max(result.returncode for result in results)


def _print_result(result) -> int:
    print(result.stdout.rstrip())
    return result.returncode


def _cmd_software_list(package_dir: Path | None) -> int:
    packages = list_packages(package_dir)
    if not packages:
        print("No packages found.")
        return 1
    for package in packages:
        status = "installed" if package.installed else "not installed"
        print(f"{package.name}: {status}")
    return 0


def _cmd_software_info(package_dir: Path | None, name: str) -> int:
    info = package_info(name, package_dir)
    lines = [
        f"Name: {info.name}",
        f"Installed: {info.installed}",
        f"Options: {info.options}",
    ]
    if info.author:
        lines.append(f"Author: {info.author}")
    if info.description:
        lines.append(f"Description: {info.description}")
    if info.url:
        lines.append(f"URL: {info.url}")
    if info.service_name:
        lines.append(f"Service: {info.service_name}")
    if info.location:
        lines.append(f"Location: {info.location}")
    if info.license_name:
        lines.append(f"License: {info.license_name}")
    if info.conflicts:
        lines.append(f"Conflicts: {info.conflicts}")
    print("\n".join(lines))
    return 0


def _cmd_software_action(package_dir: Path | None, name: str, action: str) -> int:
    result = run_action(name, action, package_dir, interactive=True)
    if result.output:
        print(result.output.rstrip())
    if result.user_message:
        print(f"\n{result.user_message}")
    return result.returncode


def _cmd_software_service(package_dir: Path | None, name: str, action: str) -> int:
    flag_map = {
        "status": "-S",
        "enable": "-e",
        "disable": "-d",
        "start": "-r",
        "restart": "-r",
        "stop": "-s",
        "detailed": "-S",
    }
    result = package_service_action(name, flag_map[action], package_dir)
    print(result.stdout.rstrip())
    return result.returncode


def main() -> int:
    args = _parse_args()
    if os.getenv("MPWRD_ALLOW_NON_ROOT") != "1" and os.geteuid() != 0:
        print("mpwrd-config must be run as root. Try: sudo mpwrd-config")
        return 1
    path = args.config

    if args.command == "init":
        return _cmd_init(path, args.force)
    if args.command == "show":
        return _cmd_show(path)
    if args.command == "networking":
        if args.networking_command == "hostname" and args.hostname_command == "set":
            return _cmd_hostname_set(path, args.name)
        if args.networking_command == "wifi":
            config = load_config(path)
            if args.wifi_command == "set":
                return _cmd_wifi_set(path, args.ssid, args.psk, args.country)
            if args.wifi_command == "enable":
                return _cmd_wifi_toggle(path, True)
            if args.wifi_command == "disable":
                return _cmd_wifi_toggle(path, False)
            if args.wifi_command == "status":
                return _print_result(wifi_status(config.networking.wifi_interface))
            if args.wifi_command == "up":
                return _print_result(wifi_state("up", interface=config.networking.wifi_interface))
            if args.wifi_command == "down":
                return _print_result(wifi_state("down", interface=config.networking.wifi_interface))
            if args.wifi_command == "toggle":
                return _print_result(wifi_toggle(config.networking.wifi_interface))
            if args.wifi_command == "restart":
                return _print_result(wifi_restart(config.networking.wifi_interface))
            if args.wifi_command == "interfaces":
                return _cmd_network_interfaces(path)
            if args.wifi_command == "set-interface":
                return _cmd_wifi_set_interface(path, args.name)
            if args.wifi_command == "clear-interface":
                return _cmd_wifi_set_interface(path, None)
        if args.networking_command == "eth-status":
            config = load_config(path)
            return _print_result(ethernet_status(config.networking.ethernet_interface))
        if args.networking_command == "ethernet":
            config = load_config(path)
            if args.ethernet_command == "status":
                return _print_result(ethernet_status(config.networking.ethernet_interface))
            if args.ethernet_command == "set-interface":
                return _cmd_ethernet_set_interface(path, args.name)
            if args.ethernet_command == "clear-interface":
                return _cmd_ethernet_set_interface(path, None)
        if args.networking_command == "ip":
            return _print_result(ip_addresses())
        if args.networking_command == "test":
            return _print_result(test_internet())
        if args.networking_command == "apply":
            return _cmd_network_apply(path)
        if args.networking_command == "interfaces":
            return _cmd_network_interfaces(path)
    if args.command == "system":
        if args.system_command == "reboot":
            return _print_result(system_reboot())
        if args.system_command == "shutdown":
            return _print_result(system_shutdown())
    if args.command == "services":
        return _print_result(system_service_action(args.service, args.action))
    if args.command == "meshtastic":
        if args.meshtastic_command == "info":
            return _print_result(meshtastic_info())
        if args.meshtastic_command == "summary":
            return _print_result(meshtastic_summary())
        if args.meshtastic_command == "config":
            result = meshtastic_config(args.categories, quiet=args.quiet)
            if not args.quiet:
                print(result.stdout.rstrip())
            return result.returncode
        if args.meshtastic_command == "config-qr":
            return _print_result(config_qr())
        if args.meshtastic_command == "config-url":
            return _print_result(get_config_url())
        if args.meshtastic_command == "set-config-url":
            return _print_result(set_config_url(args.url))
        if args.meshtastic_command == "public-key":
            return _print_result(get_public_key())
        if args.meshtastic_command == "set-public-key":
            return _print_result(set_public_key(args.key))
        if args.meshtastic_command == "private-key":
            return _print_result(get_private_key())
        if args.meshtastic_command == "set-private-key":
            return _print_result(set_private_key(args.key))
        if args.meshtastic_command == "admin-keys":
            return _print_result(list_admin_keys())
        if args.meshtastic_command == "add-admin-key":
            return _print_result(add_admin_key(args.key))
        if args.meshtastic_command == "clear-admin-keys":
            return _print_result(clear_admin_keys())
        if args.meshtastic_command == "legacy-admin":
            return _print_result(get_legacy_admin_state())
        if args.meshtastic_command == "set-legacy-admin":
            return _print_result(set_legacy_admin_state(args.enabled == "true"))
        if args.meshtastic_command == "radio":
            return _print_result(current_radio())
        if args.meshtastic_command == "set-radio":
            return _print_result(set_radio(args.model))
        if args.meshtastic_command == "mac-source":
            return _print_result(mac_address_source())
        if args.meshtastic_command == "set-mac-source":
            return _print_result(set_mac_address_source(args.source))
        if args.meshtastic_command == "service":
            if args.service_command == "status":
                return _print_result(meshtastic_service_status())
            if args.service_command == "start":
                return _print_result(meshtastic_service_action("start"))
            if args.service_command == "stop":
                return _print_result(meshtastic_service_action("stop"))
            if args.service_command == "restart":
                return _print_result(meshtastic_service_action("restart"))
            if args.service_command == "enable":
                return _print_result(meshtastic_service_enable(True))
            if args.service_command == "disable":
                return _print_result(meshtastic_service_enable(False))
        if args.meshtastic_command == "i2c":
            return _print_result(i2c_state(args.state))
        if args.meshtastic_command == "mesh-test":
            return _print_result(mesh_test())
        if args.meshtastic_command == "upgrade":
            return _print_result(upgrade())
        if args.meshtastic_command == "uninstall":
            return _print_result(uninstall())
        if args.meshtastic_command == "update":
            return _print_result(meshtastic_update(args.update_command, attempts=args.attempts, label=args.label))
        if args.meshtastic_command == "lora":
            if args.lora_command == "show":
                result, settings = lora_settings()
                if result.returncode != 0:
                    return _print_result(result)
                for key, value in settings.items():
                    print(f"{key}:{value}")
                return 0
            if args.lora_command == "set":
                settings: dict[str, str] = {}
                if args.region:
                    settings["region"] = args.region
                if args.use_preset:
                    settings["use_preset"] = args.use_preset.lower()
                if args.preset:
                    settings["modem_preset"] = args.preset
                if args.bandwidth:
                    settings["bandwidth"] = args.bandwidth
                if args.spread_factor:
                    settings["spread_factor"] = args.spread_factor
                if args.coding_rate:
                    settings["coding_rate"] = args.coding_rate
                if args.frequency_offset:
                    settings["frequency_offset"] = args.frequency_offset
                if args.hop_limit:
                    settings["hop_limit"] = args.hop_limit
                if args.tx_enabled:
                    settings["tx_enabled"] = args.tx_enabled.lower()
                if args.tx_power:
                    settings["tx_power"] = args.tx_power
                if args.channel_num:
                    settings["channel_num"] = args.channel_num
                if args.override_duty_cycle:
                    settings["override_duty_cycle"] = args.override_duty_cycle.lower()
                if args.sx126x_rx_boosted_gain:
                    settings["sx126x_rx_boosted_gain"] = args.sx126x_rx_boosted_gain.lower()
                if args.override_frequency:
                    settings["override_frequency"] = args.override_frequency
                if args.ignore_mqtt:
                    settings["ignore_mqtt"] = args.ignore_mqtt.lower()
                if args.ok_to_mqtt:
                    settings["config_ok_to_mqtt"] = args.ok_to_mqtt.lower()
                return _print_result(set_lora_settings(settings))
    if args.command == "kernel":
        if args.kernel_command == "boot":
            return _print_result(list_boot_modules())
        if args.kernel_command == "active":
            return _print_result(list_active_modules())
        if args.kernel_command == "blacklist":
            return _print_result(list_blacklisted_modules())
        if args.kernel_command == "enable":
            return _print_result(enable_module(args.name))
        if args.kernel_command == "disable":
            return _print_result(disable_module(args.name))
        if args.kernel_command == "blacklist-set":
            return _print_result(blacklist_module(args.name))
        if args.kernel_command == "blacklist-clear":
            return _print_result(unblacklist_module(args.name))
    if args.command == "tui":
        return tui_main()
    if args.command == "time":
        if args.time_command == "status":
            return _print_result(time_status())
        if args.time_command == "timezone":
            return _print_result(current_timezone())
        if args.time_command == "set-timezone":
            return _print_result(set_timezone(args.name))
        if args.time_command == "set-time":
            return _print_result(set_time(args.value))
    if args.command == "software":
        package_dir = args.package_dir
        if args.software_command == "list":
            return _cmd_software_list(package_dir)
        if args.software_command == "info":
            return _cmd_software_info(package_dir, args.name)
        if args.software_command == "install":
            return _cmd_software_action(package_dir, args.name, "-i")
        if args.software_command == "uninstall":
            return _cmd_software_action(package_dir, args.name, "-u")
        if args.software_command == "upgrade":
            return _cmd_software_action(package_dir, args.name, "-g")
        if args.software_command == "init":
            return _cmd_software_action(package_dir, args.name, "-a")
        if args.software_command == "run":
            return _cmd_software_action(package_dir, args.name, "-l")
        if args.software_command == "extra":
            return _cmd_software_action(package_dir, args.name, f"-{args.action}")
        if args.software_command == "license":
            print(license_text(args.name, package_dir))
            return 0
        if args.software_command == "service":
            return _cmd_software_service(package_dir, args.name, args.action)
        if args.software_command == "conflicts":
            return _print_result(manage_full_control_conflicts(args.action))
    if args.command == "utils":
        if args.utils_command == "act-led":
            return _print_result(act_led(args.state))
        if args.utils_command == "logging":
            return _print_result(logging_state(args.state))
        if args.utils_command == "ttyd":
            return _print_result(ttyd_action(args.action))
        if args.utils_command == "ssh-keys":
            return _print_result(generate_ssh_keys())
        if args.utils_command == "service-status":
            return _print_result(system_service_status(args.name))
        if args.utils_command == "version":
            return _print_result(foxbuntu_version())
        if args.utils_command == "info":
            if args.section == "all":
                return _print_result(all_system_info())
            if args.section == "cpu":
                return _print_result(cpu_info())
            if args.section == "os":
                return _print_result(os_info())
            if args.section == "storage":
                return _print_result(storage_info())
            if args.section == "network":
                return _print_result(networking_info())
            if args.section == "peripherals":
                return _print_result(peripherals_info())
    if args.command == "watchclock":
        if args.watchclock_command == "run":
            result = run_watchclock(
                threshold_seconds=args.threshold_seconds,
                interval_seconds=args.interval_seconds,
            )
            print(result.stdout.rstrip())
            return result.returncode
    if args.command == "wifi-mesh":
        if args.wifi_mesh_command == "run":
            result = wifi_mesh_run()
            print(result.stdout.rstrip())
            return result.returncode
        if args.wifi_mesh_command == "sync":
            result = wifi_mesh_sync()
            print(result.stdout.rstrip())
            return result.returncode
    if args.command == "wizard":
        return tui_main(wizard=True)

    raise SystemExit("Unknown command")


if __name__ == "__main__":
    raise SystemExit(main())
