from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from pathlib import Path
from typing import Any
import logging
import urllib.parse

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from starlette.middleware.sessions import SessionMiddleware

from mpwrd_config.core import DEFAULT_CONFIG_PATH, Config, WifiNetwork, load_config, save_config
from mpwrd_config.meshtastic import (
    add_admin_key,
    channel_add,
    channel_add_url,
    channel_delete,
    channel_disable,
    channel_enable,
    channel_set,
    channel_set_url,
    clear_admin_keys,
    current_radio,
    get_config_url,
    get_preference,
    get_private_key,
    get_public_key,
    get_legacy_admin_state,
    i2c_state,
    mac_address_source,
    mac_address_source_options,
    list_preference_fields,
    list_admin_keys,
    lora_settings,
    meshtastic_repo_status,
    mesh_test,
    meshtastic_config,
    meshtastic_info,
    meshtastic_snapshot,
    meshtastic_summary,
    meshtastic_update,
    service_action,
    service_enable,
    service_status,
    set_config_url,
    set_legacy_admin_state,
    set_lora_settings,
    set_mac_address_source,
    set_meshtastic_repo,
    set_preference,
    set_private_key,
    set_public_key,
    set_radio,
    uninstall,
    upgrade,
)
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
)
from mpwrd_config.kernel_modules import (
    blacklist_module,
    disable_module,
    enable_module,
    list_active_modules,
    list_blacklisted_modules,
    list_boot_modules,
    list_module_overview,
    unblacklist_module,
)
from mpwrd_config.software_manager import (
    license_text,
    list_packages,
    manage_full_control_conflicts,
    run_action,
    service_action as package_service_action,
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
from mpwrd_config.time_config import current_timezone, set_time, set_timezone, status as time_status
from mpwrd_config.wifi_mesh import sync_once as wifi_mesh_sync


TEMPLATE_DIR = Path(__file__).parent / "web_templates"
STATIC_DIR = Path(__file__).parent / "web_static"
SECRET_PATH = Path("/etc/mpwrd-config-web.secret")
AUTH_DISABLED = os.getenv("MPWRD_WEB_AUTH_DISABLED") == "1"
PAM_SERVICE_ENV = os.getenv("MPWRD_WEB_PAM_SERVICE")
PAM_SERVICE_PATH = Path("/etc/pam.d/mpwrd-config-web")
PAM_SERVICE_DEFAULT = "mpwrd-config-web" if PAM_SERVICE_PATH.exists() else "login"
PAM_SERVICE = PAM_SERVICE_ENV or PAM_SERVICE_DEFAULT
ALLOW_NON_ROOT = os.getenv("MPWRD_ALLOW_NON_ROOT") == "1"


try:
    import pam

    def _authenticate(username: str, password: str) -> bool:
        pam_session = pam.pam()
        ok = pam_session.authenticate(username, password, service=PAM_SERVICE)
        if not ok:
            logging.warning("PAM auth failed (service=%s, code=%s, reason=%s)", PAM_SERVICE, pam_session.code, pam_session.reason)
        return ok


except Exception:

    def _authenticate(username: str, password: str) -> bool:  # type: ignore[override]
        return False


def _load_secret() -> str:
    if SECRET_PATH.exists():
        return SECRET_PATH.read_text(encoding="utf-8").strip()
    secret = secrets.token_urlsafe(32)
    try:
        SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECRET_PATH.write_text(secret, encoding="utf-8")
    except OSError:
        pass
    return secret


def _qr_text_from_url(url: str) -> str | None:
    qrencode = shutil.which("qrencode")
    if not qrencode:
        return None
    result = subprocess.run(
        [qrencode, "-o", "-", "-t", "UTF8", "-s", "1", url],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        return None
    return result.stdout.rstrip()


def _qr_svg_from_url(url: str) -> str | None:
    qrencode = shutil.which("qrencode")
    if not qrencode:
        return None
    result = subprocess.run(
        [qrencode, "-t", "SVG", "-o", "-", url],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _config_path() -> Path:
    env_path = os.getenv("MPWRD_CONFIG_PATH")
    return Path(env_path) if env_path else DEFAULT_CONFIG_PATH


def _get_config() -> Config:
    return load_config(_config_path())


def _save_config(config: Config) -> None:
    save_config(config, _config_path())


def _human_uptime() -> str:
    try:
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return "unknown"
    minutes, _ = divmod(int(uptime_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _dashboard_stats(status: dict[str, Any], config: Config) -> dict[str, str]:
    load = "unknown"
    try:
        one, five, fifteen = os.getloadavg()
        load = f"{one:.2f} {five:.2f} {fifteen:.2f}"
    except Exception:
        pass
    mem_total = mem_avail = 0
    try:
        meminfo = Path("/proc/meminfo").read_text().splitlines()
        data = {}
        for line in meminfo:
            if ":" in line:
                key, rest = line.split(":", 1)
                data[key.strip()] = rest.strip()
        mem_total = int((data.get("MemTotal", "0 kB").split()[0]))
        mem_avail = int((data.get("MemAvailable", "0 kB").split()[0]))
    except Exception:
        pass
    mem_used = max(mem_total - mem_avail, 0)
    mem_total_mb = mem_total // 1024 if mem_total else 0
    mem_used_mb = mem_used // 1024 if mem_used else 0
    mem_pct = (mem_used / mem_total * 100) if mem_total else 0.0
    memory = f"{mem_used_mb} / {mem_total_mb} MB ({mem_pct:.1f}%)" if mem_total else "unknown"

    disk = "unknown"
    try:
        total, used, _free = shutil.disk_usage("/")
        total_gb = total / 1024 / 1024 / 1024
        used_gb = used / 1024 / 1024 / 1024
        pct = (used / total * 100) if total else 0.0
        disk = f"{used_gb:.2f} / {total_gb:.2f} GB ({pct:.1f}%)"
    except Exception:
        pass

    if config.networking.ethernet_interface:
        default_iface = f"ethernet: {config.networking.ethernet_interface}"
    elif config.networking.wifi_interface:
        default_iface = f"wifi: {config.networking.wifi_interface}"
    else:
        default_iface = "auto"
    radio = status.get("radio", "unknown") or "unknown"

    return {
        "hostname": status.get("hostname", "unknown"),
        "uptime": _human_uptime(),
        "load": load,
        "memory": memory,
        "disk": disk,
        "ip": status.get("ip_addresses", "unknown"),
        "default_interface": default_iface,
        "radio": radio,
    }


def _status_base(config: Config) -> dict[str, Any]:
    placeholder = "Loading..."
    return {
        "hostname": config.networking.hostname or "unknown",
        "ip_addresses": placeholder,
        "wifi_status": placeholder,
        "ethernet_status": placeholder,
        "meshtastic_service": placeholder,
        "meshtastic_repo": placeholder,
        "meshtastic_info": placeholder,
        "meshtastic_summary": placeholder,
        "meshtastic_config_url": placeholder,
        "meshtastic_i2c": placeholder,
        "meshtastic_legacy_admin": placeholder,
        "meshtastic_mac_source": "",
        "radio": "",
        "lora_settings": {},
        "lora_error": "",
    }


def _dashboard_placeholder() -> dict[str, str]:
    placeholder = "Loading..."
    return {
        "hostname": placeholder,
        "uptime": placeholder,
        "load": placeholder,
        "memory": placeholder,
        "disk": placeholder,
        "ip": placeholder,
        "default_interface": placeholder,
        "radio": placeholder,
    }


def _dashboard_live(config: Config) -> dict[str, str]:
    status = {
        "hostname": os.uname().nodename,
        "ip_addresses": ip_addresses().stdout.strip(),
        "radio": current_radio().stdout.strip(),
    }
    return _dashboard_stats(status, config)


def _status_data(config: Config) -> dict[str, Any]:
    meshtastic_details = meshtastic_info().stdout.strip()
    snapshot_result, snapshot = meshtastic_snapshot()
    i2c_status = i2c_state("check").stdout.strip()
    mac_result = mac_address_source()
    mac_source = mac_result.stdout.strip() if mac_result.returncode == 0 else "unset"
    if snapshot_result.returncode == 0:
        summary = snapshot.get("summary", "").strip()
        config_url = str(snapshot.get("config_url") or "")
        legacy_admin = str(snapshot.get("legacy_admin") or "unknown")
        lora_raw = snapshot.get("lora", {}) if isinstance(snapshot.get("lora"), dict) else {}
        lora_settings_view = {key: _stringify_lora(value) for key, value in lora_raw.items()}
        lora_error = ""
    else:
        summary = snapshot_result.stdout.strip()
        config_url = ""
        legacy_admin = "unknown"
        lora_settings_view = {}
        lora_error = snapshot_result.stdout.strip() or "Unable to read LoRa settings."
    return {
        "hostname": os.uname().nodename,
        "ip_addresses": ip_addresses().stdout.strip(),
        "wifi_status": wifi_status(config.networking.wifi_interface).stdout.strip(),
        "ethernet_status": ethernet_status(config.networking.ethernet_interface).stdout.strip(),
        "meshtastic_service": service_status().stdout.strip(),
        "meshtastic_repo": meshtastic_repo_status().stdout.strip(),
        "meshtastic_info": meshtastic_details,
        "meshtastic_summary": summary or "Unavailable",
        "meshtastic_config_url": config_url or "Unavailable",
        "meshtastic_i2c": i2c_status,
        "meshtastic_legacy_admin": legacy_admin,
        "meshtastic_mac_source": mac_source,
        "radio": current_radio().stdout.strip(),
        "lora_settings": lora_settings_view,
        "lora_error": lora_error,
    }


def _set_notice(
    request: Request,
    message: str,
    kind: str = "info",
    section: str | None = None,
    sub: str | None = None,
) -> None:
    request.session["notice"] = message
    request.session["notice_kind"] = kind
    if section:
        request.session["notice_section"] = section
    else:
        request.session.pop("notice_section", None)
    if sub is None:
        sub = request.query_params.get("sub")
    if not sub and section:
        path = request.url.path
        if section == "meshtastic":
            if path.startswith("/actions/meshtastic/service/"):
                sub = "service"
            elif path in {
                "/actions/meshtastic/i2c",
                "/actions/meshtastic/mac-source",
                "/actions/meshtastic/legacy-admin",
                "/actions/meshtastic/legacy-admin-status",
                "/actions/meshtastic/mesh-test",
            }:
                sub = "i2c"
            elif path in {
                "/actions/meshtastic/config-url",
                "/actions/meshtastic/config-qr",
                "/actions/meshtastic/radio",
            }:
                sub = "radio"
            elif path == "/actions/meshtastic/repo":
                sub = "repo"
            elif path in {
                "/actions/meshtastic/info",
                "/actions/meshtastic/summary",
                "/actions/meshtastic/lora-show",
            }:
                sub = "diagnostics"
            elif path in {
                "/actions/meshtastic/public-key",
                "/actions/meshtastic/private-key",
                "/actions/meshtastic/admin-key",
                "/actions/meshtastic/admin-clear",
                "/actions/meshtastic/public-key-show",
                "/actions/meshtastic/private-key-show",
                "/actions/meshtastic/admin-keys-show",
            }:
                sub = "keys"
            elif path in {
                "/actions/meshtastic/config",
                "/actions/meshtastic/pref-fields",
                "/actions/meshtastic/pref-get",
                "/actions/meshtastic/pref-set",
                "/actions/meshtastic/channel-set",
                "/actions/meshtastic/channel-add",
                "/actions/meshtastic/channel-del",
                "/actions/meshtastic/channel-enable",
                "/actions/meshtastic/channel-disable",
                "/actions/meshtastic/channel-set-url",
                "/actions/meshtastic/channel-add-url",
            }:
                sub = "preferences"
            elif path in {
                "/actions/meshtastic/upgrade",
                "/actions/meshtastic/uninstall",
                "/actions/meshtastic/update",
            }:
                sub = "advanced"
            elif path == "/actions/meshtastic/lora":
                sub = "lora"
        elif section == "networking":
            if path in {"/actions/hostname", "/actions/wifi-interface", "/actions/ethernet-interface"}:
                sub = "identity"
            elif path in {"/actions/wifi", "/actions/wifi-state", "/actions/wifi-restart"}:
                sub = "wifi"
            elif path in {"/actions/wifi-status", "/actions/ethernet-status", "/actions/ip-addresses", "/actions/internet-test"}:
                sub = "diagnostics"
            elif path == "/actions/service":
                sub = "services"
        elif section == "kernel" and path == "/actions/kernel-modules":
            sub = "modules"
        elif section == "time" and path in {"/actions/timezone", "/actions/time"}:
            sub = "time"
        elif section == "software" and path.startswith("/actions/software/"):
            sub = "manager"
        elif section == "utilities":
            if path == "/actions/web-ui":
                sub = "webui"
            elif path in {"/actions/act-led", "/actions/logging", "/actions/ttyd"}:
                sub = "toggles"
            elif path == "/actions/ssh-keys":
                sub = "ssh-keys"
            elif path == "/actions/system-info":
                sub = "system-info"
            elif path.startswith("/actions/legacy/"):
                sub = "legacy"
        elif section == "help":
            if path == "/actions/help/license":
                sub = "about"
            elif path == "/actions/help/pinout":
                sub = "pinouts"
        elif section == "networking" and path == "/actions/wifi-mesh/sync":
            sub = "wifi-mesh"
        elif section == "time" and path == "/actions/service":
            sub = "time"
        elif section == "wizard" and path == "/actions/wizard":
            sub = "wizard"
    if not sub:
        referer = request.headers.get("referer", "")
        if referer:
            try:
                parsed = urllib.parse.urlparse(referer)
                sub = urllib.parse.parse_qs(parsed.query).get("sub", [None])[0]
            except Exception:
                sub = None
    if sub:
        request.session["notice_sub"] = sub
    else:
        request.session.pop("notice_sub", None)

def _redirect_to_section(request: Request, section: str | None) -> RedirectResponse:
    if section:
        sub = request.session.get("notice_sub") or request.query_params.get("sub")
        if sub:
            return RedirectResponse(f"/?section={section}&sub={sub}", status_code=303)
        return RedirectResponse(f"/?section={section}", status_code=303)
    return RedirectResponse("/", status_code=303)


def _with_conflicts(
    request: Request,
    action_fn,
    fallback: str,
    prefer_error: bool = False,
    section: str | None = None,
    sub: str | None = None,
) -> RedirectResponse:
    stop_result = manage_full_control_conflicts("stop")
    result = action_fn()
    start_result = manage_full_control_conflicts("start")
    parts = [stop_result.stdout.strip(), result.stdout.strip(), start_result.stdout.strip()]
    message = "\n".join(part for part in parts if part)
    if not message:
        message = fallback
    is_error = result.returncode != 0 or prefer_error
    _set_notice(request, message, "error" if is_error else "info", section=section, sub=sub)
    return _redirect_to_section(request, section)


def _interactive_notice(request: Request, tool: str, section: str | None = None) -> RedirectResponse:
    host = request.url.hostname or "this device"
    message = (
        f"{tool} is interactive.\n\n"
        "Use the TUI, or open the web terminal at:\n"
        f"https://{host}:7681"
    )
    _set_notice(request, message, "info", section=section)
    return _redirect_to_section(request, section)


def _stringify_lora(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _service_unit_exists(name: str) -> bool:
    for base in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
        if Path(base, f"{name}.service").exists():
            return True
    return False


def _unit_file_exists(name: str) -> bool:
    for base in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system"):
        if Path(base, name).exists():
            return True
    return False


def _install_web_ui_units() -> tuple[int, str]:
    repo_root = Path(__file__).resolve().parent.parent
    systemd_dir = repo_root / "systemd"
    dest_dir = Path("/etc/systemd/system")
    messages: list[str] = []
    for unit in ("mpwrd-config-web.socket", "mpwrd-config-web.service"):
        source = systemd_dir / unit
        dest = dest_dir / unit
        if not source.exists():
            messages.append(f"Missing source file: {source}")
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        messages.append(f"Installed {unit} -> {dest}")
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    return (0 if messages else 1), "\n".join(messages) or "No service files installed."


def _select_service(candidates: list[str]) -> str:
    for candidate in candidates:
        if _service_unit_exists(candidate):
            return candidate
    return candidates[0]


def _update_wifi_enabled(config: Config, enabled: bool):
    config.networking.wifi_enabled = enabled
    _save_config(config)
    return wifi_state("up" if enabled else "down", interface=config.networking.wifi_interface)

def create_app() -> FastAPI:
    app = FastAPI(title="mpwrd-config")
    @app.middleware("http")
    async def _auth_middleware(request: Request, call_next):
        if not ALLOW_NON_ROOT and os.geteuid() != 0:
            return HTMLResponse(
                "mpwrd-config web must run as root. Start with sudo or set MPWRD_ALLOW_NON_ROOT=1 for dev.",
                status_code=503,
            )
        if AUTH_DISABLED:
            return await call_next(request)
        if request.url.path.startswith("/static"):
            return await call_next(request)
        if request.url.path in {"/login", "/logout"}:
            return await call_next(request)
        if not request.session.get("user"):
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key=_load_secret())
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=TEMPLATE_DIR)

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": None},
        )

    @app.post("/login")
    def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
        if _authenticate(username, password):
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials."},
            status_code=401,
        )

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        config = _get_config()
        status = _status_base(config)
        notice = request.session.pop("notice", None)
        notice_kind = request.session.pop("notice_kind", None)
        notice_section = request.session.pop("notice_section", None)
        notice_sub = request.session.pop("notice_sub", None)
        meshtastic_qr_url = request.session.get("meshtastic_qr_url")
        meshtastic_qr_svg = None
        if meshtastic_qr_url:
            svg = _qr_svg_from_url(meshtastic_qr_url)
            if svg:
                meshtastic_qr_svg = Markup(svg)
        valid_sections = {
            "home",
            "networking",
            "meshtastic",
            "kernel",
            "time",
            "software",
            "utilities",
            "help",
            "system-actions",
            "wizard",
        }
        query_section = request.query_params.get("section")
        query_sub = request.query_params.get("sub")
        active_section = query_section if query_section in valid_sections else None
        if not active_section and notice_section in valid_sections:
            active_section = notice_section
        if not active_section:
            active_section = "home"
        default_subs = {
            "home": "home",
            "networking": "identity",
            "meshtastic": "overview",
            "kernel": "modules",
            "time": "time",
            "software": "manager",
            "utilities": "snapshot",
            "help": "about",
            "wizard": "wizard",
        }
        active_sub = None
        if query_sub and query_section == active_section:
            active_sub = query_sub
        elif notice_sub and notice_section == active_section:
            active_sub = notice_sub
        if not active_sub:
            active_sub = default_subs.get(active_section)
        interfaces = {
            "wifi": list_wifi_interfaces(),
            "ethernet": list_ethernet_interfaces(),
        }
        kernel_info = {
            "boot": "",
            "active": "",
            "blacklist": "",
            "modules": [],
        }
        packages = None
        mesh_service = _select_service(["femto-wifi-mesh", "femto-wifi-mesh-control"])
        service_states = {
            "mesh": "Loading...",
            "avahi": "Loading...",
            "watchclock": "Loading...",
        }
        web_ui = {
            "socket_status": "Loading...",
            "service_status": "Loading...",
            "note": "",
        }
        time_info = {
            "status": "Loading...",
            "timezone": "Loading...",
        }
        dashboard = _dashboard_placeholder()
        radio_options = [
            ("lr1121_tcxo", "LR1121 TCXO"),
            ("sx1262_tcxo", "SX1262 TCXO (Ebyte e22-900m30s / Heltec ht-ra62 / Seeed wio-sx1262)"),
            ("sx1262_xtal", "SX1262 XTAL (Ebyte e80-900m22s / Waveshare / AI Thinker ra-01sh)"),
            ("lora-meshstick-1262", "LoRa Meshstick 1262 (USB)"),
            ("none", "Simulated radio"),
        ]
        mac_source_options = mac_address_source_options()
        current_mac = status.get("meshtastic_mac_source")
        if current_mac and current_mac != "unset" and current_mac not in {value for value, _ in mac_source_options}:
            mac_source_options.insert(0, (current_mac, f"Current ({current_mac})"))
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "status": status,
                "config": config,
                "dashboard": dashboard,
                "interfaces": interfaces,
                "notice": notice,
                "notice_kind": notice_kind,
                "notice_section": notice_section,
                "notice_sub": notice_sub,
                "active_section": active_section,
                "active_sub": active_sub,
                "radio_options": radio_options,
                "mac_source_options": mac_source_options,
                "kernel": kernel_info,
                "time_info": time_info,
                "packages": packages,
                "mesh_service": mesh_service,
                "service_states": service_states,
                "web_ui": web_ui,
                "meshtastic_qr_svg": meshtastic_qr_svg,
                "meshtastic_qr_url": meshtastic_qr_url,
            },
        )

    @app.get("/api/meshtastic-status")
    def meshtastic_status_api(request: Request):
        config = _get_config()
        return JSONResponse(_status_data(config))

    @app.get("/api/software-packages", response_class=HTMLResponse)
    def software_packages_api(request: Request):
        packages = list_packages()
        return templates.TemplateResponse(
            "partials/software_packages.html",
            {"request": request, "packages": packages},
        )

    @app.get("/api/dashboard")
    def dashboard_api(request: Request):
        config = _get_config()
        return JSONResponse(_dashboard_live(config))

    @app.get("/api/networking-status")
    def networking_status_api(request: Request):
        config = _get_config()
        return JSONResponse(
            {
                "hostname": os.uname().nodename,
                "ip_addresses": ip_addresses().stdout.strip(),
                "wifi_status": wifi_status(config.networking.wifi_interface).stdout.strip(),
                "ethernet_status": ethernet_status(config.networking.ethernet_interface).stdout.strip(),
            }
        )

    @app.get("/api/time-status")
    def time_status_api(request: Request):
        return JSONResponse(
            {
                "timezone": current_timezone().stdout.strip(),
                "status": time_status().stdout,
            }
        )

    @app.get("/api/service-states")
    def service_states_api(request: Request):
        mesh_service = _select_service(["femto-wifi-mesh", "femto-wifi-mesh-control"])
        return JSONResponse(
            {
                "mesh": system_service_status(mesh_service).stdout.strip(),
                "avahi": system_service_status("avahi-daemon").stdout.strip(),
                "watchclock": system_service_status("femto-watchclock").stdout.strip(),
            }
        )

    @app.get("/api/web-ui-status")
    def web_ui_status_api(request: Request):
        web_socket = "mpwrd-config-web.socket"
        web_service = "mpwrd-config-web.service"
        socket_status = system_service_status(web_socket).stdout.strip() if _unit_file_exists(web_socket) else "not installed"
        service_status_text = system_service_status(web_service).stdout.strip() if _unit_file_exists(web_service) else "not installed"
        web_note = ""
        if "running" in socket_status and "not running" in service_status_text:
            web_note = "Socket is active and will start the service on first connection."
        return JSONResponse(
            {
                "socket_status": socket_status,
                "service_status": service_status_text,
                "note": web_note,
            }
        )

    @app.post("/actions/hostname")
    def set_hostname_action(request: Request, hostname: str = Form(...), confirm: str = Form("")):
        if confirm.strip().lower() != "hostname":
            _set_notice(request, "Confirmation required to change hostname.", "error", section="networking")
            return _redirect_to_section(request, "networking")
        config = _get_config()
        config.networking.hostname = hostname
        _save_config(config)
        result = set_hostname(hostname)
        _set_notice(
            request,
            result.stdout.strip() or "Hostname updated.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/wifi")
    def set_wifi_action(
        request: Request,
        ssid: str = Form(...),
        psk: str = Form(...),
        country: str | None = Form(None),
    ):
        config = _get_config()
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
        _save_config(config)
        result = set_wifi_credentials(ssid, psk, country, interface=config.networking.wifi_interface)
        _set_notice(
            request,
            result.stdout.strip() or "Wi-Fi settings updated.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/wifi-toggle")
    def wifi_toggle_action(request: Request):
        config = _get_config()
        enabled = not config.networking.wifi_enabled
        result = _update_wifi_enabled(config, enabled)
        state_label = "enabled" if enabled else "disabled"
        message = result.stdout.strip() or f"Wi-Fi {state_label}."
        _set_notice(request, message, "error" if result.returncode else "info", section="networking")
        return _redirect_to_section(request, "networking")

    @app.post("/actions/wifi-interface")
    def wifi_interface_action(request: Request, interface: str = Form("")):
        config = _get_config()
        interface = interface.strip()
        interfaces = list_wifi_interfaces()
        if interface and interface not in interfaces:
            _set_notice(request, f"Wi-Fi interface '{interface}' not found.", "error", section="networking")
            return _redirect_to_section(request, "networking")
        config.networking.wifi_interface = interface or None
        _save_config(config)
        _set_notice(request, "Wi-Fi interface updated.", "info", section="networking")
        return _redirect_to_section(request, "networking")

    @app.post("/actions/ethernet-interface")
    def ethernet_interface_action(request: Request, interface: str = Form("")):
        config = _get_config()
        interface = interface.strip()
        interfaces = list_ethernet_interfaces()
        if interface and interface not in interfaces:
            _set_notice(request, f"Ethernet interface '{interface}' not found.", "error", section="networking")
            return _redirect_to_section(request, "networking")
        config.networking.ethernet_interface = interface or None
        _save_config(config)
        _set_notice(request, "Ethernet interface updated.", "info", section="networking")
        return _redirect_to_section(request, "networking")

    @app.post("/actions/wifi-state")
    def wifi_state_action(request: Request, state: str = Form(...)):
        if state not in {"enable", "disable"}:
            return _redirect_to_section(request, "networking")
        config = _get_config()
        enabled = state == "enable"
        result = _update_wifi_enabled(config, enabled)
        message = result.stdout.strip() or f"Wi-Fi {state}d."
        _set_notice(request, message, "error" if result.returncode else "info", section="networking")
        return _redirect_to_section(request, "networking")

    @app.post("/actions/wifi-restart")
    def wifi_restart_action(request: Request):
        config = _get_config()
        result = wifi_restart(config.networking.wifi_interface)
        _set_notice(
            request,
            result.stdout.strip() or "Wi-Fi restart requested.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/network-test")
    def network_test_action(request: Request):
        result = test_internet()
        _set_notice(
            request,
            result.stdout.strip() or "Internet test complete.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/wifi-status")
    def wifi_status_action(request: Request):
        config = _get_config()
        result = wifi_status(config.networking.wifi_interface)
        _set_notice(
            request,
            result.stdout.strip() or "Wi-Fi status unavailable.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/ethernet-status")
    def ethernet_status_action(request: Request):
        config = _get_config()
        result = ethernet_status(config.networking.ethernet_interface)
        _set_notice(
            request,
            result.stdout.strip() or "Ethernet status unavailable.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/ip-addresses")
    def ip_addresses_action(request: Request):
        result = ip_addresses()
        _set_notice(
            request,
            result.stdout.strip() or "No IP addresses found.",
            "error" if result.returncode else "info",
            section="networking",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/service")
    def service_action_handler(request: Request, name: str = Form(...), action: str = Form(...)):
        allowed_actions = {"status", "start", "stop", "restart", "enable", "disable"}
        if action not in allowed_actions:
            return _redirect_to_section(request, "networking")
        if name not in {"avahi-daemon", "femto-wifi-mesh", "femto-wifi-mesh-control", "femto-watchclock"}:
            return _redirect_to_section(request, "networking")
        result = system_service_action(name, action)
        if name == "femto-watchclock":
            section = "time"
            sub = None
        elif name in {"femto-wifi-mesh", "femto-wifi-mesh-control"}:
            section = "networking"
            sub = "wifi-mesh"
        else:
            section = "networking"
            sub = None
        _set_notice(
            request,
            result.stdout.strip() or f"{name} {action} requested.",
            "error" if result.returncode else "info",
            section=section,
            sub=sub,
        )
        return _redirect_to_section(request, section)

    @app.post("/actions/kernel-modules")
    def kernel_modules_action(request: Request, action: str = Form(...), name: str = Form("")):
        list_handlers = {
            "boot": list_boot_modules,
            "active": list_active_modules,
            "blacklist": list_blacklisted_modules,
        }
        handler = list_handlers.get(action)
        if handler is not None:
            result = handler()
            _set_notice(
                request,
                result.stdout.strip() or "No modules reported.",
                "error" if result.returncode else "info",
                section="kernel",
            )
            return _redirect_to_section(request, "kernel")
        if action == "overview":
            modules = list_module_overview()
            if not modules:
                _set_notice(request, "No modules found in the kernel module directory.", "info", section="kernel")
                return _redirect_to_section(request, "kernel")
            lines = []
            for module in modules:
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
                lines.append(label)
            _set_notice(request, "\n".join(lines), "info", section="kernel")
            return _redirect_to_section(request, "kernel")

        module_handlers = {
            "enable": enable_module,
            "disable": disable_module,
            "blacklist-set": blacklist_module,
            "blacklist-clear": unblacklist_module,
        }
        module_action = module_handlers.get(action)
        if module_action is None:
            return _redirect_to_section(request, "kernel")
        module_name = name.strip()
        if not module_name:
            _set_notice(request, "Module name is required.", "error", section="kernel")
            return _redirect_to_section(request, "kernel")
        result = module_action(module_name)
        _set_notice(
            request,
            result.stdout.strip() or "Module action complete.",
            "error" if result.returncode else "info",
            section="kernel",
        )
        return _redirect_to_section(request, "kernel")

    @app.get("/api/kernel-modules")
    def kernel_modules_api():
        modules = list_module_overview()
        return {
            "boot": list_boot_modules().stdout.strip(),
            "active": list_active_modules().stdout.strip(),
            "blacklist": list_blacklisted_modules().stdout.strip(),
            "modules": [
                {
                    "name": module.name,
                    "loaded": module.loaded,
                    "boot": module.boot,
                    "blacklisted": module.blacklisted,
                }
                for module in modules
            ],
        }

    @app.post("/actions/meshtastic/service/{action}")
    def meshtastic_service_action(request: Request, action: str):
        if action in {"start", "stop", "restart"}:
            result = service_action(action)
        elif action == "status":
            result = service_status()
        elif action in {"enable", "disable"}:
            result = service_enable(action == "enable")
        else:
            return _redirect_to_section(request, "meshtastic")
        _set_notice(
            request,
            result.stdout.strip() or f"Service {action} requested.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/config-url")
    def meshtastic_config_url_action(request: Request, url: str = Form(...)):
        result = set_config_url(url)
        if result.returncode == 0:
            request.session["meshtastic_qr_url"] = url
        else:
            request.session.pop("meshtastic_qr_url", None)
        _set_notice(
            request,
            result.stdout.strip() or "Config URL updated.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/radio")
    def meshtastic_radio_action(request: Request, model: str = Form(...)):
        result = set_radio(model)
        _set_notice(
            request,
            result.stdout.strip() or f"Radio set to {model}.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/i2c")
    def meshtastic_i2c_action(request: Request, state: str = Form(...)):
        result = i2c_state(state)
        _set_notice(
            request,
            result.stdout.strip() or f"I2C {state}.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/mac-source")
    def meshtastic_mac_source_action(request: Request, source: str = Form(...)):
        result = set_mac_address_source(source)
        _set_notice(
            request,
            result.stdout.strip() or "MAC address source updated.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/legacy-admin")
    def meshtastic_legacy_admin_action(request: Request, enabled: str = Form(...)):
        result = set_legacy_admin_state(enabled == "true")
        _set_notice(
            request,
            result.stdout.strip() or "Legacy admin updated.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/legacy-admin-status")
    def meshtastic_legacy_admin_status_action(request: Request):
        result = get_legacy_admin_state()
        _set_notice(
            request,
            result.stdout.strip() or "Legacy admin status unavailable.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/public-key")
    def meshtastic_public_key_action(request: Request, key: str = Form(...)):
        result = set_public_key(key)
        _set_notice(
            request,
            result.stdout.strip() or "Public key updated.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/private-key")
    def meshtastic_private_key_action(request: Request, key: str = Form(...)):
        result = set_private_key(key)
        _set_notice(
            request,
            result.stdout.strip() or "Private key updated.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/admin-key")
    def meshtastic_admin_key_action(request: Request, key: str = Form(...)):
        result = add_admin_key(key)
        _set_notice(
            request,
            result.stdout.strip() or "Admin key added.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/admin-clear")
    def meshtastic_admin_clear_action(request: Request):
        result = clear_admin_keys()
        _set_notice(
            request,
            result.stdout.strip() or "Admin keys cleared.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/mesh-test")
    def meshtastic_mesh_test_action(request: Request):
        result = mesh_test()
        _set_notice(
            request,
            result.stdout.strip() or "Mesh test complete.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/upgrade")
    def meshtastic_upgrade_action(request: Request):
        result = upgrade()
        _set_notice(
            request,
            result.stdout.strip() or "Upgrade requested.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/uninstall")
    def meshtastic_uninstall_action(request: Request):
        result = uninstall()
        _set_notice(
            request,
            result.stdout.strip() or "Uninstall requested.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/update")
    def meshtastic_update_action(request: Request, command: str = Form(...)):
        result = meshtastic_update(command, attempts=3, label="Custom")
        _set_notice(
            request,
            result.stdout.strip() or "Meshtastic update requested.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/config")
    def meshtastic_config_action(request: Request, category: str = Form(...)):
        category = category.strip().lower()
        if category not in {"all", "nodeinfo", "settings", "channels"}:
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: meshtastic_config(category),
            "Meshtastic config unavailable.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/pref-fields")
    def meshtastic_pref_fields_action(request: Request):
        return _with_conflicts(
            request,
            list_preference_fields,
            "No preference fields returned.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/pref-get")
    def meshtastic_pref_get_action(request: Request, field: str = Form(...)):
        field = field.strip()
        if not field:
            _set_notice(request, "Preference field is required.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: get_preference(field),
            "Preference value unavailable.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/pref-set")
    def meshtastic_pref_set_action(request: Request, field: str = Form(...), value: str = Form(...)):
        field = field.strip()
        if not field:
            _set_notice(request, "Preference field is required.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: set_preference(field, value),
            "Preference updated.",
            section="meshtastic",
        )

    def _parse_channel_index(index: str) -> int | None:
        try:
            value = int(index)
        except ValueError:
            return None
        return value if value >= 0 else None

    @app.post("/actions/meshtastic/channel-set")
    def meshtastic_channel_set_action(
        request: Request,
        index: str = Form(...),
        field: str = Form(...),
        value: str = Form(...),
    ):
        channel_index = _parse_channel_index(index.strip())
        if channel_index is None:
            _set_notice(request, "Channel index must be a non-negative integer.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        field = field.strip()
        if not field:
            _set_notice(request, "Channel field is required.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_set(channel_index, field, value),
            "Channel updated.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/channel-add")
    def meshtastic_channel_add_action(request: Request, name: str = Form(...)):
        name = name.strip()
        if not name:
            _set_notice(request, "Channel name is required.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_add(name),
            "Channel added.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/channel-del")
    def meshtastic_channel_del_action(request: Request, index: str = Form(...)):
        channel_index = _parse_channel_index(index.strip())
        if channel_index is None:
            _set_notice(request, "Channel index must be a non-negative integer.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_delete(channel_index),
            "Channel deleted.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/channel-enable")
    def meshtastic_channel_enable_action(request: Request, index: str = Form(...)):
        channel_index = _parse_channel_index(index.strip())
        if channel_index is None:
            _set_notice(request, "Channel index must be a non-negative integer.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_enable(channel_index),
            "Channel enabled.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/channel-disable")
    def meshtastic_channel_disable_action(request: Request, index: str = Form(...)):
        channel_index = _parse_channel_index(index.strip())
        if channel_index is None:
            _set_notice(request, "Channel index must be a non-negative integer.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_disable(channel_index),
            "Channel disabled.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/channel-set-url")
    def meshtastic_channel_set_url_action(request: Request, url: str = Form(...)):
        url = url.strip()
        if not url:
            _set_notice(request, "Configuration URL is required.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_set_url(url),
            "Channels updated from URL.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/channel-add-url")
    def meshtastic_channel_add_url_action(request: Request, url: str = Form(...)):
        url = url.strip()
        if not url:
            _set_notice(request, "Configuration URL is required.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        return _with_conflicts(
            request,
            lambda: channel_add_url(url),
            "Channels added from URL.",
            section="meshtastic",
        )

    @app.post("/actions/meshtastic/repo")
    def meshtastic_repo_action(request: Request, action: str = Form(...), channel: str = Form("")):
        action = action.strip()
        channel = channel.strip().lower()
        if action == "status":
            result = meshtastic_repo_status()
            _set_notice(
                request,
                result.stdout.strip() or "Repo status unavailable.",
                "error" if result.returncode else "info",
                section="meshtastic",
            )
            return _redirect_to_section(request, "meshtastic")
        if action == "set" and channel:
            result = set_meshtastic_repo(channel)
            _set_notice(
                request,
                result.stdout.strip() or f"Repo set to {channel}.",
                "error" if result.returncode else "info",
                section="meshtastic",
            )
            return _redirect_to_section(request, "meshtastic")
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/info")
    def meshtastic_info_action(request: Request):
        result = meshtastic_info()
        _set_notice(
            request,
            result.stdout.strip() or "Meshtastic info unavailable.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/summary")
    def meshtastic_summary_action(request: Request):
        result = meshtastic_summary()
        _set_notice(
            request,
            result.stdout.strip() or "Meshtastic summary unavailable.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/config-qr")
    def meshtastic_config_qr_action(request: Request):
        result = get_config_url()
        request.session.pop("meshtastic_qr", None)
        if result.returncode == 0 and result.stdout.strip():
            request.session["meshtastic_qr_url"] = result.stdout.strip()
            message = "Config URL generated below."
            kind = "info"
        else:
            request.session.pop("meshtastic_qr_url", None)
            message = result.stdout.strip() or "Config URL unavailable."
            kind = "error"
        _set_notice(
            request,
            message,
            kind,
            section="meshtastic",
            sub="radio",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/public-key-show")
    def meshtastic_public_key_show_action(request: Request):
        result = get_public_key()
        _set_notice(
            request,
            result.stdout.strip() or "Public key unavailable.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/private-key-show")
    def meshtastic_private_key_show_action(request: Request):
        result = get_private_key()
        _set_notice(
            request,
            result.stdout.strip() or "Private key unavailable.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/admin-keys-show")
    def meshtastic_admin_keys_show_action(request: Request):
        result = list_admin_keys()
        _set_notice(
            request,
            result.stdout.strip() or "Admin keys unavailable.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/lora-show")
    def meshtastic_lora_show_action(request: Request):
        result, settings = lora_settings()
        if result.returncode != 0:
            _set_notice(request, result.stdout.strip() or "Unable to read LoRa settings.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        lines = [f"{key}:{value}" for key, value in settings.items()]
        _set_notice(request, "\n".join(lines) or "No LoRa settings found.", "info", section="meshtastic")
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/meshtastic/lora")
    def meshtastic_lora_action(
        request: Request,
        region: str = Form(""),
        use_preset: str = Form(""),
        preset: str = Form(""),
        bandwidth: str = Form(""),
        spread_factor: str = Form(""),
        coding_rate: str = Form(""),
        frequency_offset: str = Form(""),
        hop_limit: str = Form(""),
        tx_enabled: str = Form(""),
        tx_power: str = Form(""),
        channel_num: str = Form(""),
        override_duty_cycle: str = Form(""),
        sx126x_rx_boosted_gain: str = Form(""),
        override_frequency: str = Form(""),
        ignore_mqtt: str = Form(""),
        ok_to_mqtt: str = Form(""),
    ):
        settings: dict[str, str] = {}
        if region.strip():
            settings["region"] = region.strip()
        if use_preset in {"true", "false"}:
            settings["use_preset"] = use_preset
        if preset.strip():
            settings["modem_preset"] = preset.strip()
        if bandwidth.strip():
            settings["bandwidth"] = bandwidth.strip()
        if spread_factor.strip():
            settings["spread_factor"] = spread_factor.strip()
        if coding_rate.strip():
            settings["coding_rate"] = coding_rate.strip()
        if frequency_offset.strip():
            settings["frequency_offset"] = frequency_offset.strip()
        if hop_limit.strip():
            settings["hop_limit"] = hop_limit.strip()
        if tx_enabled in {"true", "false"}:
            settings["tx_enabled"] = tx_enabled
        if tx_power.strip():
            settings["tx_power"] = tx_power.strip()
        if channel_num.strip():
            settings["channel_num"] = channel_num.strip()
        if override_duty_cycle in {"true", "false"}:
            settings["override_duty_cycle"] = override_duty_cycle
        if sx126x_rx_boosted_gain in {"true", "false"}:
            settings["sx126x_rx_boosted_gain"] = sx126x_rx_boosted_gain
        if override_frequency.strip():
            settings["override_frequency"] = override_frequency.strip()
        if ignore_mqtt in {"true", "false"}:
            settings["ignore_mqtt"] = ignore_mqtt
        if ok_to_mqtt in {"true", "false"}:
            settings["config_ok_to_mqtt"] = ok_to_mqtt
        if not settings:
            _set_notice(request, "No LoRa settings provided.", "error", section="meshtastic")
            return _redirect_to_section(request, "meshtastic")
        result = set_lora_settings(settings)
        _set_notice(
            request,
            result.stdout.strip() or "LoRa settings updated.",
            "error" if result.returncode else "info",
            section="meshtastic",
        )
        return _redirect_to_section(request, "meshtastic")

    @app.post("/actions/act-led")
    def act_led_action(request: Request, state: str = Form(...)):
        result = act_led(state)
        _set_notice(
            request,
            result.stdout.strip() or "ACT LED updated.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/logging")
    def logging_action(request: Request, state: str = Form(...)):
        result = logging_state(state)
        _set_notice(
            request,
            result.stdout.strip() or "Logging updated.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/ttyd")
    def ttyd_action_handler(request: Request, state: str = Form(...)):
        result = ttyd_action(state)
        _set_notice(
            request,
            result.stdout.strip() or "ttyd updated.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/ssh-keys")
    def ssh_keys_action(request: Request):
        result = generate_ssh_keys()
        _set_notice(
            request,
            result.stdout.strip() or "SSH keys regenerated.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/system-info")
    def system_info_action(request: Request, section: str = Form("all")):
        section_map = {
            "all": all_system_info,
            "cpu": cpu_info,
            "os": os_info,
            "storage": storage_info,
            "network": networking_info,
            "peripherals": peripherals_info,
        }
        getter = section_map.get(section)
        if not getter:
            return _redirect_to_section(request, "utilities")
        result = getter()
        _set_notice(
            request,
            result.stdout.strip() or "No data available.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/legacy/runonce")
    def legacy_runonce_action(request: Request):
        result = run_first_boot()
        _set_notice(
            request,
            result.stdout.strip() or "First-boot script complete.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/legacy/usb-config")
    def legacy_usb_config_action(request: Request):
        result = run_usb_config_tool()
        _set_notice(
            request,
            result.stdout.strip() or "USB configuration complete.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/legacy/process")
    def legacy_process_action(request: Request):
        result = process_snapshot()
        _set_notice(
            request,
            result.stdout.strip() or "No process data.",
            "error" if result.returncode else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/legacy/luckfox")
    def legacy_luckfox_action(request: Request):
        if legacy_tool_command(["luckfox-config", "raspi-config"]):
            return _interactive_notice(request, "luckfox-config", section="utilities")
        _set_notice(request, "luckfox-config not found.", "error", section="utilities")
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/help/license")
    def help_license_action(request: Request, kind: str = Form(...)):
        kind = kind.strip()
        result = license_info(kind)
        _set_notice(request, result.stdout.strip() or "No license data.", "error" if result.returncode else "info", section="help")
        return _redirect_to_section(request, "help")

    @app.post("/actions/help/pinout")
    def help_pinout_action(request: Request, kind: str = Form(...)):
        kind = kind.strip()
        result = pinout_info(kind)
        _set_notice(request, result.stdout.strip() or "No pinout data.", "error" if result.returncode else "info", section="help")
        return _redirect_to_section(request, "help")

    @app.post("/actions/web-ui")
    def web_ui_action(request: Request, action: str = Form(...)):
        socket_name = "mpwrd-config-web.socket"
        service_name = "mpwrd-config-web.service"
        if action == "install":
            code, output = _install_web_ui_units()
            _set_notice(request, output, "error" if code else "info", section="utilities")
            return _redirect_to_section(request, "utilities")

        if not _unit_file_exists(socket_name) or not _unit_file_exists(service_name):
            code, output = _install_web_ui_units()
            _set_notice(request, output, "error" if code else "info", section="utilities")

        results = []
        if action == "status":
            results.append(system_service_status(socket_name))
            results.append(system_service_status(service_name))
        elif action == "start":
            results.append(system_service_action(socket_name, "start"))
            results.append(system_service_action(service_name, "start"))
        elif action == "stop":
            results.append(system_service_action(service_name, "stop"))
            results.append(system_service_action(socket_name, "stop"))
        elif action == "restart":
            results.append(system_service_action(socket_name, "restart"))
            results.append(system_service_action(service_name, "stop"))
        elif action == "enable":
            results.append(system_service_action(socket_name, "enable"))
            results.append(system_service_action(service_name, "enable"))
        elif action == "disable":
            results.append(system_service_action(service_name, "disable"))
            results.append(system_service_action(socket_name, "disable"))
            results.append(system_service_action(service_name, "stop"))
            results.append(system_service_action(socket_name, "stop"))
        else:
            return _redirect_to_section(request, "utilities")

        output = "\n".join(r.stdout.strip() for r in results if r and r.stdout.strip())
        _set_notice(
            request,
            output or f"Web UI action '{action}' executed.",
            "error" if any(r.returncode for r in results if r) else "info",
            section="utilities",
        )
        return _redirect_to_section(request, "utilities")

    @app.post("/actions/timezone")
    def timezone_action(request: Request, timezone: str = Form(...)):
        result = set_timezone(timezone)
        _set_notice(
            request,
            result.stdout.strip() or f"Timezone set to {timezone}.",
            "error" if result.returncode else "info",
            section="time",
        )
        return _redirect_to_section(request, "time")

    @app.post("/actions/time")
    def time_action(request: Request, date_value: str = Form(...), time_value: str = Form(...)):
        if not date_value or not time_value:
            _set_notice(request, "Date and time are required.", "error", section="time")
            return _redirect_to_section(request, "time")
        timespec = f"{date_value} {time_value}"
        result = set_time(timespec)
        _set_notice(
            request,
            result.stdout.strip() or "System time updated.",
            "error" if result.returncode else "info",
            section="time",
        )
        return _redirect_to_section(request, "time")

    @app.post("/actions/system/reboot")
    def system_reboot_action(request: Request, confirm: str = Form("")):
        if confirm.strip().lower() != "reboot":
            _set_notice(request, "Confirmation required to reboot.", "error", section="system-actions")
            return _redirect_to_section(request, "system-actions")
        result = system_reboot()
        _set_notice(
            request,
            result.stdout.strip() or "Reboot requested.",
            "error" if result.returncode else "info",
            section="system-actions",
        )
        return _redirect_to_section(request, "system-actions")

    @app.post("/actions/system/shutdown")
    def system_shutdown_action(request: Request, confirm: str = Form("")):
        if confirm.strip().lower() != "shutdown":
            _set_notice(request, "Confirmation required to shut down.", "error", section="system-actions")
            return _redirect_to_section(request, "system-actions")
        result = system_shutdown()
        _set_notice(
            request,
            result.stdout.strip() or "Shutdown requested.",
            "error" if result.returncode else "info",
            section="system-actions",
        )
        return _redirect_to_section(request, "system-actions")

    @app.post("/actions/wifi-mesh/sync")
    def wifi_mesh_sync_action(request: Request):
        result = wifi_mesh_sync()
        _set_notice(
            request,
            result.stdout.strip() or "Wi-Fi mesh sync complete.",
            "error" if result.returncode else "info",
            section="networking",
            sub="wifi-mesh",
        )
        return _redirect_to_section(request, "networking")

    @app.post("/actions/software/{key}/{action}")
    def software_action(request: Request, key: str, action: str):
        action_map = {
            "install": "-i",
            "uninstall": "-u",
            "upgrade": "-g",
            "init": "-a",
            "run": "-l",
        }
        flag = action_map.get(action)
        if not flag:
            return _redirect_to_section(request, "software")
        result = run_action(key, flag, interactive=False)
        output = result.output.strip() if result.output else ""
        if result.user_message:
            output = f"{output}\n\n{result.user_message}".strip()
        _set_notice(
            request,
            output or f"{key} {action} complete.",
            "error" if result.returncode else "info",
            section="software",
        )
        return _redirect_to_section(request, "software")

    @app.post("/actions/software/{key}/extra")
    def software_extra_action(request: Request, key: str, action: str = Form(...)):
        if not action:
            return _redirect_to_section(request, "software")
        result = run_action(key, f"-{action}", interactive=False)
        output = result.output.strip() if result.output else ""
        if result.user_message:
            output = f"{output}\n\n{result.user_message}".strip()
        _set_notice(
            request,
            output or f"{key} {action} complete.",
            "error" if result.returncode else "info",
            section="software",
        )
        return _redirect_to_section(request, "software")

    @app.post("/actions/software/{key}/service")
    def software_service_action(request: Request, key: str, action: str = Form(...)):
        action_map = {
            "status": "-S",
            "detailed": "-S",
            "enable": "-e",
            "disable": "-d",
            "stop": "-s",
            "restart": "-r",
            "start": "-r",
        }
        flag = action_map.get(action)
        if not flag:
            return _redirect_to_section(request, "software")
        result = package_service_action(key, flag)
        _set_notice(
            request,
            result.stdout.strip() or f"{key} {action} complete.",
            "error" if result.returncode else "info",
            section="software",
        )
        return _redirect_to_section(request, "software")

    @app.post("/actions/software/{key}/license")
    def software_license_action(request: Request, key: str):
        text = license_text(key)
        _set_notice(request, text or "No license available.", "info", section="software")
        return _redirect_to_section(request, "software")

    @app.post("/actions/wizard")
    def wizard_action(
        request: Request,
        timezone: str = Form(""),
        date_value: str = Form(""),
        time_value: str = Form(""),
        hostname: str = Form(""),
        wifi_ssid: str = Form(""),
        wifi_psk: str = Form(""),
        wifi_country: str = Form(""),
        radio_model: str = Form(""),
        config_url: str = Form(""),
        private_key: str = Form(""),
        public_key: str = Form(""),
    ):
        config = _get_config()
        outputs: list[str] = []
        has_error = False

        if timezone.strip():
            result = set_timezone(timezone.strip())
            outputs.append(result.stdout.strip() or f"Timezone set to {timezone.strip()}.")
            has_error = has_error or result.returncode != 0

        if date_value.strip() and time_value.strip():
            timespec = f"{date_value.strip()} {time_value.strip()}"
            result = set_time(timespec)
            outputs.append(result.stdout.strip() or "System time updated.")
            has_error = has_error or result.returncode != 0

        if hostname.strip():
            config.networking.hostname = hostname.strip()
            _save_config(config)
            result = set_hostname(hostname.strip())
            outputs.append(result.stdout.strip() or f"Hostname set to {hostname.strip()}.")
            has_error = has_error or result.returncode != 0

        if wifi_ssid.strip():
            if not wifi_psk.strip():
                outputs.append("Wi-Fi password required to update credentials.")
                has_error = True
            else:
                updated = False
                for network in config.networking.wifi:
                    if network.ssid == wifi_ssid.strip():
                        network.psk = wifi_psk.strip()
                        updated = True
                        break
                if not updated:
                    config.networking.wifi.append(WifiNetwork(ssid=wifi_ssid.strip(), psk=wifi_psk.strip()))
                if wifi_country.strip():
                    config.networking.country_code = wifi_country.strip()
                config.networking.wifi_enabled = True
                _save_config(config)
                result = set_wifi_credentials(
                    wifi_ssid.strip(),
                    wifi_psk.strip(),
                    wifi_country.strip() or None,
                    interface=config.networking.wifi_interface,
                )
                outputs.append(result.stdout.strip() or "Wi-Fi settings updated.")
                has_error = has_error or result.returncode != 0

        if radio_model.strip():
            result = set_radio(radio_model.strip())
            outputs.append(result.stdout.strip() or f"Radio set to {radio_model.strip()}.")
            has_error = has_error or result.returncode != 0

        if config_url.strip():
            result = set_config_url(config_url.strip())
            outputs.append(result.stdout.strip() or "Meshtastic config URL updated.")
            has_error = has_error or result.returncode != 0

        if private_key.strip():
            result = set_private_key(private_key.strip())
            outputs.append(result.stdout.strip() or "Private key updated.")
            has_error = has_error or result.returncode != 0

        if public_key.strip():
            result = set_public_key(public_key.strip())
            outputs.append(result.stdout.strip() or "Public key updated.")
            has_error = has_error or result.returncode != 0

        if not outputs:
            outputs.append("No wizard changes requested.")
        _set_notice(request, "\n\n".join(outputs), "error" if has_error else "info", section="wizard")
        return _redirect_to_section(request, "wizard")

    return app


app = create_app()
