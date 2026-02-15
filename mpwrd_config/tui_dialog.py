from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from InquirerPy import get_style, inquirer
from prompt_toolkit.application import Application
from prompt_toolkit.filters import has_focus
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, Dialog, RadioList, TextArea


def _clear_screen() -> None:
    try:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    except Exception:
        return


APP_STYLE = get_style(
    {
        "questionmark": "#9fa6ad",
        "answermark": "#9fa6ad",
        "question": "bold #e5e7eb",
        "answer": "#86efac",
        "input": "#86efac",
        "pointer": "bold #22c55e",
        "marker": "#22c55e",
        "instruction": "#9ca3af",
        "long_instruction": "#9ca3af",
        "separator": "#6b7280",
        "frame": "bold ansigreen",
        "frame.label": "bold ansigreen",
        "fuzzy_border": "#22c55e",
    },
    style_override=False,
)

FUZZY_STYLE = get_style(
    {
        "question": "bold #e5e7eb",
        "answer": "#86efac",
        "input": "#e5e7eb",
        "pointer": "bold #22c55e",
        "marker": "#22c55e",
        "fuzzy_prompt": "#e5e7eb",
        "fuzzy_info": "#9ca3af",
        "fuzzy_border": "#22c55e",
        "fuzzy_match": "#22c55e",
        "": "bg:#000000 #e5e7eb",
    },
    style_override=False,
)

DIALOG_STYLE = Style.from_dict(
    {
        **APP_STYLE.dict,
        "": "bg:#000000 #22c55e",
        "dialog": "bg:#000000 #22c55e",
        "dialog.body": "bg:#000000 #22c55e",
        "dialog frame.border": "#22c55e",
        "dialog frame.label": "bold #22c55e",
        "text-area": "bg:#000000 #22c55e",
        "button": "bg:#0b0f0d #22c55e",
        "button.focused": "bg:#22c55e #000000",
    }
)

MENU_STYLE = Style.from_dict(
    {
        "": "bg:#000000 #e5e7eb",
        "dialog": "bg:#000000 #e5e7eb",
        "dialog.body": "bg:#000000 #e5e7eb",
        "dialog frame.border": "#22c55e",
        "dialog frame.label": "bold #e5e7eb",
        "radio-list": "bg:#000000 #e5e7eb",
        "radio": "#e5e7eb",
        "radio-selected": "bold #22c55e",
        "radio-checked": "bold #22c55e",
        "radio-number": "#9ca3af",
        "scrollbar.background": "#111827",
        "scrollbar.button": "#22c55e",
        "scrollbar.arrow": "#22c55e",
        "button": "bg:#111827 #e5e7eb",
        "button.focused": "bg:#22c55e #000000",
    }
)

from mpwrd_config.core import DEFAULT_CONFIG_PATH, load_config
from mpwrd_config.software_manager import (
    license_text,
    list_packages,
    manage_full_control_conflicts,
    package_info,
    run_action,
    service_action as package_service_action,
)
from mpwrd_config.meshtastic import (
    channel_add,
    channel_add_url,
    channel_delete,
    channel_disable,
    channel_enable,
    channel_set,
    channel_set_url,
    current_radio,
    get_preference,
    list_preference_fields,
    lora_settings,
    mac_address_source,
    mac_address_source_options,
    meshtastic_config,
    meshtastic_repo_status,
    set_config_url,
    set_mac_address_source,
    set_meshtastic_repo,
    set_lora_settings,
    set_preference,
)
from mpwrd_config.system_utils import (
    act_led,
    all_system_info,
    cpu_info,
    generate_ssh_keys,
    legacy_tool_command,
    license_info,
    logging_state,
    networking_info,
    os_info,
    peripherals_info,
    pinout_info,
    process_snapshot,
    run_first_boot,
    run_usb_config_tool,
    service_action as system_service_action,
    service_status as system_service_status,
    storage_info,
    ttyd_action,
)
from mpwrd_config.kernel_modules import (
    blacklist_module,
    disable_module,
    enable_module,
    list_active_modules,
    list_blacklisted_modules,
    list_boot_modules,
    list_module_overview,
    module_info,
    unblacklist_module,
)
from mpwrd_config.system import list_wifi_interfaces, list_ethernet_interfaces
from mpwrd_config.time_config import current_timezone, set_time, set_timezone, status as time_status
from mpwrd_config.watchclock import run_watchclock
from mpwrd_config.wifi_mesh import sync_once as wifi_mesh_sync


def _message(title: str, body: str) -> None:
    _clear_screen()
    text = body or ""
    text_area = TextArea(
        text=text,
        read_only=True,
        scrollbar=True,
        wrap_lines=True,
        focusable=True,
    )
    app: Application | None = None

    def _close(event=None) -> None:
        if app:
            app.exit()

    ok_button = Button(text="OK", handler=_close)
    buttons = [ok_button]
    dialog = Dialog(title=title, body=text_area, buttons=buttons, with_background=True)
    kb = KeyBindings()
    kb.add("tab")(focus_next)
    kb.add("s-tab")(focus_previous)
    kb.add("escape")(_close)
    kb.add("q")(_close)
    kb.add("enter")(_close)
    kb.add("left")(_close)
    app = Application(
        layout=Layout(dialog, focused_element=ok_button),
        key_bindings=merge_key_bindings([load_key_bindings(), kb]),
        mouse_support=False,
        style=DIALOG_STYLE,
        full_screen=True,
    )
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        return
    finally:
        _clear_screen()


def _run_interactive(command: list[str], title: str, missing: str) -> None:
    _clear_screen()
    try:
        subprocess.run(command, check=False)
    except FileNotFoundError:
        _message(title, missing)
    finally:
        _clear_screen()


def _menu(title: str, items: list[tuple[str, str]], default: str | None = None) -> str | None:
    choices = [{"name": label or key, "value": key} for key, label in items]
    try:
        if len(choices) > 30:
            return inquirer.fuzzy(
                message=title,
                choices=choices,
                default=default,
                border=True,
                pointer=">",
                style=FUZZY_STYLE,
                qmark="",
                amark="",
                mandatory=False,
                raise_keyboard_interrupt=False,
                keybindings={
                    "answer": [{"key": "enter"}, {"key": "right"}, {"key": " "}],
                    "skip": [{"key": "escape"}, {"key": "left"}],
                },
            ).execute()
        values = [(item["value"], item["name"]) for item in choices]
        if not values:
            return None
        radio = RadioList(
            values=values,
            default=default,
            select_on_focus=True,
            open_character=" ",
            select_character=">",
            close_character=" ",
            container_style="class:radio-list",
            default_style="class:radio",
            selected_style="class:radio-selected",
            checked_style="class:radio-checked",
            number_style="class:radio-number",
            show_numbers=False,
            show_cursor=False,
            show_scrollbar=True,
        )
        result: dict[str, str | None] = {"value": None}

        def _accept(event=None) -> None:
            result["value"] = radio.current_value
            app.exit()

        def _cancel(event=None) -> None:
            result["value"] = None
            app.exit()

        radio.control.key_bindings.add("enter")(_accept)
        radio.control.key_bindings.add(" ")(_accept)
        radio.control.key_bindings.add("right")(_accept)
        radio.control.key_bindings.add("left")(_cancel)

        dialog = Dialog(title=title, body=radio, buttons=[], with_background=True)
        kb = KeyBindings()
        kb.add("tab")(focus_next)
        kb.add("s-tab")(focus_previous)
        kb.add("escape")(_cancel)
        kb.add("q")(_cancel)
        kb.add("left")(_cancel)
        kb.add("right")(_accept)
        kb.add("enter")(_accept)
        kb.add(" ")(_accept)
        app = Application(
            layout=Layout(dialog, focused_element=radio),
            key_bindings=merge_key_bindings([load_key_bindings(), kb]),
            mouse_support=False,
            style=MENU_STYLE,
            full_screen=True,
        )
        app.run()
        _clear_screen()
        return result["value"]
    except (KeyboardInterrupt, EOFError):
        return None


def _yesno(title: str, body: str) -> bool:
    try:
        return bool(inquirer.confirm(message=f"{title}\n{body}", default=False, style=APP_STYLE).execute())
    except (KeyboardInterrupt, EOFError):
        return False


def _inputbox(title: str, body: str, default: str = "") -> str | None:
    try:
        value = inquirer.text(message=f"{title}\n{body}", default=default, style=APP_STYLE).execute()
    except (KeyboardInterrupt, EOFError):
        return None
    if value is None:
        return None
    return str(value).strip()


def _safe_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _safe_time(value: str) -> bool:
    try:
        datetime.strptime(value, "%H:%M:%S")
        return True
    except ValueError:
        return False


def _config_path() -> Path:
    return Path(os.getenv("MPWRD_CONFIG_PATH") or DEFAULT_CONFIG_PATH)


def _has_wifi_interface() -> bool:
    return len(list_wifi_interfaces()) > 0


def _calendar(title: str, body: str, day: int, month: int, year: int) -> str | None:
    default = f"{year:04d}-{month:02d}-{day:02d}"
    try:
        value = inquirer.text(
            message=f"{title}\n{body}\n(YYYY-MM-DD)",
            default=default,
            validate=lambda text: bool(re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", text))
            and _safe_date(text),
            style=APP_STYLE,
        ).execute()
    except (KeyboardInterrupt, EOFError):
        return None
    if not value:
        return None
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return f"{parsed.day:02d}/{parsed.month:02d}/{parsed.year:04d}"


def _timebox(title: str, body: str, hour: int, minute: int, second: int) -> str | None:
    default = f"{hour:02d}:{minute:02d}:{second:02d}"
    try:
        value = inquirer.text(
            message=f"{title}\n{body}\n(HH:MM:SS)",
            default=default,
            validate=lambda text: bool(re.fullmatch(r"\\d{2}:\\d{2}:\\d{2}", text)) and _safe_time(text),
            style=APP_STYLE,
        ).execute()
    except (KeyboardInterrupt, EOFError):
        return None
    if not value:
        return None
    return str(value).strip()


def _cli_command(args: list[str]) -> list[str]:
    return [sys.executable, "-m", "mpwrd_config.cli", *args]


def _run_cli(args: list[str]) -> int:
    return subprocess.call(_cli_command(args))


def _run_cli_output(args: list[str], title: str) -> int:
    result = subprocess.run(
        _cli_command(args),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = result.stdout.strip()
    _message(title, output or "Done.")
    return result.returncode


def _read_wpa_defaults() -> tuple[str, str]:
    path = Path("/etc/wpa_supplicant/wpa_supplicant.conf")
    if not path.exists():
        return "", ""
    content = path.read_text(encoding="utf-8")
    ssid_match = re.search(r'\bssid="([^"]+)"', content)
    country_match = re.search(r'^country=([A-Za-z]{2})', content, flags=re.MULTILINE)
    ssid = ssid_match.group(1) if ssid_match else ""
    country = country_match.group(1) if country_match else ""
    return ssid, country


def _wifi_form() -> tuple[str | None, str | None, str | None]:
    ssid_default, country_default = _read_wpa_defaults()
    try:
        ssid = inquirer.text(message="Wi-Fi SSID", default=ssid_default, style=APP_STYLE).execute()
    except (KeyboardInterrupt, EOFError):
        return None, None, None
    if not ssid:
        return None, None, None
    try:
        psk = inquirer.secret(message="Wi-Fi Password", style=APP_STYLE).execute()
    except (KeyboardInterrupt, EOFError):
        return None, None, None
    try:
        country = inquirer.text(message="Country Code (optional)", default=country_default, style=APP_STYLE).execute()
    except (KeyboardInterrupt, EOFError):
        return None, None, None
    return str(ssid).strip(), str(psk or "").strip(), str(country or "").strip()


def _networking_menu() -> None:
    def _select_interface(kind: str, interfaces: list[str]) -> None:
        config = load_config(_config_path())
        current = (
            config.networking.wifi_interface
            if kind == "wifi"
            else config.networking.ethernet_interface
        )
        if not interfaces:
            _message("Interfaces", f"No {kind} interfaces detected.")
            return
        items = [("auto", "Auto (if only one)")]
        for iface in interfaces:
            label = iface
            if current == iface:
                label = f"{label} (selected)"
            items.append((iface, label))
        choice = _menu(f"Select {kind} interface", items, default=current or "auto")
        if choice is None:
            return
        if choice == "auto":
            if kind == "wifi":
                _run_cli_output(["networking", "wifi", "clear-interface"], "Wi-Fi interface")
            else:
                _run_cli_output(["networking", "ethernet", "clear-interface"], "Ethernet interface")
            return
        if kind == "wifi":
            _run_cli_output(["networking", "wifi", "set-interface", "--name", choice], "Wi-Fi interface")
        else:
            _run_cli_output(["networking", "ethernet", "set-interface", "--name", choice], "Ethernet interface")

    def _service_exists(name: str) -> bool:
        for base in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
            if Path(base, f"{name}.service").exists():
                return True
        return False

    def _select_service(candidates: list[str]) -> str:
        for candidate in candidates:
            if _service_exists(candidate):
                return candidate
        return candidates[0]

    def _service_menu(title: str, candidates: list[str], description: str) -> None:
        service = _select_service(candidates)
        if description:
            _message(title, description)
        while True:
            action = _menu(
                title,
                [
                    ("1", "Status"),
                    ("2", "Start"),
                    ("3", "Stop"),
                    ("4", "Restart"),
                    ("5", "Enable"),
                    ("6", "Disable"),
                    ("7", "Back"),
                ],
            )
            if action in (None, "7"):
                return
            action_map = {
                "1": "status",
                "2": "start",
                "3": "stop",
                "4": "restart",
                "5": "enable",
                "6": "disable",
            }
            action_name = action_map.get(action)
            if action_name:
                _run_cli_output(["services", service, action_name], f"{title} {action_name}")

    def _identity_menu() -> None:
        while True:
            action = _menu(
                "Identity & Interfaces",
                [
                    ("1", "Set hostname"),
                    ("2", "Select Wi-Fi interface"),
                    ("3", "Select ethernet interface"),
                    ("4", "Back"),
                ],
            )
            if action in (None, "4"):
                return
            if action == "1":
                hostname = _inputbox("Hostname", "Enter new hostname:", os.uname().nodename)
                if hostname:
                    _run_cli(["networking", "hostname", "set", "--name", hostname])
                    _run_cli(["networking", "apply"])
                    _message("Hostname", f"mpwrd-config is now reachable at\n{hostname}.local")
            elif action == "2":
                _select_interface("wifi", list_wifi_interfaces())
            elif action == "3":
                _select_interface("ethernet", list_ethernet_interfaces())

    def _wifi_settings_menu() -> None:
        while True:
            action = _menu(
                "Wi-Fi Settings",
                [
                    ("1", "Show Wi-Fi status"),
                    ("2", "Change Wi-Fi settings"),
                    ("3", "Enable Wi-Fi"),
                    ("4", "Disable Wi-Fi"),
                    ("5", "Restart Wi-Fi"),
                    ("6", "Back"),
                ],
            )
            if action in (None, "6"):
                return
            if action == "1":
                _run_cli_output(["networking", "wifi", "status"], "Wi-Fi settings")
            elif action == "2":
                ssid, psk, country = _wifi_form()
                if ssid:
                    args = ["networking", "wifi", "set", "--ssid", ssid, "--psk", psk]
                    if country:
                        args.extend(["--country", country])
                    _run_cli(args)
                    _run_cli(["networking", "apply"])
                    _message(
                        "Wi-Fi",
                        f"Wi-Fi settings saved.\nSSID: {ssid}\nPassword: (hidden)\nCountry: {country or ''}",
                    )
            elif action == "3":
                _run_cli_output(["networking", "wifi", "enable"], "Wi-Fi")
            elif action == "4":
                _run_cli_output(["networking", "wifi", "disable"], "Wi-Fi")
            elif action == "5":
                if not _has_wifi_interface():
                    _message("Restart Wi-Fi", "No Wi-Fi adapter detected.\n\nIs a Wi-Fi adapter connected?")
                elif _yesno("Restart Wi-Fi", "Wi-Fi will be restarted.\n\nProceed?"):
                    _run_cli_output(["networking", "wifi", "restart"], "Restart Wi-Fi")

    def _network_services_menu() -> None:
        _service_menu(
            "Avahi-Daemon",
            ["avahi-daemon"],
            "The Avahi-Daemon service advertises mpwrd-config on the LAN.",
        )

    def _diagnostics_menu() -> None:
        while True:
            action = _menu(
                "Networking Diagnostics",
                [
                    ("1", "Show Wi-Fi status"),
                    ("2", "Show ethernet status"),
                    ("3", "Show IP addresses"),
                    ("4", "Test internet connection"),
                    ("5", "Back"),
                ],
            )
            if action in (None, "5"):
                return
            if action == "1":
                _run_cli_output(["networking", "wifi", "status"], "Wi-Fi status")
            elif action == "2":
                _run_cli_output(["networking", "eth-status"], "Ethernet status")
            elif action == "3":
                _run_cli_output(["networking", "ip"], "IP addresses")
            elif action == "4":
                _run_cli_output(["networking", "test"], "Internet test")

    while True:
        choice = _menu(
            "Networking",
            [
                ("1", "Identity & Interfaces"),
                ("2", "Wi-Fi Settings"),
                ("3", "Wi-Fi Mesh Sync"),
                ("4", "Networking Services"),
                ("5", "Diagnostics"),
                ("6", "Back"),
            ],
        )
        if choice in (None, "6"):
            return
        if choice == "1":
            _identity_menu()
        elif choice == "2":
            _wifi_settings_menu()
        elif choice == "3":
            _wifi_mesh_menu()
        elif choice == "4":
            _network_services_menu()
        elif choice == "5":
            _diagnostics_menu()


def _meshtastic_full_settings_menu() -> None:
    def _show(title: str, result) -> None:
        _message(title, result.stdout.strip() or "No output.")

    def _prompt_index() -> int | None:
        value = _input_with_validation(
            "Channel index",
            "Enter channel index (0+):",
            "0",
            lambda v: v.isdigit(),
            "Enter a numeric channel index.",
        )
        if value is None:
            return None
        return int(value)

    manage_full_control_conflicts("stop")
    try:
        while True:
            choice = _menu(
                "Meshtastic Settings",
                [
                    ("1", "Show preferences + modules"),
                    ("2", "List preference fields"),
                    ("3", "Get preference value"),
                    ("4", "Set preference value"),
                    ("5", "Show channels"),
                    ("6", "Set channel field"),
                    ("7", "Add channel"),
                    ("8", "Delete channel"),
                    ("9", "Enable channel"),
                    ("10", "Disable channel"),
                    ("11", "Set channels from URL"),
                    ("12", "Add channels from URL"),
                    ("13", "Back"),
                ],
            )
            if choice in (None, "13"):
                return
            if choice == "1":
                _show("Meshtastic settings", meshtastic_config("settings"))
            elif choice == "2":
                _show("Preference fields", list_preference_fields())
            elif choice == "3":
                field = _inputbox("Get preference", "Enter preference field (e.g. power.ls_secs):")
                if field:
                    _show("Preference value", get_preference(field))
            elif choice == "4":
                field = _inputbox("Set preference", "Enter preference field (e.g. power.ls_secs):")
                if field:
                    value = _inputbox("Set preference", "Enter value:")
                    if value is not None:
                        _show("Set preference", set_preference(field, value))
            elif choice == "5":
                _show("Meshtastic channels", meshtastic_config("channels"))
            elif choice == "6":
                index = _prompt_index()
                if index is None:
                    continue
                field = _inputbox("Set channel field", "Enter channel field (e.g. name, psk, uplink):")
                if field:
                    value = _inputbox("Set channel field", "Enter value:")
                    if value is not None:
                        _show("Set channel", channel_set(index, field, value))
            elif choice == "7":
                name = _inputbox("Add channel", "Enter channel name:")
                if name:
                    _show("Add channel", channel_add(name))
            elif choice == "8":
                index = _prompt_index()
                if index is None:
                    continue
                if _yesno("Delete channel", f"Delete channel {index}?"):
                    _show("Delete channel", channel_delete(index))
            elif choice == "9":
                index = _prompt_index()
                if index is None:
                    continue
                _show("Enable channel", channel_enable(index))
            elif choice == "10":
                index = _prompt_index()
                if index is None:
                    continue
                _show("Disable channel", channel_disable(index))
            elif choice == "11":
                url = _inputbox("Set channels from URL", "Enter configuration URL:")
                if url:
                    if _yesno(
                        "Set channels from URL",
                        "This will overwrite LoRa settings and channels.\n\nProceed?",
                    ):
                        _show("Set channels from URL", channel_set_url(url))
            elif choice == "12":
                url = _inputbox("Add channels from URL", "Enter configuration URL:")
                if url:
                    _show("Add channels from URL", channel_add_url(url))
    finally:
        manage_full_control_conflicts("start")


def _meshtastic_repo_menu() -> None:
    while True:
        choice = _menu(
            "Meshtastic Repo",
            [
                ("1", "Show current repo"),
                ("2", "Install/Update repo (choose channel)"),
                ("3", "Use beta repo (install/update)"),
                ("4", "Use alpha repo (install/update)"),
                ("5", "Use daily repo (install/update)"),
                ("6", "Upgrade meshtasticd"),
                ("7", "Uninstall meshtasticd"),
                ("8", "Back"),
            ],
        )
        if choice in (None, "8"):
            return
        if choice == "1":
            _message("Meshtastic Repo", meshtastic_repo_status().stdout)
        elif choice == "2":
            channel = _menu(
                "Install/Update Repo",
                [
                    ("beta", "Beta"),
                    ("alpha", "Alpha"),
                    ("daily", "Daily"),
                    ("back", "Back"),
                ],
            )
            if channel in (None, "back"):
                continue
            _message("Meshtastic Repo", set_meshtastic_repo(channel).stdout)
        elif choice == "3":
            _message("Meshtastic Repo", set_meshtastic_repo("beta").stdout)
        elif choice == "4":
            _message("Meshtastic Repo", set_meshtastic_repo("alpha").stdout)
        elif choice == "5":
            _message("Meshtastic Repo", set_meshtastic_repo("daily").stdout)
        elif choice == "6":
            if _yesno("Upgrade", "Upgrade meshtasticd now?"):
                _run_cli_output(["meshtastic", "upgrade"], "Upgrade")
        elif choice == "7":
            if _yesno("Uninstall", "Uninstall meshtasticd?"):
                _run_cli_output(["meshtastic", "uninstall"], "Uninstall")


def _meshtastic_menu() -> None:
    def _service_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic Service",
                [
                    ("1", "Status"),
                    ("2", "Start"),
                    ("3", "Stop"),
                    ("4", "Restart"),
                    ("5", "Enable"),
                    ("6", "Disable"),
                    ("7", "MAC address source"),
                    ("8", "Back"),
                ],
            )
            if action in (None, "8"):
                return
            action_map = {
                "1": "status",
                "2": "start",
                "3": "stop",
                "4": "restart",
                "5": "enable",
                "6": "disable",
            }
            action_name = action_map.get(action)
            if action_name:
                _run_cli_output(["meshtastic", "service", action_name], f"Meshtastic service {action_name}")
            elif action == "7":
                _mac_source_menu()

    def _i2c_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic I2C",
                [
                    ("1", "Check I2C state"),
                    ("2", "Enable I2C"),
                    ("3", "Disable I2C"),
                    ("4", "Back"),
                ],
            )
            if action in (None, "4"):
                return
            if action == "1":
                _run_cli_output(["meshtastic", "i2c", "check"], "I2C status")
            elif action == "2":
                _run_cli_output(["meshtastic", "i2c", "enable"], "I2C enable")
            elif action == "3":
                _run_cli_output(["meshtastic", "i2c", "disable"], "I2C disable")

    def _mac_source_menu() -> None:
        current = mac_address_source().stdout.strip()
        options = mac_address_source_options()
        option_keys = {value for value, _ in options}
        if current and current not in option_keys:
            options.insert(0, (current, f"Current ({current})"))
        options.append(("back", "Back"))
        choice = _menu("MAC Address Source", options, default=current if current in option_keys else None)
        if not choice or choice == "back":
            return
        result = set_mac_address_source(choice)
        _message("MAC Address Source", result.stdout.strip() or "Done.")

    def _keys_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic Keys",
                [
                    ("1", "Show public key"),
                    ("2", "Set public key"),
                    ("3", "Show private key"),
                    ("4", "Set private key"),
                    ("5", "List admin keys"),
                    ("6", "Add admin key"),
                    ("7", "Clear admin keys"),
                    ("8", "Legacy admin status"),
                    ("9", "Set legacy admin"),
                    ("10", "Back"),
                ],
            )
            if action in (None, "10"):
                return
            if action == "1":
                _run_cli_output(["meshtastic", "public-key"], "Public key")
            elif action == "2":
                key = _inputbox("Public key", "Enter base64 public key:")
                if key:
                    _run_cli_output(["meshtastic", "set-public-key", "--key", key], "Public key")
            elif action == "3":
                _run_cli_output(["meshtastic", "private-key"], "Private key")
            elif action == "4":
                key = _inputbox("Private key", "Enter base64 private key:")
                if key:
                    _run_cli_output(["meshtastic", "set-private-key", "--key", key], "Private key")
            elif action == "5":
                _run_cli_output(["meshtastic", "admin-keys"], "Admin keys")
            elif action == "6":
                key = _inputbox("Admin key", "Enter base64 admin key:")
                if key:
                    _run_cli_output(["meshtastic", "add-admin-key", "--key", key], "Admin key")
            elif action == "7":
                if _yesno("Admin keys", "Clear all admin keys?"):
                    _run_cli_output(["meshtastic", "clear-admin-keys"], "Admin keys")
            elif action == "8":
                _run_cli_output(["meshtastic", "legacy-admin"], "Legacy admin")
            elif action == "9":
                state = _menu("Legacy admin", [("true", "Enable"), ("false", "Disable"), ("back", "Back")])
                if state and state != "back":
                    _run_cli_output(["meshtastic", "set-legacy-admin", "--enabled", state], "Legacy admin")

    def _advanced_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic Advanced",
                [
                    ("1", "Run custom Meshtastic command"),
                    ("2", "Back"),
                ],
            )
            if action in (None, "2"):
                return
            if action == "1":
                command = _inputbox("Meshtastic command", "Enter meshtastic CLI args (e.g. --set lora.region US):")
                if command:
                    _run_cli_output(["meshtastic", "update", "--command", command, "--label", "Custom"], "Meshtastic update")

    def _overview_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic Overview",
                [
                    ("1", "Show node info"),
                    ("2", "Show node summary"),
                    ("3", "Back"),
                ],
            )
            if action in (None, "3"):
                return
            if action == "1":
                _run_cli_output(["meshtastic", "info"], "Meshtastic info")
            elif action == "2":
                _run_cli_output(["meshtastic", "summary"], "Meshtastic summary")

    def _url_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic URL",
                [
                    ("1", "Show config URL + QR"),
                    ("2", "Set config URL"),
                    ("3", "Back"),
                ],
            )
            if action in (None, "3"):
                return
            if action == "1":
                _run_cli_output(["meshtastic", "config-qr"], "Config URL")
            elif action == "2":
                url = _inputbox("Config URL", "Enter config URL:")
                if url and _yesno(
                    "Config URL",
                    "This will overwrite LoRa settings and channels.\n\nProceed?",
                ):
                    _run_cli_output(["meshtastic", "set-config-url", "--url", url], "Config URL")

    def _diagnostics_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic Diagnostics",
                [
                    ("1", "Show LoRa settings"),
                    ("2", "Mesh connectivity test"),
                    ("3", "Back"),
                ],
            )
            if action in (None, "3"):
                return
            if action == "1":
                _run_cli_output(["meshtastic", "lora", "show"], "LoRa settings")
            elif action == "2":
                _run_cli_output(["meshtastic", "mesh-test"], "Mesh test")

    while True:
        choice = _menu(
            "Meshtastic",
            [
                ("1", "Meshtastic overview"),
                ("2", "URL"),
                ("3", "LoRa configuration"),
                ("4", "Preferences & channels"),
                ("5", "Keys & admin"),
                ("6", "Meshtastic repo"),
                ("7", "meshtasticd service"),
                ("8", "I2C"),
                ("9", "Diagnostics"),
                ("10", "Advanced"),
                ("11", "Back"),
            ],
        )
        if choice in (None, "11"):
            return
        if choice == "1":
            _overview_menu()
        elif choice == "2":
            _url_menu()
        elif choice == "3":
            _meshtastic_lora_menu()
        elif choice == "4":
            _meshtastic_full_settings_menu()
        elif choice == "5":
            _keys_menu()
        elif choice == "6":
            _meshtastic_repo_menu()
        elif choice == "7":
            _service_menu()
        elif choice == "8":
            _i2c_menu()
        elif choice == "9":
            _diagnostics_menu()
        elif choice == "10":
            _advanced_menu()


def _bool_prompt(title: str, prompt: str, current: str | None) -> str | None:
    default = None
    if isinstance(current, str):
        if current.lower() in {"true", "false"}:
            default = current.lower()
    choice = _menu(title, [("true", "True"), ("false", "False"), ("skip", "Cancel")], default=default)
    if choice in (None, "skip"):
        return None
    return choice


def _input_with_validation(title: str, prompt: str, default: str, validator, error: str) -> str | None:
    while True:
        value = _inputbox(title, prompt, default)
        if value is None or value == "":
            return None
        if validator(value):
            return value
        _message(title, error)


def _meshtastic_lora_menu() -> None:
    result, current = lora_settings()
    if result.returncode != 0:
        _message("Meshtastic", result.stdout.strip() or "Unable to query Meshtastic.")
        return

    def apply(settings: dict[str, str], title: str) -> None:
        nonlocal current
        if not settings:
            return
        response = set_lora_settings(settings)
        _message(title, response.stdout.strip() or "Done.")
        if response.returncode == 0:
            refreshed, updated = lora_settings()
            if refreshed.returncode == 0:
                current = updated

    def select_radio() -> None:
        current_model = current_radio().stdout.strip()
        options = [
            ("lr1121_tcxo", "LR1121 TCXO"),
            ("sx1262_tcxo", "SX1262 TCXO (Ebyte e22-900m30s / Heltec ht-ra62 / Seeed wio-sx1262)"),
            ("sx1262_xtal", "SX1262 XTAL (Ebyte e80-900m22s / Waveshare / AI Thinker ra-01sh)"),
            ("lora-meshstick-1262", "LoRa Meshstick 1262 (USB)"),
            ("none", "Simulated radio"),
        ]
        model = _menu(
            "LoRa radio",
            options,
            default=current_model if current_model in {opt[0] for opt in options} else None,
        )
        if model:
            _run_cli_output(["meshtastic", "set-radio", "--model", model], "LoRa radio")

    def config_url_prompt() -> bool:
        nonlocal current
        url = _inputbox("Meshtastic URL", "Enter configuration URL:")
        if not url:
            return False
        if not _yesno(
            "Meshtastic URL",
            "This will overwrite LoRa settings and channels.\n\nProceed?",
        ):
            return False
        response = set_config_url(url)
        _message("Meshtastic URL", response.stdout.strip() or "URL updated.")
        refreshed, updated = lora_settings()
        if refreshed.returncode == 0:
            current = updated
        return True

    while True:
        choice = _menu(
            "LoRa settings",
            [
                ("1", "Wizard (set all)"),
                ("2", "Set LoRa radio model"),
                ("3", "Show radio selection"),
                ("4", "Configure with URL"),
                ("5", "Region"),
                ("6", "Use modem preset"),
                ("7", "Preset"),
                ("8", "Bandwidth"),
                ("9", "Spread factor"),
                ("10", "Coding rate"),
                ("11", "Frequency offset"),
                ("12", "Hop limit"),
                ("13", "Enable/disable TX"),
                ("14", "TX power"),
                ("15", "Frequency slot"),
                ("16", "Override duty cycle"),
                ("17", "SX126X RX boosted gain"),
                ("18", "Override frequency"),
                ("19", "Ignore MQTT"),
                ("20", "OK to MQTT"),
                ("21", "Show current LoRa settings"),
                ("22", "Show config URL + QR"),
                ("23", "Back"),
            ],
        )
        if choice in (None, "23"):
            return
        if choice == "1":
            if _yesno(
                "LoRa wizard",
                f"Current radio: {current_radio().stdout.strip()}\n\nSet radio model?",
            ):
                select_radio()
            method = _menu(
                "Meshtastic configuration method",
                [
                    ("url", "Automatic configuration with URL"),
                    ("manual", "Manual configuration"),
                    ("cancel", "Cancel"),
                ],
                default="manual",
            )
            if method in (None, "cancel"):
                continue
            if method == "url":
                config_url_prompt()
                refreshed, updated = lora_settings()
                if refreshed.returncode == 0:
                    current = updated
                continue
            _meshtastic_lora_wizard(current)
        elif choice == "2":
            select_radio()
        elif choice == "3":
            _run_cli_output(["meshtastic", "radio"], "Radio selection")
        elif choice == "4":
            config_url_prompt()
        elif choice == "5":
            regions = [
                "UNSET",
                "US",
                "EU_433",
                "EU_868",
                "CN",
                "JP",
                "ANZ",
                "KR",
                "TW",
                "RU",
                "IN",
                "NZ_865",
                "TH",
                "LORA_24",
                "UA_433",
                "UA_868",
                "MY_433",
                "MY_919",
                "SG_923",
            ]
            region = _menu("Region", [(value, value) for value in regions], default=str(current.get("lora_region") or "UNSET"))
            if region:
                apply({"region": region}, "Region")
        elif choice == "6":
            value = _bool_prompt("Use modem preset", "Use preset?", str(current.get("lora_usePreset")))
            if value is not None:
                apply({"use_preset": value}, "Use modem preset")
        elif choice == "7":
            presets = [
                "LONG_FAST",
                "LONG_SLOW",
                "VERY_LONG_SLOW",
                "MEDIUM_SLOW",
                "MEDIUM_FAST",
                "SHORT_SLOW",
                "SHORT_FAST",
                "SHORT_TURBO",
            ]
            preset = _menu("Preset", [(value, value) for value in presets], default=str(current.get("lora_modemPreset")))
            if preset:
                apply({"modem_preset": preset}, "Preset")
        elif choice == "8":
            bandwidth = _menu(
                "Bandwidth",
                [(value, value) for value in ["0", "31", "62", "125", "250", "500"]],
                default=str(current.get("lora_bandwidth")),
            )
            if bandwidth:
                apply({"bandwidth": bandwidth}, "Bandwidth")
        elif choice == "9":
            spread = _menu(
                "Spread factor",
                [(value, value) for value in ["0", "7", "8", "9", "10", "11", "12"]],
                default=str(current.get("lora_spreadFactor")),
            )
            if spread:
                apply({"spread_factor": spread}, "Spread factor")
        elif choice == "10":
            coding = _menu(
                "Coding rate",
                [(value, value) for value in ["0", "5", "6", "7", "8"]],
                default=str(current.get("lora_codingRate")),
            )
            if coding:
                apply({"coding_rate": coding}, "Coding rate")
        elif choice == "11":
            value = _input_with_validation(
                "Frequency offset",
                "Frequency offset (0-1000000):",
                str(current.get("lora_frequencyOffset") or "0"),
                lambda v: re.fullmatch(r"[0-9]{1,7}(\\.[0-9]+)?", v) is not None and float(v) <= 1000000,
                "Must be between 0 and 1000000.",
            )
            if value:
                apply({"frequency_offset": value}, "Frequency offset")
        elif choice == "12":
            value = _input_with_validation(
                "Hop limit",
                "Hop limit (0-7):",
                str(current.get("lora_hopLimit") or "3"),
                lambda v: v.isdigit() and 0 <= int(v) <= 7,
                "Must be an integer between 0 and 7.",
            )
            if value:
                apply({"hop_limit": value}, "Hop limit")
        elif choice == "13":
            value = _bool_prompt("TX enabled", "Enable TX?", str(current.get("lora_txEnabled")))
            if value is not None:
                apply({"tx_enabled": value}, "TX enabled")
        elif choice == "14":
            value = _input_with_validation(
                "TX power",
                "TX power (0-30):",
                str(current.get("lora_txPower") or "0"),
                lambda v: v.isdigit() and 0 <= int(v) <= 30,
                "Must be an integer between 0 and 30.",
            )
            if value:
                apply({"tx_power": value}, "TX power")
        elif choice == "15":
            value = _input_with_validation(
                "Frequency slot",
                "Frequency slot (0+):",
                str(current.get("lora_channelNum") or "0"),
                lambda v: v.isdigit() and int(v) >= 0,
                "Must be an integer 0 or higher.",
            )
            if value:
                apply({"channel_num": value}, "Frequency slot")
        elif choice == "16":
            value = _bool_prompt("Override duty cycle", "Override duty cycle?", str(current.get("lora_overrideDutyCycle")))
            if value is not None:
                apply({"override_duty_cycle": value}, "Override duty cycle")
        elif choice == "17":
            value = _bool_prompt(
                "SX126X RX boosted gain",
                "Enable SX126X RX boosted gain?",
                str(current.get("lora_sx126xRxBoostedGain")),
            )
            if value is not None:
                apply({"sx126x_rx_boosted_gain": value}, "SX126X RX boosted gain")
        elif choice == "18":
            value = _input_with_validation(
                "Override frequency",
                "Override frequency (MHz, 0+):",
                str(current.get("lora_overrideFrequency") or "0"),
                lambda v: re.fullmatch(r"[0-9]+(\\.[0-9]+)?", v) is not None,
                "Must be a number 0 or higher.",
            )
            if value:
                apply({"override_frequency": value}, "Override frequency")
        elif choice == "19":
            value = _bool_prompt("Ignore MQTT", "Ignore MQTT?", str(current.get("lora_ignoreMqtt")))
            if value is not None:
                apply({"ignore_mqtt": value}, "Ignore MQTT")
        elif choice == "20":
            value = _bool_prompt("OK to MQTT", "OK to MQTT?", str(current.get("lora_configOkToMqtt")))
            if value is not None:
                apply({"config_ok_to_mqtt": value}, "OK to MQTT")
        elif choice == "21":
            _run_cli_output(["meshtastic", "lora", "show"], "LoRa settings")
        elif choice == "22":
            _run_cli_output(["meshtastic", "config-qr"], "LoRa config URL")


def _meshtastic_lora_wizard(current: dict[str, Any]) -> None:
    settings: dict[str, str] = {}
    region = _menu(
        "Region",
        [(value, value) for value in ["UNSET", "US", "EU_433", "EU_868", "CN", "JP", "ANZ", "KR", "TW", "RU", "IN", "NZ_865", "TH", "LORA_24", "UA_433", "UA_868", "MY_433", "MY_919", "SG_923"]],
        default=str(current.get("lora_region")),
    )
    if region:
        settings["region"] = region
    use_preset = _bool_prompt("Use modem preset", "Use preset?", str(current.get("lora_usePreset")))
    if use_preset is not None:
        settings["use_preset"] = use_preset
    if use_preset == "true":
        preset = _menu(
            "Preset",
            [(value, value) for value in ["LONG_FAST", "LONG_SLOW", "VERY_LONG_SLOW", "MEDIUM_SLOW", "MEDIUM_FAST", "SHORT_SLOW", "SHORT_FAST", "SHORT_TURBO"]],
            default=str(current.get("lora_modemPreset")),
        )
        if preset:
            settings["modem_preset"] = preset
    elif use_preset == "false":
        bandwidth = _menu("Bandwidth", [(value, value) for value in ["0", "31", "62", "125", "250", "500"]], default=str(current.get("lora_bandwidth")))
        if bandwidth:
            settings["bandwidth"] = bandwidth
        spread = _menu("Spread factor", [(value, value) for value in ["0", "7", "8", "9", "10", "11", "12"]], default=str(current.get("lora_spreadFactor")))
        if spread:
            settings["spread_factor"] = spread
        coding = _menu("Coding rate", [(value, value) for value in ["0", "5", "6", "7", "8"]], default=str(current.get("lora_codingRate")))
        if coding:
            settings["coding_rate"] = coding
    freq_offset = _input_with_validation(
        "Frequency offset",
        "Frequency offset (0-1000000):",
        str(current.get("lora_frequencyOffset") or "0"),
        lambda v: re.fullmatch(r"[0-9]{1,7}(\\.[0-9]+)?", v) is not None and float(v) <= 1000000,
        "Must be between 0 and 1000000.",
    )
    if freq_offset:
        settings["frequency_offset"] = freq_offset
    hop_limit = _input_with_validation(
        "Hop limit",
        "Hop limit (0-7):",
        str(current.get("lora_hopLimit") or "3"),
        lambda v: v.isdigit() and 0 <= int(v) <= 7,
        "Must be an integer between 0 and 7.",
    )
    if hop_limit:
        settings["hop_limit"] = hop_limit
    tx_enabled = _bool_prompt("TX enabled", "Enable TX?", str(current.get("lora_txEnabled")))
    if tx_enabled is not None:
        settings["tx_enabled"] = tx_enabled
    tx_power = _input_with_validation(
        "TX power",
        "TX power (0-30):",
        str(current.get("lora_txPower") or "0"),
        lambda v: v.isdigit() and 0 <= int(v) <= 30,
        "Must be an integer between 0 and 30.",
    )
    if tx_power:
        settings["tx_power"] = tx_power
    channel_num = _input_with_validation(
        "Frequency slot",
        "Frequency slot (0+):",
        str(current.get("lora_channelNum") or "0"),
        lambda v: v.isdigit() and int(v) >= 0,
        "Must be an integer 0 or higher.",
    )
    if channel_num:
        settings["channel_num"] = channel_num
    override_duty = _bool_prompt("Override duty cycle", "Override duty cycle?", str(current.get("lora_overrideDutyCycle")))
    if override_duty is not None:
        settings["override_duty_cycle"] = override_duty
    rx_gain = _bool_prompt(
        "SX126X RX boosted gain",
        "Enable SX126X RX boosted gain?",
        str(current.get("lora_sx126xRxBoostedGain")),
    )
    if rx_gain is not None:
        settings["sx126x_rx_boosted_gain"] = rx_gain
    override_freq = _input_with_validation(
        "Override frequency",
        "Override frequency (MHz, 0+):",
        str(current.get("lora_overrideFrequency") or "0"),
        lambda v: re.fullmatch(r"[0-9]+(\\.[0-9]+)?", v) is not None,
        "Must be a number 0 or higher.",
    )
    if override_freq:
        settings["override_frequency"] = override_freq
    ignore_mqtt = _bool_prompt("Ignore MQTT", "Ignore MQTT?", str(current.get("lora_ignoreMqtt")))
    if ignore_mqtt is not None:
        settings["ignore_mqtt"] = ignore_mqtt
    ok_mqtt = _bool_prompt("OK to MQTT", "OK to MQTT?", str(current.get("lora_configOkToMqtt")))
    if ok_mqtt is not None:
        settings["config_ok_to_mqtt"] = ok_mqtt
    if settings:
        response = set_lora_settings(settings)
        _message("LoRa wizard", response.stdout.strip() or "Done.")


def _kernel_menu() -> None:
    def _format_module_label(module) -> str:
        flags = []
        if module.loaded:
            flags.append("L")
        if module.boot:
            flags.append("B")
        if module.blacklisted:
            flags.append("X")
        marker = "".join(flags) or "-"
        label = f"{marker:3} {module.name}"
        if module.blacklisted:
            label = f"{label} (BLACKLISTED)"
        return label

    def _browse_modules() -> None:
        modules = list_module_overview()
        if not modules:
            _message("Kernel Modules", "No modules found in the kernel module directory.")
            return
        module_map = {module.name: module for module in modules}
        while True:
            items = [(module.name, _format_module_label(module)) for module in modules]
            items.append(("back", "Back"))
            choice = _menu("Kernel Modules", items)
            if choice in (None, "back"):
                return
            module = module_map.get(choice)
            if not module:
                continue
            while True:
                action = _menu(
                    choice,
                    [
                        ("info", "Show module info"),
                        ("enable", "Enable module"),
                        ("disable", "Disable module"),
                        ("blacklist-set", "Blacklist module"),
                        ("blacklist-clear", "Un-blacklist module"),
                        ("back", "Back"),
                    ],
                )
                if action in (None, "back"):
                    break
                if action == "info":
                    info = module_info(choice)
                    status = (
                        f"Loaded: {'yes' if module.loaded else 'no'}\n"
                        f"Boot: {'yes' if module.boot else 'no'}\n"
                        f"Blacklisted: {'yes' if module.blacklisted else 'no'}\n\n"
                    )
                    _message(choice, status + info.stdout)
                    continue
                action_map = {
                    "enable": enable_module,
                    "disable": disable_module,
                    "blacklist-set": blacklist_module,
                    "blacklist-clear": unblacklist_module,
                }
                handler = action_map.get(action)
                if handler:
                    result = handler(choice)
                    _message(choice, result.stdout.strip() or "Done.")
                modules = list_module_overview()
                module_map = {module.name: module for module in modules}
                module = module_map.get(choice)
                if not module:
                    break

    while True:
        choice = _menu(
            "Kernel Modules",
            [
                ("1", "Browse modules"),
                ("2", "List boot modules"),
                ("3", "List active modules"),
                ("4", "List blacklisted modules"),
                ("5", "Enable module"),
                ("6", "Disable module"),
                ("7", "Blacklist module"),
                ("8", "Un-blacklist module"),
                ("9", "Back"),
            ],
        )
        if choice in (None, "9"):
            return
        if choice == "1":
            _browse_modules()
        elif choice == "2":
            result = list_boot_modules()
            _message("Boot modules", result.stdout.strip() or "none")
        elif choice == "3":
            result = list_active_modules()
            _message("Active modules", result.stdout.strip() or "none")
        elif choice == "4":
            result = list_blacklisted_modules()
            _message("Blacklisted modules", result.stdout.strip() or "none")
        elif choice == "5":
            name = _inputbox("Enable module", "Module name:")
            if name:
                result = enable_module(name)
                _message("Enable module", result.stdout.strip() or "Done.")
        elif choice == "6":
            name = _inputbox("Disable module", "Module name:")
            if name:
                result = disable_module(name)
                _message("Disable module", result.stdout.strip() or "Done.")
        elif choice == "7":
            name = _inputbox("Blacklist module", "Module name:")
            if name:
                result = blacklist_module(name)
                _message("Blacklist module", result.stdout.strip() or "Done.")
        elif choice == "8":
            name = _inputbox("Un-blacklist module", "Module name:")
            if name:
                result = unblacklist_module(name)
                _message("Un-blacklist module", result.stdout.strip() or "Done.")


def _system_menu() -> None:
    while True:
        choice = _menu(
            "System Actions",
            [
                ("1", "Reboot"),
                ("2", "Shutdown"),
                ("3", "Back"),
            ],
        )
        if choice in (None, "3"):
            return
        if choice == "1":
            _run_cli_output(["system", "reboot"], "Reboot")
        elif choice == "2":
            _run_cli_output(["system", "shutdown"], "Shutdown")


def _time_menu() -> None:
    while True:
        choice = _menu(
            "Time & Timezone",
            [
                ("1", "Show current status"),
                ("2", "Set timezone"),
                ("3", "Set time"),
                ("4", "Watchclock"),
                ("5", "Back"),
            ],
        )
        if choice in (None, "5"):
            return
        if choice == "1":
            _message("Time status", time_status().stdout)
        elif choice == "2":
            tz = current_timezone().stdout.strip()
            timezones = subprocess.check_output(["timedatectl", "list-timezones"], text=True).splitlines()
            items = [(zone, "") for zone in timezones]
            selected = _menu("Set Time Zone", items, default=tz)
            if selected:
                result = set_timezone(selected)
                _message("Timezone", result.stdout or "Timezone updated.")
        elif choice == "3":
            now = datetime.now()
            date_value = _calendar("Set Date", f"Current date: {now:%B %d, %Y}", now.day, now.month, now.year)
            if not date_value:
                continue
            time_value = _timebox("Set Time", f"Current time: {now:%H:%M:%S}", now.hour, now.minute, now.second)
            if not time_value:
                continue
            day, month, year = date_value.split("/")
            timespec = f"{year}-{month}-{day} {time_value}"
            result = set_time(timespec)
            _message("System time", result.stdout or "Time updated.")
        elif choice == "4":
            _watchclock_menu()


def _software_action_dialog(title: str, result) -> None:
    body = ""
    if result.user_message:
        body += f"{result.user_message}\n\n"
    if result.output:
        body += f"Log:\n{result.output}"
    _message(title, body or "Done.")


def _software_menu() -> None:
    while True:
        packages = list_packages()
        if not packages:
            _message("Software Manager", "No packages found.")
            return
        items = [(pkg.key, f"{pkg.name} ({'installed' if pkg.installed else 'not installed'})") for pkg in packages]
        items.append(("back", "Back"))
        choice = _menu("Software Manager", items)
        if choice in (None, "back"):
            return
        info = package_info(choice)
        if not _yesno(
            info.name,
            f"{info.description or ''}\n\nInstalled: {info.installed}\nOptions: {info.options}",
        ):
            continue
        while True:
            actions: list[tuple[str, str]] = []
            if "l" in info.options and info.installed:
                actions.append(("run", "Run software"))
            if "i" in info.options and not info.installed:
                actions.append(("install", "Install"))
            if "u" in info.options and info.installed:
                actions.append(("uninstall", "Uninstall"))
            if "a" in info.options and info.installed:
                actions.append(("init", "Initialize"))
            if "g" in info.options and info.installed:
                actions.append(("upgrade", "Upgrade"))
            if "e" in info.options and info.installed:
                actions.append(("enable", "Enable service"))
                actions.append(("disable", "Disable service"))
                actions.append(("stop", "Stop service"))
                actions.append(("restart", "Start/restart service"))
            if "S" in info.options and info.installed:
                actions.append(("status", "Detailed service status"))
            if "G" in info.options:
                actions.append(("license", "License"))
            extra_actions = {f"extra:{key}": label for key, label in info.extra_actions}
            for action_key, label in extra_actions.items():
                actions.append((action_key, label))
            actions.append(("back", "Back"))
            action = _menu(info.name, actions)
            if action in (None, "back"):
                break
            if action in extra_actions:
                result = run_action(choice, f"-{action.split(':', 1)[1]}")
                _software_action_dialog(extra_actions[action], result)
                continue
            if action == "license":
                _message("License", license_text(choice))
                continue
            if action == "status":
                output = package_service_action(choice, "-S").stdout
                _message("Service status", output)
                continue
            if action in {"enable", "disable", "stop", "restart"}:
                flag_map = {
                    "enable": "-e",
                    "disable": "-d",
                    "stop": "-s",
                    "restart": "-r",
                }
                result = package_service_action(choice, flag_map[action])
                _message(info.name, result.stdout or "Done.")
                continue
            if action == "install":
                result = run_action(choice, "-i")
                _software_action_dialog("Install", result)
            elif action == "uninstall":
                result = run_action(choice, "-u")
                _software_action_dialog("Uninstall", result)
            elif action == "upgrade":
                result = run_action(choice, "-g")
                _software_action_dialog("Upgrade", result)
            elif action == "init":
                result = run_action(choice, "-a")
                _software_action_dialog("Initialize", result)
            elif action == "run":
                result = run_action(choice, "-l")
                _software_action_dialog("Run", result)


def _utilities_menu() -> None:
    while True:
        choice = _menu(
            "System Utilities",
            [
                ("1", "Web UI service"),
                ("2", "ACT LED"),
                ("3", "Logging"),
                ("4", "ttyd service"),
                ("5", "Regenerate SSH keys"),
                ("6", "System info"),
                ("7", "Run USB configuration tool"),
                ("8", "Re-run first-boot script"),
                ("9", "Run OEM luckfox-config"),
                ("10", "Process viewer/manager"),
                ("11", "Back"),
            ],
        )
        if choice in (None, "11"):
            return
        if choice == "1":
            _web_ui_menu()
        elif choice == "2":
            state = _menu("ACT LED", [("enable", "Enable"), ("disable", "Disable"), ("check", "Check")])
            if state:
                _message("ACT LED", act_led(state).stdout)
        elif choice == "3":
            state = _menu("Logging", [("enable", "Enable"), ("disable", "Disable"), ("check", "Check")])
            if state:
                _message("Logging", logging_state(state).stdout)
        elif choice == "4":
            action = _menu(
                "ttyd",
                [("enable", "Enable"), ("disable", "Disable"), ("start", "Start"), ("stop", "Stop"), ("restart", "Restart"), ("check", "Check")],
            )
            if action:
                _message("ttyd", ttyd_action(action).stdout)
        elif choice == "5":
            if _yesno("SSH Keys", "Regenerate SSH host keys?"):
                _message("SSH Keys", generate_ssh_keys().stdout)
        elif choice == "6":
            section = _menu(
                "System Info",
                [
                    ("all", "All"),
                    ("cpu", "CPU"),
                    ("os", "OS"),
                    ("storage", "Storage"),
                    ("network", "Networking"),
                    ("peripherals", "Peripherals"),
                ],
            )
            if section == "all":
                _message("System Info", all_system_info().stdout)
            elif section == "cpu":
                _message("CPU Info", cpu_info().stdout)
            elif section == "os":
                _message("OS Info", os_info().stdout)
            elif section == "storage":
                _message("Storage Info", storage_info().stdout)
            elif section == "network":
                _message("Networking Info", networking_info().stdout)
            elif section == "peripherals":
                _message("Peripherals Info", peripherals_info().stdout)
        elif choice == "7":
            if _yesno(
                "USB Configuration Tool",
                "The USB configuration tool applies settings from a USB drive.\n\nRun now?",
            ):
                result = run_usb_config_tool()
                _message("USB Configuration Tool", result.stdout or "Done.")
        elif choice == "8":
            if _yesno("First Boot", "Re-run the first-boot script now?"):
                result = run_first_boot()
                _message("First Boot", result.stdout or "Done.")
        elif choice == "9":
            command = legacy_tool_command(["luckfox-config", "raspi-config"])
            if command:
                _run_interactive(command, "OEM Config", "luckfox-config not found.")
            else:
                _message("OEM Config", "luckfox-config not found.")
        elif choice == "10":
            command = legacy_tool_command(["htop", "top"])
            if command:
                _run_interactive(command, "Process Viewer", "Process viewer not available.")
            else:
                _message("Process Viewer", "Process viewer not available.")


def _help_menu() -> None:
    while True:
        choice = _menu(
            "Help / About",
            [
                ("1", "About mpwrd-config"),
                ("2", "Display pinout"),
                ("3", "Femtofox licensing info - short"),
                ("4", "Femtofox licensing info - long"),
                ("5", "Meshtastic licensing info"),
                ("6", "About Luckfox"),
                ("7", "About Ubuntu"),
                ("8", "Back"),
            ],
        )
        if choice in (None, "8"):
            return
        if choice == "1":
            _message("About mpwrd-config", license_info("about").stdout)
        elif choice == "2":
            pinout_choice = _menu(
                "Pinouts",
                [
                    ("femtofox", "Femtofox Pro/CE"),
                    ("zero", "Femtofox Zero"),
                    ("tiny", "Femtofox Tiny"),
                    ("luckfox", "Luckfox Pico Mini"),
                    ("back", "Back"),
                ],
            )
            if pinout_choice and pinout_choice != "back":
                _message("Pinout", pinout_info(pinout_choice).stdout)
        elif choice == "3":
            _message("Femtofox license", license_info("short").stdout)
        elif choice == "4":
            _message("Femtofox license (long)", license_info("long").stdout)
        elif choice == "5":
            _message("Meshtastic license", license_info("meshtastic").stdout)
        elif choice == "6":
            _message("About Luckfox", license_info("luckfox").stdout)
        elif choice == "7":
            _message("About Ubuntu", license_info("ubuntu").stdout)


def _web_ui_menu() -> None:
    socket_name = "mpwrd-config-web.socket"
    service_name = "mpwrd-config-web.service"
    repo_root = Path(__file__).resolve().parent.parent
    systemd_dir = repo_root / "systemd"
    dest_dir = Path("/etc/systemd/system")

    def _unit_installed(unit: str) -> bool:
        for base in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
            if Path(base, unit).exists():
                return True
        return False

    def _install_units() -> str:
        messages: list[str] = []
        for unit in (socket_name, service_name):
            source = systemd_dir / unit
            dest = dest_dir / unit
            existed = dest.exists()
            if not source.exists():
                messages.append(f"Missing source file: {source}")
                continue
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            action = "Updated" if existed else "Installed"
            messages.append(f"{action} {unit} to {dest}")
        if messages:
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            return "\n".join(messages)
        return "Service files already installed."

    def _ensure_installed() -> None:
        if _unit_installed(socket_name) and _unit_installed(service_name):
            return
        output = _install_units()
        _message("Web UI Service", output)

    def _parse_state(text: str) -> tuple[bool, bool]:
        enabled = "enabled" in text
        running = "running" in text and "not running" not in text
        return enabled, running

    while True:
        choice = _menu(
            "Web UI Service",
            [
                ("1", "Status"),
                ("2", "Install service files"),
                ("3", "Start"),
                ("4", "Stop"),
                ("5", "Restart"),
                ("6", "Enable"),
                ("7", "Disable"),
                ("8", "Back"),
            ],
        )
        if choice in (None, "8"):
            return

        if choice == "1":
            if not _unit_installed(socket_name) or not _unit_installed(service_name):
                _message(
                    "Web UI Status",
                    "Service files are not installed.\n\nChoose 'Install service files' to add them.",
                )
                continue
            socket_status = system_service_status(socket_name).stdout.strip() or "unknown"
            service_status = system_service_status(service_name).stdout.strip() or "unknown"
            _, socket_running = _parse_state(socket_status)
            _, service_running = _parse_state(service_status)
            service_note = ""
            if socket_running and not service_running:
                service_note = "\nService is socket-activated and will start on first connection."
            _message(
                "Web UI Status",
                f"Socket ({socket_name}): {socket_status}\nService ({service_name}): {service_status}{service_note}",
            )
            continue

        if choice == "2":
            output = _install_units()
            _message("Web UI Install", output)
            continue

        if choice == "3":
            _ensure_installed()
            results = [
                system_service_action(socket_name, "start"),
            ]
            output = "\n".join(result.stdout.strip() for result in results if result.stdout.strip())
            _message("Web UI Start", output or "Web UI socket started. Service will launch on first connection.")
            continue

        if choice == "4":
            _ensure_installed()
            results = [
                system_service_action(service_name, "stop"),
                system_service_action(socket_name, "stop"),
            ]
            output = "\n".join(result.stdout.strip() for result in results if result.stdout.strip())
            _message("Web UI Stop", output or "Web UI stopped.")
            continue

        if choice == "5":
            _ensure_installed()
            results = [
                system_service_action(socket_name, "restart"),
                system_service_action(service_name, "stop"),
            ]
            output = "\n".join(result.stdout.strip() for result in results if result.stdout.strip())
            _message("Web UI Restart", output or "Web UI socket restarted.")
            continue

        if choice == "6":
            _ensure_installed()
            results = [
                system_service_action(socket_name, "enable"),
                system_service_action(service_name, "enable"),
            ]
            output = "\n".join(result.stdout.strip() for result in results if result.stdout.strip())
            _message("Web UI Enable", output or "Web UI enabled.")
            continue

        if choice == "7":
            _ensure_installed()
            results = [
                system_service_action(service_name, "disable"),
                system_service_action(socket_name, "disable"),
                system_service_action(service_name, "stop"),
                system_service_action(socket_name, "stop"),
            ]
            output = "\n".join(result.stdout.strip() for result in results if result.stdout.strip())
            _message("Web UI Disable", output or "Web UI disabled.")


def _wifi_mesh_menu() -> None:
    def _service_exists(name: str) -> bool:
        for base in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
            if Path(base, f"{name}.service").exists():
                return True
        return False

    def _select_service(candidates: list[str]) -> str:
        for candidate in candidates:
            if _service_exists(candidate):
                return candidate
        return candidates[0]

    service = _select_service(["femto-wifi-mesh", "femto-wifi-mesh-control"])
    while True:
        choice = _menu(
            "Wi-Fi Mesh Sync",
            [
                ("1", "Run sync now"),
                ("2", "Service status"),
                ("3", "Start service"),
                ("4", "Stop service"),
                ("5", "Restart service"),
                ("6", "Enable service"),
                ("7", "Disable service"),
                ("8", "Back"),
            ],
        )
        if choice in (None, "8"):
            return
        if choice == "1":
            result = wifi_mesh_sync()
            _message("Wi-Fi Mesh Sync", result.stdout or "Sync complete.")
            continue
        action_map = {
            "2": "status",
            "3": "start",
            "4": "stop",
            "5": "restart",
            "6": "enable",
            "7": "disable",
        }
        action = action_map.get(choice)
        if action:
            _run_cli_output(["services", service, action], f"{service} {action}")


def _watchclock_menu() -> None:
    if _yesno("Watchclock", "Run watchclock loop now?"):
        result = run_watchclock()
        _message("Watchclock", result.stdout)


def _install_wizard() -> None:
    if not _yesno(
        "Install Wizard",
        "The install wizard will allow you to configure all the settings necessary to run your Femtofox.\n\n"
        "The wizard takes several minutes to complete and will overwrite some current settings.\n\nProceed?",
    ):
        return
    _time_menu()
    hostname = _inputbox("Hostname", "Enter hostname:", os.uname().nodename)
    if hostname:
        _run_cli(["networking", "hostname", "set", "--name", hostname])
        _run_cli(["networking", "apply"])
        _message("Hostname", f"Femtofox is now reachable at\n{hostname}.local")
    if _yesno("Install Wizard", "Configure Wi-Fi settings?"):
        ssid, psk, country = _wifi_form()
        if ssid:
            args = ["networking", "wifi", "set", "--ssid", ssid, "--psk", psk]
            if country:
                args.extend(["--country", country])
            _run_cli(args)
            _run_cli(["networking", "apply"])
            _message("Wi-Fi", "Wi-Fi settings saved.")
    if _yesno("Install Wizard", "Configure Meshtastic?"):
        while True:
            choice = _menu(
                "Meshtastic Configuration",
                [
                    ("1", "Set LoRa radio model"),
                    ("2", "Set configuration URL"),
                    ("3", "Set private key"),
                    ("4", "Set public key"),
                    ("5", "Full Meshtastic settings"),
                    ("6", "Continue"),
                ],
            )
            if choice in (None, "6"):
                break
            if choice == "1":
                model = _inputbox("LoRa radio", "Enter radio model (or 'none'):", "none")
                if model:
                    _run_cli(["meshtastic", "set-radio", "--model", model])
            elif choice == "2":
                url = _inputbox("Config URL", "Enter config URL:")
                if url:
                    _run_cli(["meshtastic", "set-config-url", "--url", url])
            elif choice == "3":
                key = _inputbox("Private Key", "Enter private key:")
                if key:
                    _run_cli(["meshtastic", "set-private-key", "--key", key])
            elif choice == "4":
                key = _inputbox("Public Key", "Enter public key:")
                if key:
                    _run_cli(["meshtastic", "set-public-key", "--key", key])
            elif choice == "5":
                _meshtastic_full_settings_menu()
    _message("Install Wizard", "Setup wizard complete!")


def main(wizard: bool = False) -> int:
    if wizard:
        _install_wizard()
        return 0
    while True:
        choice = _menu(
            "mpwrd-config",
            [
                ("1", "Meshtastic"),
                ("2", "Networking"),
                ("3", "Time & Timezone"),
                ("4", "Software Manager"),
                ("5", "System Utilities"),
                ("6", "System Actions"),
                ("7", "Kernel Modules"),
                ("8", "Install Wizard"),
                ("9", "Help / About"),
                ("10", "Exit"),
            ],
        )
        if choice in (None, "10"):
            return 0
        if choice == "1":
            _meshtastic_menu()
        elif choice == "2":
            _networking_menu()
        elif choice == "3":
            _time_menu()
        elif choice == "4":
            _software_menu()
        elif choice == "5":
            _utilities_menu()
        elif choice == "6":
            _system_menu()
        elif choice == "7":
            _kernel_menu()
        elif choice == "8":
            _install_wizard()
        elif choice == "9":
            _help_menu()


if __name__ == "__main__":
    raise SystemExit(main())
