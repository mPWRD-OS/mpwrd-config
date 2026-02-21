from __future__ import annotations

import asyncio
import atexit
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar

from InquirerPy import get_style, inquirer
from prompt_toolkit.application import Application
from prompt_toolkit.filters import has_focus
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.bindings.focus import focus_next, focus_previous
from prompt_toolkit.key_binding.defaults import load_key_bindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Button, Dialog, RadioList, TextArea

DEFAULT_KEY_BINDINGS = load_key_bindings()


class _QuickExit(Exception):
    """Raised to terminate the TUI quickly from global Ctrl-C."""


def _quick_exit(event=None) -> None:
    if event and getattr(event, "app", None):
        event.app.exit(exception=_QuickExit())
        return
    raise _QuickExit()


GLOBAL_KEY_BINDINGS = KeyBindings()
GLOBAL_KEY_BINDINGS.add("c-c")(_quick_exit)


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
        "frame": "bold #39ff14",
        "frame.label": "bold #ffffff",
        "fuzzy_border": "#39ff14",
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
        "fuzzy_border": "#39ff14",
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
        "dialog frame.border": "#39ff14",
        "dialog frame.label": "bold #ffffff",
        "text-area": "bg:#000000 #22c55e",
        "button": "bg:#0b0f0d #22c55e",
        "button.focused": "bg:#22c55e #000000",
        "scrollbar.background": "#111827",
        "scrollbar.button": "bg:#22c55e",
        "scrollbar.arrow": "#22c55e",
    }
)

MENU_STYLE = Style.from_dict(
    {
        "": "bg:#000000 #e5e7eb",
        "dialog": "bg:#000000 #e5e7eb",
        "dialog.body": "bg:#000000 #e5e7eb",
        "dialog frame.border": "#39ff14",
        "dialog frame.label": "bold #ffffff",
        "radio-list": "bg:#000000 #e5e7eb",
        "radio": "#e5e7eb",
        "radio-selected": "bold #22c55e",
        "radio-checked": "bold #22c55e",
        "radio-number": "#9ca3af",
        "scrollbar.background": "#111827",
        "scrollbar.button": "bg:#22c55e",
        "scrollbar.arrow": "#22c55e",
        "button": "bg:#111827 #e5e7eb",
        "button.focused": "bg:#22c55e #000000",
    }
)

MAIN_MENU_TITLE = FormattedText(
    [
        ("fg:#39ff14 bold", "m"),
        ("fg:#ffffff bold", "PWRD-config"),
    ]
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
    MeshtasticSession,
    add_admin_key,
    channel_add,
    channel_add_url,
    channel_delete,
    channel_disable,
    channel_enable,
    channel_set,
    channel_set_url,
    clear_admin_keys,
    config_qr,
    current_radio,
    get_legacy_admin_state,
    get_private_key,
    get_preference,
    get_public_key,
    i2c_state,
    list_admin_keys,
    list_preference_fields,
    lora_settings,
    mac_address_source,
    mac_address_source_options,
    meshtastic_config,
    meshtastic_info,
    meshtastic_repo_status,
    meshtastic_summary,
    meshtastic_update,
    mesh_test,
    service_action as meshtastic_service_action,
    service_enable as meshtastic_service_enable,
    service_status as meshtastic_service_status,
    set_config_url,
    set_legacy_admin_state,
    set_mac_address_source,
    set_meshtastic_repo,
    set_lora_settings,
    set_private_key,
    set_preference,
    set_public_key,
    set_radio,
    uninstall as meshtastic_uninstall,
    upgrade as meshtastic_upgrade,
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
    storage_info,
    ttyd_action,
)
from mpwrd_config.system import list_wifi_interfaces, list_ethernet_interfaces
from mpwrd_config.time_config import current_timezone, set_time, set_timezone, status as time_status
from mpwrd_config.wifi_mesh import sync_once as wifi_mesh_sync

T = TypeVar("T")


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
        layout=Layout(dialog, focused_element=text_area),
        key_bindings=merge_key_bindings([GLOBAL_KEY_BINDINGS, load_key_bindings(), kb]),
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


def _print_exiting_notice() -> None:
    _clear_screen()
    try:
        sys.stdout.write("Exiting...\n")
        sys.stdout.flush()
    except Exception:
        pass


def _print_starting_notice() -> None:
    _clear_screen()
    try:
        sys.stdout.write("Starting...\n")
        sys.stdout.flush()
    except Exception:
        pass


def _run_interactive(command: list[str], title: str, missing: str) -> None:
    _clear_screen()
    try:
        subprocess.run(command, check=False)
    except FileNotFoundError:
        _message(title, missing)
    finally:
        _clear_screen()


def _run_with_status(title: str, body: str, action: Callable[[], T]) -> T:
    _clear_screen()
    result: dict[str, T] = {}
    errors: dict[str, BaseException] = {}
    text = body.strip() or "Working..."
    text_area = TextArea(
        text=text,
        read_only=True,
        scrollbar=False,
        wrap_lines=True,
        focusable=False,
    )
    dialog = Dialog(title=title, body=text_area, buttons=[], with_background=True)
    kb = KeyBindings()

    def _ignore(event=None) -> None:
        return

    kb.add("escape")(_ignore)
    kb.add("q")(_ignore)
    kb.add("enter")(_ignore)
    kb.add(" ")(_ignore)
    kb.add("left")(_ignore)
    kb.add("right")(_ignore)

    app = Application(
        layout=Layout(dialog),
        key_bindings=merge_key_bindings([GLOBAL_KEY_BINDINGS, DEFAULT_KEY_BINDINGS, kb]),
        mouse_support=False,
        style=DIALOG_STYLE,
        full_screen=True,
    )

    async def _run_action() -> None:
        loop = asyncio.get_running_loop()
        try:
            result["value"] = await loop.run_in_executor(None, action)
        except BaseException as exc:
            errors["error"] = exc
        finally:
            app.exit()

    try:
        app.run(pre_run=lambda: app.create_background_task(_run_action()))
    finally:
        _clear_screen()
    if "error" in errors:
        raise errors["error"]
    return result["value"]


def _run_with_status_message(
    title: str,
    action: Callable[[], object],
    *,
    empty: str = "Done.",
    status: str = "Working...",
) -> object:
    result = _run_with_status(title, status, action)
    if hasattr(result, "stdout"):
        output = str(getattr(result, "stdout", "") or "").strip()
    else:
        output = str(result or "").strip()
    _message(title, output or empty)
    return result


def _extract_meshtastic_result(payload: object) -> Any | None:
    if hasattr(payload, "returncode") and hasattr(payload, "stdout"):
        return payload
    if isinstance(payload, tuple) and payload:
        first = payload[0]
        if hasattr(first, "returncode") and hasattr(first, "stdout"):
            return first
    return None


def _is_meshtastic_connection_error(result: Any) -> bool:
    if result is None:
        return False
    code = getattr(result, "returncode", None)
    text = str(getattr(result, "stdout", "") or "").lower()
    if code == 124:
        return True
    markers = (
        "meshtastic command timed out",
        "unable to connect to meshtastic",
        "meshtastic connect failed",
        "timed out waiting for connection completion",
        "timed out waiting for interface config",
        "meshinterfaceerror",
    )
    return any(marker in text for marker in markers)


def _meshtastic_connection_dialog(message: str) -> str:
    _clear_screen()
    text = message.strip() or "Unable to connect to Meshtastic."
    text_area = TextArea(
        text=text,
        read_only=True,
        scrollbar=True,
        wrap_lines=True,
        focusable=True,
    )
    choice = {"value": "ok"}
    app: Application | None = None

    def _set(value: str) -> None:
        choice["value"] = value
        if app:
            app.exit()

    def _pick_reconnect(event=None) -> None:
        _set("reconnect")

    def _pick_ok(event=None) -> None:
        _set("ok")

    reconnect_button = Button(text="Reconnect", handler=_pick_reconnect)
    ok_button = Button(text="OK", handler=_pick_ok)
    dialog = Dialog(
        title="Meshtastic Connection",
        body=text_area,
        buttons=[reconnect_button, ok_button],
        with_background=True,
    )
    kb = KeyBindings()
    kb.add("tab")(focus_next)
    kb.add("s-tab")(focus_previous)
    kb.add("left")(focus_previous)
    kb.add("right")(focus_next)
    kb.add("escape")(_pick_ok)
    kb.add("q")(_pick_ok)
    kb.add("r")(_pick_reconnect)
    app = Application(
        layout=Layout(dialog, focused_element=reconnect_button),
        key_bindings=merge_key_bindings([GLOBAL_KEY_BINDINGS, DEFAULT_KEY_BINDINGS, kb]),
        mouse_support=False,
        style=DIALOG_STYLE,
        full_screen=True,
    )
    try:
        app.run()
    except (EOFError, KeyboardInterrupt):
        choice["value"] = "ok"
    finally:
        _clear_screen()
    return str(choice["value"])


def _recover_meshtastic_connection(session: MeshtasticSession, message: str) -> bool:
    prompt = message.strip() or "Unable to connect to Meshtastic."
    while True:
        if _meshtastic_connection_dialog(prompt) != "reconnect":
            return False
        error, interface = _run_with_status(
            "Meshtastic",
            "Reconnecting to Meshtastic API...\nPlease wait.",
            lambda: session.get_interface(wait_for_config=False, reconnect=True),
        )
        if not error and interface:
            return True
        prompt = error.stdout.strip() if error else "Unable to connect to Meshtastic."


def _run_meshtastic_with_reconnect(
    session: MeshtasticSession | None,
    title: str,
    action: Callable[[], T],
) -> T | None:
    while True:
        payload = _run_with_status(title, "Working...", action)
        result = _extract_meshtastic_result(payload)
        if _is_meshtastic_connection_error(result):
            message = str(getattr(result, "stdout", "") or "Unable to connect to Meshtastic.")
            if session and _recover_meshtastic_connection(session, message):
                continue
            if session is None:
                _message("Meshtastic", message.strip() or "Unable to connect to Meshtastic.")
            return None
        return payload


class _PersistentMenuDialog:
    def __init__(self) -> None:
        placeholder = "__placeholder__"
        self._result: str | None = None
        self._radio = RadioList(
            values=[(placeholder, "")],
            default=placeholder,
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
        self._radio.control.key_bindings.add("enter")(self._accept)
        self._radio.control.key_bindings.add(" ")(self._accept)
        self._radio.control.key_bindings.add("right")(self._accept)
        self._radio.control.key_bindings.add("left")(self._cancel)

        self._dialog = Dialog(title="", body=self._radio, buttons=[], with_background=True)
        kb = KeyBindings()
        kb.add("tab")(focus_next)
        kb.add("s-tab")(focus_previous)
        kb.add("escape")(self._cancel)
        kb.add("q")(self._cancel)
        kb.add("left")(self._cancel)
        kb.add("right")(self._accept)
        kb.add("enter")(self._accept)
        kb.add(" ")(self._accept)
        self._app = Application(
            layout=Layout(self._dialog, focused_element=self._radio),
            key_bindings=merge_key_bindings([GLOBAL_KEY_BINDINGS, DEFAULT_KEY_BINDINGS, kb]),
            mouse_support=False,
            style=MENU_STYLE,
            full_screen=True,
        )

    def _accept(self, event=None) -> None:
        self._result = self._radio.current_value
        self._app.exit()

    def _cancel(self, event=None) -> None:
        self._result = None
        self._app.exit()

    def show(self, title: str, values: list[tuple[str, str]], default: str | None = None) -> str | None:
        self._dialog.title = title
        self._radio.values = values
        keys = [value for value, _ in values]
        if default in keys:
            self._radio.current_value = default
            self._radio._selected_index = keys.index(default)
        else:
            self._radio.current_value = values[0][0]
            self._radio._selected_index = 0
        self._result = None
        self._app.layout.focus(self._radio)
        self._app.run()
        _clear_screen()
        return self._result


_MENU_DIALOG: _PersistentMenuDialog | None = None


def _menu_dialog() -> _PersistentMenuDialog:
    global _MENU_DIALOG
    if _MENU_DIALOG is None:
        _MENU_DIALOG = _PersistentMenuDialog()
    return _MENU_DIALOG


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
                raise_keyboard_interrupt=True,
                keybindings={
                    "answer": [{"key": "enter"}, {"key": "right"}, {"key": " "}],
                    "skip": [{"key": "escape"}, {"key": "left"}],
                },
            ).execute()
        values = [(item["value"], item["name"]) for item in choices]
        if not values:
            return None
        return _menu_dialog().show(title, values, default=default)
    except (KeyboardInterrupt, EOFError):
        raise _QuickExit()


def _yesno(title: str, body: str) -> bool:
    try:
        return bool(inquirer.confirm(message=f"{title}\n{body}", default=False, style=APP_STYLE).execute())
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
        return False


def _inputbox(title: str, body: str, default: str = "") -> str | None:
    try:
        value = inquirer.text(message=f"{title}\n{body}", default=default, style=APP_STYLE).execute()
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
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
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
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
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
        return None
    if not value:
        return None
    return str(value).strip()


def _cli_command(args: list[str]) -> list[str]:
    return [sys.executable, "-m", "mpwrd_config.cli", *args]


def _run_cli(args: list[str]) -> int:
    return _run_with_status(
        "mpwrd-config",
        "Working...",
        lambda: subprocess.call(_cli_command(args)),
    )


def _run_cli_output(args: list[str], title: str) -> int:
    result = _run_with_status(
        title,
        "Working...",
        lambda: subprocess.run(
            _cli_command(args),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        ),
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
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
        return None, None, None
    if not ssid:
        return None, None, None
    try:
        psk = inquirer.secret(message="Wi-Fi Password", style=APP_STYLE).execute()
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
        return None, None, None
    try:
        country = inquirer.text(message="Country Code (optional)", default=country_default, style=APP_STYLE).execute()
    except KeyboardInterrupt:
        raise _QuickExit()
    except EOFError:
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
                "Identity",
                [
                    ("1", "Set hostname"),
                    ("2", "Back"),
                ],
            )
            if action in (None, "2"):
                return
            if action == "1":
                hostname = _inputbox("Hostname", "Enter new hostname:", os.uname().nodename)
                if hostname:
                    _run_cli(["networking", "hostname", "set", "--name", hostname])
                    _run_cli(["networking", "apply"])
                    _message("Hostname", f"mpwrd-config is now reachable at\n{hostname}.local")

    def _interfaces_menu() -> None:
        while True:
            action = _menu(
                "Interfaces",
                [
                    ("1", "Select Wi-Fi interface"),
                    ("2", "Select ethernet interface"),
                    ("3", "Back"),
                ],
            )
            if action in (None, "3"):
                return
            if action == "1":
                _select_interface("wifi", list_wifi_interfaces())
            elif action == "2":
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
                ("1", "Identity"),
                ("2", "Interfaces"),
                ("3", "Wi-Fi Settings"),
                ("4", "Diagnostics"),
                ("5", "Back"),
            ],
        )
        if choice in (None, "5"):
            return
        if choice == "1":
            _identity_menu()
        elif choice == "2":
            _interfaces_menu()
        elif choice == "3":
            _wifi_settings_menu()
        elif choice == "4":
            _diagnostics_menu()


def _meshtastic_full_settings_menu(
    session: MeshtasticSession | None = None,
    section: str = "all",
) -> None:
    def _show(title: str, action: Callable[[], object]) -> None:
        result = _run_meshtastic_with_reconnect(session, title, action)
        if result is None:
            return
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
            if section == "all":
                choice = _menu(
                    "Meshtastic Settings",
                    [
                        ("1", "Preferences"),
                        ("2", "Channels"),
                        ("3", "Back"),
                    ],
                )
                if choice in (None, "3"):
                    return
                if choice == "1":
                    _meshtastic_full_settings_menu(session=session, section="preferences")
                elif choice == "2":
                    _meshtastic_full_settings_menu(session=session, section="channels")
                continue

            if section == "preferences":
                choice = _menu(
                    "Meshtastic Preferences",
                    [
                        ("1", "Show preferences + modules"),
                        ("2", "List preference fields"),
                        ("3", "Get preference value"),
                        ("4", "Set preference value"),
                        ("5", "Back"),
                    ],
                )
                if choice in (None, "5"):
                    return
                if choice == "1":
                    _show("Meshtastic settings", lambda: meshtastic_config("settings", session=session))
                elif choice == "2":
                    _show("Preference fields", list_preference_fields)
                elif choice == "3":
                    field = _inputbox("Get preference", "Enter preference field (e.g. power.ls_secs):")
                    if field:
                        _show("Preference value", lambda: get_preference(field, session=session))
                elif choice == "4":
                    field = _inputbox("Set preference", "Enter preference field (e.g. power.ls_secs):")
                    if field:
                        value = _inputbox("Set preference", "Enter value:")
                        if value is not None:
                            _show("Set preference", lambda: set_preference(field, value, session=session))
                continue

            choice = _menu(
                "Meshtastic Channels",
                [
                    ("1", "Show channels"),
                    ("2", "Set channel field"),
                    ("3", "Add channel"),
                    ("4", "Delete channel"),
                    ("5", "Enable channel"),
                    ("6", "Disable channel"),
                    ("7", "Set channels from URL"),
                    ("8", "Add channels from URL"),
                    ("9", "Back"),
                ],
            )
            if choice in (None, "9"):
                return
            if choice == "1":
                _show("Meshtastic channels", lambda: meshtastic_config("channels", session=session))
            elif choice == "2":
                index = _prompt_index()
                if index is None:
                    continue
                field = _inputbox("Set channel field", "Enter channel field (e.g. name, psk, uplink):")
                if field:
                    value = _inputbox("Set channel field", "Enter value:")
                    if value is not None:
                        _show("Set channel", lambda: channel_set(index, field, value, session=session))
            elif choice == "3":
                name = _inputbox("Add channel", "Enter channel name:")
                if name:
                    _show("Add channel", lambda: channel_add(name, session=session))
            elif choice == "4":
                index = _prompt_index()
                if index is None:
                    continue
                if _yesno("Delete channel", f"Delete channel {index}?"):
                    _show("Delete channel", lambda: channel_delete(index, session=session))
            elif choice == "5":
                index = _prompt_index()
                if index is None:
                    continue
                _show("Enable channel", lambda: channel_enable(index, session=session))
            elif choice == "6":
                index = _prompt_index()
                if index is None:
                    continue
                _show("Disable channel", lambda: channel_disable(index, session=session))
            elif choice == "7":
                url = _inputbox("Set channels from URL", "Enter configuration URL:")
                if url:
                    if _yesno(
                        "Set channels from URL",
                        "This will overwrite LoRa settings and channels.\n\nProceed?",
                    ):
                        _show("Set channels from URL", lambda: channel_set_url(url, session=session))
            elif choice == "8":
                url = _inputbox("Add channels from URL", "Enter configuration URL:")
                if url:
                    _show("Add channels from URL", lambda: channel_add_url(url, session=session))
    finally:
        manage_full_control_conflicts("start")


def _meshtastic_repo_menu() -> None:
    def _show_repo(
        title: str,
        action: Callable[[], object],
        empty: str = "No output.",
        stream: bool = False,
    ) -> None:
        result = _run_with_status(title, "Working...", action)
        if stream and not result.stdout.strip():
            if result.returncode == 0:
                _message(title, "Command completed.")
            else:
                _message(title, f"Command failed (exit {result.returncode}).")
            return
        _message(title, result.stdout.strip() or empty)

    while True:
        choice = _menu(
            "Meshtastic Repo",
            [
                ("1", "Show current repo"),
                ("2", "Upgrade meshtasticd"),
                ("3", "Install/Update repo (choose channel)"),
                ("4", "Use beta repo (install/update)"),
                ("5", "Use alpha repo (install/update)"),
                ("6", "Use daily repo (install/update)"),
                ("7", "Uninstall meshtasticd"),
                ("8", "Back"),
            ],
        )
        if choice in (None, "8"):
            return
        if choice == "1":
            _show_repo("Meshtastic Repo", meshtastic_repo_status)
        elif choice == "2":
            if _yesno("Upgrade", "Upgrade meshtasticd now?"):
                _show_repo("Upgrade", lambda: meshtastic_upgrade(stream=True), "Done.", stream=True)
        elif choice == "3":
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
            _show_repo("Meshtastic Repo", lambda: set_meshtastic_repo(channel, stream=True), stream=True)
        elif choice == "4":
            _show_repo("Meshtastic Repo", lambda: set_meshtastic_repo("beta", stream=True), stream=True)
        elif choice == "5":
            _show_repo("Meshtastic Repo", lambda: set_meshtastic_repo("alpha", stream=True), stream=True)
        elif choice == "6":
            _show_repo("Meshtastic Repo", lambda: set_meshtastic_repo("daily", stream=True), stream=True)
        elif choice == "7":
            if _yesno("Uninstall", "Uninstall meshtasticd?"):
                _show_repo("Uninstall", lambda: meshtastic_uninstall(stream=True), "Done.", stream=True)


def _meshtastic_menu(session: MeshtasticSession) -> None:
    def _run_meshtastic(action: Callable[[], object]) -> object | None:
        return _run_meshtastic_with_reconnect(session, "Meshtastic", action)

    def _show_result(title: str, action: Callable[[], object], empty: str = "No output.") -> None:
        result = _run_meshtastic(action)
        if result is None:
            return
        _message(title, result.stdout.strip() or empty)

    def _meshtasticd_service_menu() -> None:
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
            if action == "1":
                _show_result("Meshtastic service status", meshtastic_service_status)
            elif action == "2":
                _show_result("Meshtastic service start", lambda: meshtastic_service_action("start"), "Done.")
            elif action == "3":
                _show_result("Meshtastic service stop", lambda: meshtastic_service_action("stop"), "Done.")
            elif action == "4":
                _show_result("Meshtastic service restart", lambda: meshtastic_service_action("restart"), "Done.")
            elif action == "5":
                _show_result("Meshtastic service enable", lambda: meshtastic_service_enable(True), "Done.")
            elif action == "6":
                _show_result("Meshtastic service disable", lambda: meshtastic_service_enable(False), "Done.")
            elif action == "7":
                _mac_source_menu()

    def _avahi_service_menu() -> None:
        while True:
            action = _menu(
                "Avahi service",
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
                _run_cli_output(["services", "avahi-daemon", action_name], f"avahi-daemon {action_name}")

    def _meshtastic_services_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic services",
                [
                    ("1", "meshtasticd service"),
                    ("2", "avahi"),
                    ("3", "Admin mesh client Wi-Fi toggle"),
                    ("4", "Back"),
                ],
            )
            if action in (None, "4"):
                return
            if action == "1":
                _meshtasticd_service_menu()
            elif action == "2":
                _avahi_service_menu()
            elif action == "3":
                _wifi_mesh_menu()

    def _mac_source_menu() -> None:
        current = _run_with_status(
            "MAC Address Source",
            "Working...",
            lambda: mac_address_source().stdout.strip(),
        )
        options = mac_address_source_options()
        option_keys = {value for value, _ in options}
        if current and current not in option_keys:
            options.insert(0, (current, f"Current ({current})"))
        options.append(("back", "Back"))
        choice = _menu("MAC Address Source", options, default=current if current in option_keys else None)
        if not choice or choice == "back":
            return
        _show_result("MAC Address Source", lambda: set_mac_address_source(choice), "Done.")

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
                _show_result("Public key", lambda: get_public_key(session=session))
            elif action == "2":
                key = _inputbox("Public key", "Enter base64 public key:")
                if key:
                    _show_result("Public key", lambda: set_public_key(key, session=session), "Done.")
            elif action == "3":
                _show_result("Private key", lambda: get_private_key(session=session))
            elif action == "4":
                key = _inputbox("Private key", "Enter base64 private key:")
                if key:
                    _show_result("Private key", lambda: set_private_key(key, session=session), "Done.")
            elif action == "5":
                _show_result("Admin keys", lambda: list_admin_keys(session=session))
            elif action == "6":
                key = _inputbox("Admin key", "Enter base64 admin key:")
                if key:
                    _show_result("Admin key", lambda: add_admin_key(key, session=session), "Done.")
            elif action == "7":
                if _yesno("Admin keys", "Clear all admin keys?"):
                    _show_result("Admin keys", lambda: clear_admin_keys(session=session), "Done.")
            elif action == "8":
                _show_result("Legacy admin", lambda: get_legacy_admin_state(session=session))
            elif action == "9":
                state = _menu("Legacy admin", [("true", "Enable"), ("false", "Disable"), ("back", "Back")])
                if state and state != "back":
                    _show_result("Legacy admin", lambda: set_legacy_admin_state(state == "true", session=session), "Done.")

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
                    _show_result(
                        "Meshtastic update",
                        lambda: meshtastic_update(command, attempts=1, label="Custom"),
                        "Done.",
                    )

    def _overview_menu() -> None:
        while True:
            action = _menu(
                "Meshtastic Overview",
                [
                    ("1", "Show node summary"),
                    ("2", "Show node info"),
                    ("3", "Back"),
                ],
            )
            if action in (None, "3"):
                return
            if action == "1":
                _show_result("Meshtastic summary", lambda: meshtastic_summary(session=session))
            elif action == "2":
                _show_result("Meshtastic info", lambda: meshtastic_info(session=session))

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
                _show_result("Config URL", lambda: config_qr(session=session))
            elif action == "2":
                url = _inputbox("Config URL", "Enter config URL:")
                if url and _yesno(
                    "Config URL",
                    "This will overwrite LoRa settings and channels.\n\nProceed?",
                ):
                    _show_result("Config URL", lambda: set_config_url(url, session=session), "Done.")

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
                payload = _run_meshtastic(lambda: lora_settings(session=session))
                if payload is None:
                    continue
                result, settings = payload
                if result.returncode != 0:
                    _message("LoRa settings", result.stdout.strip() or "Unable to query Meshtastic.")
                else:
                    body = "\n".join(f"{key}:{value}" for key, value in settings.items())
                    _message("LoRa settings", body or "No output.")
            elif action == "2":
                _show_result("Mesh test", lambda: mesh_test(session=session))

    while True:
        choice = _menu(
            "Meshtastic",
            [
                ("1", "Meshtastic overview"),
                ("2", "URL"),
                ("3", "Channels"),
                ("4", "LoRa configuration"),
                ("5", "Preferences"),
                ("6", "Keys & admin"),
                ("7", "Meshtastic services"),
                ("8", "Meshtastic repository"),
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
            _meshtastic_full_settings_menu(session=session, section="channels")
        elif choice == "4":
            _meshtastic_lora_menu(session=session)
        elif choice == "5":
            _meshtastic_full_settings_menu(session=session, section="preferences")
        elif choice == "6":
            _keys_menu()
        elif choice == "7":
            _meshtastic_services_menu()
        elif choice == "8":
            _meshtastic_repo_menu()
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


def _meshtastic_lora_menu(session: MeshtasticSession | None = None) -> None:
    def _run_lora(action: Callable[[], object]) -> object | None:
        return _run_meshtastic_with_reconnect(session, "LoRa settings", action)

    current: dict[str, Any] = {}
    initial = _run_lora(lambda: lora_settings(session=session))
    connected = False
    if initial is not None:
        result, discovered = initial
        if result.returncode == 0:
            current = discovered
            connected = True

    def apply(settings: dict[str, str], title: str) -> None:
        nonlocal current
        if not settings:
            return
        response = _run_lora(lambda: set_lora_settings(settings, session=session))
        if response is None:
            return
        _message(title, response.stdout.strip() or "Done.")
        if response.returncode == 0:
            refreshed_payload = _run_lora(lambda: lora_settings(session=session))
            if refreshed_payload is not None:
                refreshed, updated = refreshed_payload
                if refreshed.returncode == 0:
                    current = updated

    def select_radio() -> None:
        current_model = _run_with_status(
            "LoRa radio",
            "Working...",
            lambda: current_radio().stdout.strip(),
        )
        options = [
            ("lr1121_tcxo", "LR1121 TCXO"),
            ("sx1262_tcxo", "SX1262 TCXO (Ebyte e22-900m30s / Heltec ht-ra62 / Seeed wio-sx1262)"),
            ("sx1262_xtal", "SX1262 XTAL (Ebyte e80-900m22s / Waveshare / AI Thinker ra-01sh)"),
            ("lora-meshstick-1262", "LoRa Meshstick 1262 (USB)"),
            ("sim", "Simulated radio (software)"),
            ("none", "Auto-detect (no forced profile)"),
        ]
        model = _menu(
            "LoRa radio",
            options,
            default=current_model if current_model in {opt[0] for opt in options} else None,
        )
        if model:
            response = _run_lora(lambda: set_radio(model))
            if response is None:
                return
            _message("LoRa radio", response.stdout.strip() or "Done.")

    if not connected:
        _message(
            "LoRa settings",
            "Meshtastic API is not connected.\n\n"
            "You can still set the LoRa radio model from this menu.\n"
            "Other LoRa settings require a live Meshtastic connection.",
        )
        while True:
            choice = _menu(
                "LoRa settings",
                [
                    ("1", "Set LoRa radio model"),
                    ("2", "Back"),
                ],
            )
            if choice in (None, "2"):
                return
            if choice == "1":
                select_radio()
        return

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
        response = _run_lora(lambda: set_config_url(url, session=session))
        if response is None:
            return False
        _message("Meshtastic URL", response.stdout.strip() or "URL updated.")
        refreshed_payload = _run_lora(lambda: lora_settings(session=session))
        if refreshed_payload is not None:
            refreshed, updated = refreshed_payload
            if refreshed.returncode == 0:
                current = updated
        return True

    while True:
        choice = _menu(
            "LoRa settings",
            [
                ("1", "Wizard (set all)"),
                ("2", "Set LoRa radio model"),
                ("3", "Configure with URL"),
                ("4", "Region"),
                ("5", "Use modem preset"),
                ("6", "Preset"),
                ("7", "Bandwidth"),
                ("8", "Spread factor"),
                ("9", "Coding rate"),
                ("10", "Frequency offset"),
                ("11", "Hop limit"),
                ("12", "Enable/disable TX"),
                ("13", "TX power"),
                ("14", "Frequency slot"),
                ("15", "Override duty cycle"),
                ("16", "SX126X RX boosted gain"),
                ("17", "Override frequency"),
                ("18", "Ignore MQTT"),
                ("19", "OK to MQTT"),
                ("20", "Show current LoRa settings"),
                ("21", "Show config URL + QR"),
                ("22", "Back"),
            ],
        )
        if choice in (None, "22"):
            return
        if choice == "1":
            current_radio_value = _run_with_status(
                "LoRa wizard",
                "Working...",
                lambda: current_radio().stdout.strip(),
            )
            if _yesno(
                "LoRa wizard",
                f"Current radio: {current_radio_value}\n\nSet radio model?",
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
                refreshed_payload = _run_lora(lambda: lora_settings(session=session))
                if refreshed_payload is not None:
                    refreshed, updated = refreshed_payload
                    if refreshed.returncode == 0:
                        current = updated
                continue
            _meshtastic_lora_wizard(current, session=session)
        elif choice == "2":
            select_radio()
        elif choice == "3":
            config_url_prompt()
        elif choice == "4":
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
        elif choice == "5":
            value = _bool_prompt("Use modem preset", "Use preset?", str(current.get("lora_usePreset")))
            if value is not None:
                apply({"use_preset": value}, "Use modem preset")
        elif choice == "6":
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
        elif choice == "7":
            bandwidth = _menu(
                "Bandwidth",
                [(value, value) for value in ["0", "31", "62", "125", "250", "500"]],
                default=str(current.get("lora_bandwidth")),
            )
            if bandwidth:
                apply({"bandwidth": bandwidth}, "Bandwidth")
        elif choice == "8":
            spread = _menu(
                "Spread factor",
                [(value, value) for value in ["0", "7", "8", "9", "10", "11", "12"]],
                default=str(current.get("lora_spreadFactor")),
            )
            if spread:
                apply({"spread_factor": spread}, "Spread factor")
        elif choice == "9":
            coding = _menu(
                "Coding rate",
                [(value, value) for value in ["0", "5", "6", "7", "8"]],
                default=str(current.get("lora_codingRate")),
            )
            if coding:
                apply({"coding_rate": coding}, "Coding rate")
        elif choice == "10":
            value = _input_with_validation(
                "Frequency offset",
                "Frequency offset (0-1000000):",
                str(current.get("lora_frequencyOffset") or "0"),
                lambda v: re.fullmatch(r"[0-9]{1,7}(\\.[0-9]+)?", v) is not None and float(v) <= 1000000,
                "Must be between 0 and 1000000.",
            )
            if value:
                apply({"frequency_offset": value}, "Frequency offset")
        elif choice == "11":
            value = _input_with_validation(
                "Hop limit",
                "Hop limit (0-7):",
                str(current.get("lora_hopLimit") or "3"),
                lambda v: v.isdigit() and 0 <= int(v) <= 7,
                "Must be an integer between 0 and 7.",
            )
            if value:
                apply({"hop_limit": value}, "Hop limit")
        elif choice == "12":
            value = _bool_prompt("TX enabled", "Enable TX?", str(current.get("lora_txEnabled")))
            if value is not None:
                apply({"tx_enabled": value}, "TX enabled")
        elif choice == "13":
            value = _input_with_validation(
                "TX power",
                "TX power (0-30):",
                str(current.get("lora_txPower") or "0"),
                lambda v: v.isdigit() and 0 <= int(v) <= 30,
                "Must be an integer between 0 and 30.",
            )
            if value:
                apply({"tx_power": value}, "TX power")
        elif choice == "14":
            value = _input_with_validation(
                "Frequency slot",
                "Frequency slot (0+):",
                str(current.get("lora_channelNum") or "0"),
                lambda v: v.isdigit() and int(v) >= 0,
                "Must be an integer 0 or higher.",
            )
            if value:
                apply({"channel_num": value}, "Frequency slot")
        elif choice == "15":
            value = _bool_prompt("Override duty cycle", "Override duty cycle?", str(current.get("lora_overrideDutyCycle")))
            if value is not None:
                apply({"override_duty_cycle": value}, "Override duty cycle")
        elif choice == "16":
            value = _bool_prompt(
                "SX126X RX boosted gain",
                "Enable SX126X RX boosted gain?",
                str(current.get("lora_sx126xRxBoostedGain")),
            )
            if value is not None:
                apply({"sx126x_rx_boosted_gain": value}, "SX126X RX boosted gain")
        elif choice == "17":
            value = _input_with_validation(
                "Override frequency",
                "Override frequency (MHz, 0+):",
                str(current.get("lora_overrideFrequency") or "0"),
                lambda v: re.fullmatch(r"[0-9]+(\\.[0-9]+)?", v) is not None,
                "Must be a number 0 or higher.",
            )
            if value:
                apply({"override_frequency": value}, "Override frequency")
        elif choice == "18":
            value = _bool_prompt("Ignore MQTT", "Ignore MQTT?", str(current.get("lora_ignoreMqtt")))
            if value is not None:
                apply({"ignore_mqtt": value}, "Ignore MQTT")
        elif choice == "19":
            value = _bool_prompt("OK to MQTT", "OK to MQTT?", str(current.get("lora_configOkToMqtt")))
            if value is not None:
                apply({"config_ok_to_mqtt": value}, "OK to MQTT")
        elif choice == "20":
            payload = _run_lora(lambda: lora_settings(session=session))
            if payload is None:
                continue
            result, settings = payload
            if result.returncode != 0:
                _message("LoRa settings", result.stdout.strip() or "Unable to query Meshtastic.")
            else:
                body = "\n".join(f"{key}:{value}" for key, value in settings.items())
                _message("LoRa settings", body or "No output.")
        elif choice == "21":
            result = _run_lora(lambda: config_qr(session=session))
            if result is None:
                continue
            _message("LoRa config URL", result.stdout.strip() or "No output.")


def _meshtastic_lora_wizard(
    current: dict[str, Any],
    session: MeshtasticSession | None = None,
) -> None:
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
        response = _run_meshtastic_with_reconnect(
            session,
            "LoRa wizard",
            lambda: set_lora_settings(settings, session=session),
        )
        if response is None:
            return
        _message("LoRa wizard", response.stdout.strip() or "Done.")


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
                ("4", "Watchclock service"),
                ("5", "Back"),
            ],
        )
        if choice in (None, "5"):
            return
        if choice == "1":
            _run_with_status_message("Time status", time_status)
        elif choice == "2":
            tz = _run_with_status("Timezone", "Working...", lambda: current_timezone().stdout.strip())
            timezones = _run_with_status(
                "Timezone",
                "Working...",
                lambda: subprocess.check_output(["timedatectl", "list-timezones"], text=True).splitlines(),
            )
            items = [(zone, "") for zone in timezones]
            selected = _menu("Set Time Zone", items, default=tz)
            if selected:
                _run_with_status_message("Timezone", lambda: set_timezone(selected), empty="Timezone updated.")
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
            _run_with_status_message("System time", lambda: set_time(timespec), empty="Time updated.")
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
        packages = _run_with_status("Software Manager", "Working...", list_packages)
        if not packages:
            _message("Software Manager", "No packages found.")
            return
        items = [(pkg.key, f"{pkg.name} ({'installed' if pkg.installed else 'not installed'})") for pkg in packages]
        items.append(("back", "Back"))
        choice = _menu("Software Manager", items)
        if choice in (None, "back"):
            return
        info = _run_with_status("Software Manager", "Working...", lambda: package_info(choice))
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
                result = _run_with_status(
                    extra_actions[action],
                    "Working...",
                    lambda: run_action(choice, f"-{action.split(':', 1)[1]}"),
                )
                _software_action_dialog(extra_actions[action], result)
                continue
            if action == "license":
                _run_with_status_message("License", lambda: license_text(choice), empty="No license text.")
                continue
            if action == "status":
                result = _run_with_status("Service status", "Working...", lambda: package_service_action(choice, "-S"))
                _message("Service status", result.stdout)
                continue
            if action in {"enable", "disable", "stop", "restart"}:
                flag_map = {
                    "enable": "-e",
                    "disable": "-d",
                    "stop": "-s",
                    "restart": "-r",
                }
                result = _run_with_status(
                    info.name,
                    "Working...",
                    lambda: package_service_action(choice, flag_map[action]),
                )
                _message(info.name, result.stdout or "Done.")
                continue
            if action == "install":
                result = _run_with_status("Install", "Working...", lambda: run_action(choice, "-i"))
                _software_action_dialog("Install", result)
            elif action == "uninstall":
                result = _run_with_status("Uninstall", "Working...", lambda: run_action(choice, "-u"))
                _software_action_dialog("Uninstall", result)
            elif action == "upgrade":
                result = _run_with_status("Upgrade", "Working...", lambda: run_action(choice, "-g"))
                _software_action_dialog("Upgrade", result)
            elif action == "init":
                result = _run_with_status("Initialize", "Working...", lambda: run_action(choice, "-a"))
                _software_action_dialog("Initialize", result)
            elif action == "run":
                result = _run_with_status("Run", "Working...", lambda: run_action(choice, "-l"))
                _software_action_dialog("Run", result)


def _meshtastic_i2c_menu() -> None:
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
            _run_with_status_message("I2C status", lambda: i2c_state("check"))
        elif action == "2":
            _run_with_status_message("I2C enable", lambda: i2c_state("enable"), empty="Done.")
        elif action == "3":
            _run_with_status_message("I2C disable", lambda: i2c_state("disable"), empty="Done.")


def _utilities_menu() -> None:
    while True:
        choice = _menu(
            "System Utilities",
            [
                ("1", "System info"),
                ("2", "Logging"),
                ("3", "Activity LED"),
                ("4", "Meshtastic I2C"),
                ("5", "ttyd service"),
                ("6", "Regenerate SSH keys"),
                ("7", "Process viewer/manager"),
                ("8", "Time & Timezone"),
                ("9", "Back"),
            ],
        )
        if choice in (None, "9"):
            return
        if choice == "1":
            section = _menu(
                "System Info",
                [
                    ("all", "All"),
                    ("cpu", "CPU"),
                    ("os", "OS"),
                    ("storage", "Storage"),
                    ("network", "Networking"),
                    ("peripherals", "Peripherals"),
                    ("back", "Back"),
                ],
            )
            if section in (None, "back"):
                continue
            if section == "all":
                _run_with_status_message("System Info", all_system_info)
            elif section == "cpu":
                _run_with_status_message("CPU Info", cpu_info)
            elif section == "os":
                _run_with_status_message("OS Info", os_info)
            elif section == "storage":
                _run_with_status_message("Storage Info", storage_info)
            elif section == "network":
                _run_with_status_message("Networking Info", networking_info)
            elif section == "peripherals":
                _run_with_status_message("Peripherals Info", peripherals_info)
        elif choice == "2":
            state = _menu(
                "Logging",
                [
                    ("enable", "Enable /var/log"),
                    ("disable", "Disable /var/log"),
                    ("check", "Check"),
                    ("back", "Back"),
                ],
            )
            if state and state != "back":
                _run_with_status_message("Logging", lambda: logging_state(state))
        elif choice == "3":
            state = _menu(
                "Activity LED",
                [("enable", "Enable"), ("disable", "Disable"), ("check", "Check"), ("back", "Back")],
            )
            if state and state != "back":
                _run_with_status_message("Activity LED", lambda: act_led(state))
        elif choice == "4":
            _meshtastic_i2c_menu()
        elif choice == "5":
            action = _menu(
                "ttyd",
                [("enable", "Enable"), ("disable", "Disable"), ("start", "Start"), ("stop", "Stop"), ("restart", "Restart"), ("check", "Check")],
            )
            if action:
                _run_with_status_message("ttyd", lambda: ttyd_action(action))
        elif choice == "6":
            if _yesno("SSH Keys", "Regenerate SSH host keys?"):
                _run_with_status_message("SSH Keys", generate_ssh_keys)
        elif choice == "7":
            command = legacy_tool_command(["htop", "top"])
            if command:
                _run_interactive(command, "Process Viewer", "Process viewer not available.")
            else:
                _message("Process Viewer", "Process viewer not available.")
        elif choice == "8":
            _time_menu()


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
                ("6", "Back"),
            ],
        )
        if choice in (None, "6"):
            return
        if choice == "1":
            _run_with_status_message("About mpwrd-config", lambda: license_info("about"))
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
                _run_with_status_message("Pinout", lambda: pinout_info(pinout_choice))
        elif choice == "3":
            _run_with_status_message("Femtofox license", lambda: license_info("short"))
        elif choice == "4":
            _run_with_status_message("Femtofox license (long)", lambda: license_info("long"))
        elif choice == "5":
            _run_with_status_message("Meshtastic license", lambda: license_info("meshtastic"))


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
            "Admin mesh client Wi-Fi toggle",
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
            _run_with_status_message("Admin mesh client Wi-Fi toggle", wifi_mesh_sync, empty="Sync complete.")
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

    service = _select_service(["femto-watchclock", "watchclock"])
    while True:
        choice = _menu(
            "Watchclock service",
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
        if choice in (None, "7"):
            return
        action_map = {
            "1": "status",
            "2": "start",
            "3": "stop",
            "4": "restart",
            "5": "enable",
            "6": "disable",
        }
        action = action_map.get(choice)
        if action:
            _run_cli_output(["services", service, action], f"{service} {action}")


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
    if os.getenv("MPWRD_TUI_STARTING_SHOWN") != "1":
        _print_starting_notice()
    meshtastic_session = MeshtasticSession()
    startup_done = threading.Event()
    startup_state: dict[str, Any] = {"connected": False, "error": "Meshtastic is still connecting."}
    startup_lock = threading.Lock()

    def _set_startup(connected: bool, error: str) -> None:
        with startup_lock:
            startup_state["connected"] = connected
            startup_state["error"] = error

    def _get_startup() -> tuple[bool, str]:
        with startup_lock:
            return bool(startup_state["connected"]), str(startup_state["error"] or "")

    def _startup_connect() -> None:
        try:
            error, interface = meshtastic_session.get_interface(
                wait_for_config=False,
                reconnect=True,
                attempts=1,
            )
            if not error and interface:
                _set_startup(True, "")
            else:
                _set_startup(False, error.stdout.strip() if error else "Unable to connect to Meshtastic.")
        except Exception as exc:
            _set_startup(False, f"Meshtastic connect failed: {exc}")
        finally:
            startup_done.set()

    startup_thread = threading.Thread(target=_startup_connect, name="meshtastic-startup", daemon=True)
    startup_thread.start()

    def close_handler() -> None:
        if startup_thread.is_alive():
            return
        meshtastic_session.close(wait=False)

    def _open_meshtastic_menu() -> None:
        if not startup_done.is_set():
            _run_with_status(
                "mpwrd-config",
                "Connecting to Meshtastic API...\nPlease wait.",
                lambda: startup_done.wait(),
            )
        startup_connected, startup_error = _get_startup()
        if not startup_connected:
            if _recover_meshtastic_connection(
                meshtastic_session,
                startup_error or "Meshtastic is not connected.\nTry reconnecting now?",
            ):
                _set_startup(True, "")
                startup_done.set()
        _meshtastic_menu(meshtastic_session)

    def _basic_menu() -> None:
        while True:
            choice = _menu(
                "Basic",
                [
                    ("1", "Install Wizard"),
                    ("2", "Back"),
                ],
            )
            if choice in (None, "2"):
                return
            if choice == "1":
                _install_wizard()

    def _advanced_menu() -> None:
        while True:
            choice = _menu(
                "Advanced",
                [
                    ("1", "Meshtastic"),
                    ("2", "Networking"),
                    ("3", "Software Manager"),
                    ("4", "System Utilities"),
                    ("5", "System Actions"),
                    ("6", "Help / About"),
                    ("7", "Back"),
                ],
            )
            if choice in (None, "7"):
                return
            if choice == "1":
                _open_meshtastic_menu()
            elif choice == "2":
                _networking_menu()
            elif choice == "3":
                _software_menu()
            elif choice == "4":
                _utilities_menu()
            elif choice == "5":
                _system_menu()
            elif choice == "6":
                _help_menu()

    atexit.register(close_handler)

    try:
        while True:
            choice = _menu(
                MAIN_MENU_TITLE,
                [
                    ("1", "Basic"),
                    ("2", "Advanced"),
                    ("3", "Exit"),
                ],
            )
            if choice in (None, "3"):
                _print_exiting_notice()
                return 0
            if choice == "1":
                _basic_menu()
            elif choice == "2":
                _advanced_menu()
    except _QuickExit:
        _print_exiting_notice()
        return 130
    finally:
        close_handler()
        try:
            atexit.unregister(close_handler)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
