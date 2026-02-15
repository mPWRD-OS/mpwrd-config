from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import json
import os
import pwd
import shutil
import subprocess
import textwrap
import urllib.request

from mpwrd_config.system import CommandResult, TTYD_CERT_PATH, TTYD_KEY_PATH, _run


@dataclass(frozen=True)
class ExtraAction:
    key: str
    label: str
    handler: Callable[[bool], "PackageActionResult"]
    requires_installed: bool = True


@dataclass
class PackageActionResult:
    returncode: int
    output: str
    user_message: str | None


@dataclass(frozen=True)
class PackageSpec:
    key: str
    name: str
    description: str
    options: str
    author: str | None = None
    url: str | None = None
    service_names: tuple[str, ...] = ()
    location: Path | None = None
    license_path: Path | None = None
    license_name: str | None = None
    conflicts: str | None = None
    install: Callable[[bool], PackageActionResult] | None = None
    uninstall: Callable[[bool], PackageActionResult] | None = None
    upgrade: Callable[[bool], PackageActionResult] | None = None
    init: Callable[[bool], PackageActionResult] | None = None
    run: Callable[[bool], PackageActionResult] | None = None
    check_installed: Callable[[], bool] | None = None
    extra_actions: tuple[ExtraAction, ...] = ()


class _ActionLog:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, message: str) -> None:
        message = message.strip()
        if message:
            self._lines.append(message)

    def add_result(self, result: CommandResult, prefix: str | None = None) -> None:
        if prefix:
            self.add(prefix)
        if result.stdout.strip():
            self.add(result.stdout.strip())

    def finish(self, returncode: int, user_message: str | None = None) -> PackageActionResult:
        return PackageActionResult(returncode=returncode, output="\n".join(self._lines).strip(), user_message=user_message)


OPT_ROOT = Path("/opt")
CONTACT_DIR = OPT_ROOT / "contact"
CONTROL_DIR = OPT_ROOT / "control"
MESHING_DIR = OPT_ROOT / "meshing-around"
TC2_DIR = OPT_ROOT / "TC2-BBS-mesh"
TTYD_DIR = OPT_ROOT / "ttyd"


def _run_interactive(command: list[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> CommandResult:
    try:
        result = subprocess.run(
            command,
            check=False,
            text=True,
            env=env,
            cwd=None if cwd is None else str(cwd),
        )
        return CommandResult(returncode=result.returncode, stdout="")
    except FileNotFoundError:
        return CommandResult(returncode=127, stdout=f"command not found: {command[0]}")
    except PermissionError:
        return CommandResult(returncode=126, stdout=f"permission denied: {command[0]}")


def _read_license(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    lines: list[str] = []
    length = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line_len = len(line)
            if length + line_len > limit:
                remaining = limit - length
                if remaining > 0:
                    lines.append(line[:remaining])
                lines.append(f"...\n\nFile truncated, see {path} for complete license.")
                break
            lines.append(line)
            length += line_len
    return "".join(lines).strip()


def _run_required(
    log: _ActionLog,
    command: list[str],
    user_message: str,
    error_token: str | None = None,
) -> bool:
    result = _run(command)
    log.add(f"$ {' '.join(command)}")
    log.add_result(result)
    if result.returncode != 0:
        return False
    if error_token and error_token in result.stdout:
        return False
    return True


def _apt_update(log: _ActionLog) -> bool:
    log.add("apt update can take a long while...")
    return _run_required(
        log,
        ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "update", "-y"],
        "apt update failed. Is internet connected?",
        error_token="Err",
    )


def _apt_install(log: _ActionLog, packages: list[str]) -> bool:
    return _run_required(
        log,
        ["env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "install", "-y", *packages],
        "apt install failed. Is internet connected?",
        error_token="Err",
    )


def _apt_remove(log: _ActionLog, packages: list[str]) -> bool:
    result = _run(["env", "DEBIAN_FRONTEND=noninteractive", "apt", "remove", "-y", *packages])
    log.add(f"$ apt remove -y {' '.join(packages)}")
    log.add_result(result)
    return result.returncode == 0


def _apt_upgrade(log: _ActionLog, packages: list[str]) -> bool:
    return _run_required(
        log,
        ["env", "DEBIAN_FRONTEND=noninteractive", "apt", "upgrade", "-y", *packages],
        "apt upgrade failed. Is internet connected?",
        error_token="Err",
    )


def _dpkg_installed(package: str) -> bool:
    result = _run(["dpkg-query", "-W", "-f=${Status}", package])
    return result.returncode == 0 and "install ok installed" in result.stdout


def _git_clone(log: _ActionLog, repo: str, dest: Path) -> bool:
    if dest.exists():
        log.add(f"{dest} already exists.")
        return False
    return _run_required(log, ["git", "clone", repo, str(dest)], "Git clone failed. Is internet connected?")


def _git_pull(log: _ActionLog, dest: Path) -> bool:
    if not dest.exists():
        log.add(f"{dest} not found.")
        return False
    return _run_required(log, ["git", "-C", str(dest), "pull"], "Git pull failed. Is internet connected?")


def _pip_install_requirements(log: _ActionLog, dest: Path) -> bool:
    req = dest / "requirements.txt"
    if not req.exists():
        log.add(f"Missing requirements: {req}")
        return False
    return _run_required(
        log,
        ["python3", "-m", "pip", "install", "-r", str(req)],
        "pip install failed. Is internet connected?",
    )


def _chown_recursive(log: _ActionLog, dest: Path, user: str | None) -> None:
    if not user:
        log.add("Skipping chown: no suitable user found.")
        return
    try:
        pwd.getpwnam(user)
    except KeyError:
        log.add(f"Skipping chown: user '{user}' not found.")
        return
    result = _run(["chown", "-R", user, str(dest)])
    log.add(f"$ chown -R {user} {dest}")
    log.add_result(result)


def _git_safe_directory(log: _ActionLog, dest: Path) -> None:
    result = _run(["git", "config", "--global", "--add", "safe.directory", str(dest)])
    log.add(f"$ git config --global --add safe.directory {dest}")
    log.add_result(result)


def _write_script(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _primary_user() -> str:
    for key in ("SUDO_USER", "USER"):
        candidate = os.environ.get(key)
        if not candidate:
            continue
        try:
            info = pwd.getpwnam(candidate)
        except KeyError:
            continue
        if info.pw_uid >= 1000 and info.pw_dir.startswith("/home/"):
            return candidate
        if candidate != "root":
            return candidate
    users = sorted(pwd.getpwall(), key=lambda entry: entry.pw_uid)
    for info in users:
        if info.pw_uid >= 1000 and info.pw_dir.startswith("/home/"):
            return info.pw_name
    return "root"


def _install_contact(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_clone(log, "https://github.com/pdxlocations/contact.git", CONTACT_DIR):
        return log.finish(1, "Git clone failed. Is internet connected?")
    if not _pip_install_requirements(log, CONTACT_DIR):
        return log.finish(1, "pip install failed. Is internet connected?")
    _chown_recursive(log, CONTACT_DIR, _primary_user())
    _git_safe_directory(log, CONTACT_DIR)
    wrapper = textwrap.dedent(
        """\
        #!/bin/bash
        export NCURSES_NO_UTF8_ACS=1
        export TERM=xterm-256color
        export LANG=C.UTF-8
        echo "Stopping conflicting services (if any), will restart after exit..."
        if command -v mpwrd-config >/dev/null 2>&1; then
          sudo mpwrd-config software conflicts --action stop
        else
          sudo -E python3 -m mpwrd_config.cli software conflicts --action stop
        fi
        sudo -u "${SUDO_USER:-$(whoami)}" env LANG="$LANG" TERM="$TERM" NCURSES_NO_UTF8_ACS="$NCURSES_NO_UTF8_ACS" python3 /opt/contact/main.py --host
        echo "Restarting conflicting services (if any)..."
        if command -v mpwrd-config >/dev/null 2>&1; then
          sudo mpwrd-config software conflicts --action start
        else
          sudo -E python3 -m mpwrd_config.cli software conflicts --action start
        fi
        """
    )
    _write_script(Path("/usr/local/bin/contact"), wrapper)
    log.add("Created /usr/local/bin/contact shortcut.")
    return log.finish(0, "To launch, run `contact`.")


def _uninstall_contact(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if CONTACT_DIR.exists():
        shutil.rmtree(CONTACT_DIR, ignore_errors=True)
        log.add(f"Removed {CONTACT_DIR}.")
    wrapper = Path("/usr/local/bin/contact")
    if wrapper.exists():
        wrapper.unlink()
        log.add("Removed /usr/local/bin/contact shortcut.")
    return log.finish(0, "All files removed.")


def _upgrade_contact(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_pull(log, CONTACT_DIR):
        return log.finish(1, "Git pull failed. Is internet connected?")
    return log.finish(0)


def _run_contact(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    stop = manage_full_control_conflicts("stop")
    log.add_result(stop, "Stopping conflicting services (if any)...")
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    env = os.environ.copy()
    env.setdefault("NCURSES_NO_UTF8_ACS", "1")
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("LANG", "C.UTF-8")
    cmd = [
        "sudo",
        "-u",
        user,
        "env",
        f"LANG={env['LANG']}",
        f"TERM={env['TERM']}",
        f"NCURSES_NO_UTF8_ACS={env['NCURSES_NO_UTF8_ACS']}",
        "python3",
        str(CONTACT_DIR / "main.py"),
        "--host",
    ]
    result = _run_interactive(cmd, env=env) if interactive else _run(cmd)
    if result.returncode != 0:
        log.add_result(result)
        return log.finish(1)
    start = manage_full_control_conflicts("start")
    log.add_result(start, "Restarting conflicting services (if any)...")
    return log.finish(0)


def _install_control(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_clone(log, "https://github.com/pdxlocations/control.git", CONTROL_DIR):
        return log.finish(1, "Git clone failed. Is internet connected?")
    if not _pip_install_requirements(log, CONTROL_DIR):
        return log.finish(1, "pip install failed. Is internet connected?")
    _chown_recursive(log, CONTROL_DIR, _primary_user())
    _git_safe_directory(log, CONTROL_DIR)
    return log.finish(0, "To launch, go to Meshtastic settings. Launching manually may conflict with full control software.")


def _uninstall_control(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if CONTROL_DIR.exists():
        shutil.rmtree(CONTROL_DIR, ignore_errors=True)
        log.add(f"Removed {CONTROL_DIR}.")
    return log.finish(0, "All files removed.")


def _upgrade_control(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_pull(log, CONTROL_DIR):
        return log.finish(1, "Git pull failed. Is internet connected?")
    return log.finish(0)


def _run_control(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    stop = manage_full_control_conflicts("stop")
    log.add_result(stop, "Stopping conflicting services (if any)...")
    user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "root"
    env = os.environ.copy()
    env.setdefault("NCURSES_NO_UTF8_ACS", "1")
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("LANG", "C.UTF-8")
    cmd = [
        "sudo",
        "-u",
        user,
        "env",
        f"LANG={env['LANG']}",
        f"TERM={env['TERM']}",
        f"NCURSES_NO_UTF8_ACS={env['NCURSES_NO_UTF8_ACS']}",
        "python3",
        str(CONTROL_DIR / "main.py"),
        "--host",
    ]
    result = _run_interactive(cmd, env=env) if interactive else _run(cmd)
    if result.returncode != 0:
        log.add_result(result)
        return log.finish(1)
    start = manage_full_control_conflicts("start")
    log.add_result(start, "Restarting conflicting services (if any)...")
    return log.finish(0)


def _install_meshing(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_clone(log, "https://github.com/spudgunman/meshing-around", MESHING_DIR):
        return log.finish(1, "Git clone failed. Is internet connected?")
    if not _pip_install_requirements(log, MESHING_DIR):
        return log.finish(1, "pip install failed. Is internet connected?")
    if interactive:
        return _init_meshing(interactive)
    return log.finish(
        0,
        "IMPORTANT: To complete installation, run `sudo /opt/meshing-around/install.sh`\n"
        "To change settings, run `sudo nano /opt/meshing-around/config.ini`.",
    )


def _uninstall_meshing(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    for service in ("mesh_bot",):
        log.add_result(_run(["systemctl", "stop", service]))
        log.add_result(_run(["systemctl", "disable", service]))
        service_file = Path("/etc/systemd/system") / f"{service}.service"
        if service_file.exists():
            service_file.unlink()
            log.add(f"Removed {service_file}.")
    log.add_result(_run(["systemctl", "daemon-reload"]))
    log.add_result(_run(["systemctl", "reset-failed"]))
    log.add_result(_run(["gpasswd", "-d", "meshbot", "dialout"]))
    log.add_result(_run(["gpasswd", "-d", "meshbot", "tty"]))
    log.add_result(_run(["gpasswd", "-d", "meshbot", "bluetooth"]))
    log.add_result(_run(["groupdel", "meshbot"]))
    log.add_result(_run(["userdel", "meshbot"]))
    if MESHING_DIR.exists():
        shutil.rmtree(MESHING_DIR, ignore_errors=True)
        log.add(f"Removed {MESHING_DIR}.")
    return log.finish(0, "Service removed, all files deleted.")


def _init_meshing(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not interactive:
        return log.finish(1, "Initialization requires a terminal. Run this from the TUI.")
    result = _run_interactive(["bash", str(MESHING_DIR / "install.sh")])
    if result.returncode != 0:
        return log.finish(1, "Install script failed.")
    return log.finish(0, "To change settings, run `sudo nano /opt/meshing-around/config.ini`.")


def _upgrade_meshing(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_pull(log, MESHING_DIR):
        return log.finish(1, "Git pull failed. Is internet connected?")
    return log.finish(0)


def _install_tc2(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_clone(log, "https://github.com/TheCommsChannel/TC2-BBS-mesh.git", TC2_DIR):
        return log.finish(1, "Git clone failed. Is internet connected?")
    if not _pip_install_requirements(log, TC2_DIR):
        return log.finish(1, "pip install failed. Is internet connected?")
    user = _primary_user()
    _chown_recursive(log, TC2_DIR, user)
    _git_safe_directory(log, TC2_DIR)
    example_config = TC2_DIR / "example_config.ini"
    config = TC2_DIR / "config.ini"
    if example_config.exists() and not config.exists():
        shutil.copy(example_config, config)
    if config.exists():
        content = config.read_text(encoding="utf-8")
        content = content.replace("type = serial", "type = tcp")
        content = content.replace("# hostname = 192.168.x.x", "hostname = 127.0.0.1")
        config.write_text(content, encoding="utf-8")
    service_file = TC2_DIR / "mesh-bbs.service"
    if service_file.exists():
        service_contents = service_file.read_text(encoding="utf-8")
        service_contents = service_contents.replace("pi", user)
        service_contents = service_contents.replace(f"/home/{user}/", "/opt/")
        service_contents = service_contents.replace("/opt/TC2-BBS-mesh/venv/bin/python3", "python")
        service_file.write_text(service_contents, encoding="utf-8")
        shutil.copy(service_file, Path("/etc/systemd/system") / service_file.name)
    log.add_result(_run(["systemctl", "daemon-reload"]))
    log.add_result(_run(["systemctl", "enable", "mesh-bbs.service"]))
    log.add_result(_run(["systemctl", "restart", "mesh-bbs.service"]))
    return log.finish(0, "Installation complete, service launched. To adjust configuration, run `sudo nano /opt/TC2-BBS-mesh/config.ini`.")


def _uninstall_tc2(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    log.add_result(_run(["systemctl", "disable", "mesh-bbs"]))
    log.add_result(_run(["systemctl", "stop", "mesh-bbs"]))
    service_file = Path("/etc/systemd/system/mesh-bbs.service")
    if service_file.exists():
        service_file.unlink()
    log.add_result(_run(["systemctl", "daemon-reload"]))
    if TC2_DIR.exists():
        shutil.rmtree(TC2_DIR, ignore_errors=True)
        log.add(f"Removed {TC2_DIR}.")
    return log.finish(0, "Service removed, all files deleted.")


def _upgrade_tc2(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _git_pull(log, TC2_DIR):
        return log.finish(1, "Git pull failed. Is internet connected?")
    return log.finish(0)


def _install_mosquitto_broker(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _apt_update(log) or not _apt_install(log, ["mosquitto"]):
        return log.finish(1, "apt install failed. Is internet connected?")
    return log.finish(
        0,
        "Installation requires more setup. For a guide, see https://docs.vultr.com/how-to-install-mosquitto-mqtt-broker-on-ubuntu-24-04",
    )


def _uninstall_mosquitto_broker(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    _apt_remove(log, ["mosquitto"])
    log.add_result(_run(["systemctl", "stop", "mosquitto"]))
    log.add_result(_run(["systemctl", "disable", "mosquitto"]))
    return log.finish(0, "Some files may remain. To remove: `sudo apt remove --purge mosquitto -y` then `sudo apt autoremove -y`.")


def _upgrade_mosquitto_broker(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _apt_update(log) or not _apt_upgrade(log, ["mosquitto"]):
        return log.finish(1, "apt upgrade failed. Is internet connected?")
    return log.finish(0)


def _install_mosquitto_client(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _apt_update(log) or not _apt_install(log, ["mosquitto-clients"]):
        return log.finish(1, "apt install failed. Is internet connected?")
    return log.finish(
        0,
        "Installation requires more setup. For a guide, see https://docs.vultr.com/how-to-install-mosquitto-mqtt-broker-on-ubuntu-24-04",
    )


def _uninstall_mosquitto_client(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    _apt_remove(log, ["mosquitto-clients"])
    return log.finish(0, "Some files may remain. To remove: `sudo apt remove --purge mosquitto -y` then `sudo apt autoremove -y`.")


def _upgrade_mosquitto_client(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _apt_update(log) or not _apt_upgrade(log, ["mosquitto-clients"]):
        return log.finish(1, "apt upgrade failed. Is internet connected?")
    return log.finish(0)


def _install_samba(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    user = _primary_user()
    share_path = f"/home/{user}"
    init_msg = "To enable file sharing, run `Initialize` in the mpwrd-config Samba menu to set a Samba password."
    user_message = (
        f"To connect to network share, enter `\\\\{_hostname()}\\\\home` in Windows, "
        f"`smb://{_hostname()}/home` in MacOS or `smbclient //{_hostname()}/home -U {user}` in Linux. "
        f"Default configuration shares {share_path}. Edit `/etc/samba/smb.conf` to add other shares.\n\n"
        "Troubleshooting: if Windows refuses to connect after succeeding previously, hit [win]+R and enter `net use * /delete`."
    )
    if interactive:
        return _init_samba(interactive)
    return log.finish(0, f"IMPORTANT: {init_msg}\n\n{user_message}")


def _init_samba(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not interactive:
        return log.finish(1, "Initialization requires a terminal. Run this from the TUI.")
    user = _primary_user()
    log.add(f"Set user `{user}` login password:")
    result = _run_interactive(["smbpasswd", "-a", user])
    if result.returncode != 0:
        return log.finish(1, "Samba password setup failed.")
    for service in ("smbd", "nmbd"):
        log.add_result(_run(["systemctl", "enable", service]))
        log.add_result(_run(["systemctl", "restart", service]))
    user_message = (
        "Samba initialized, and service enabled and started. "
        f"To connect, enter `\\\\{_hostname()}\\\\home` in Windows, "
        f"`smb://{_hostname()}/home` in MacOS or `smbclient //{_hostname()}/home -U {user}` in Linux."
    )
    return log.finish(0, user_message)


def _uninstall_samba(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    _apt_remove(log, ["samba"])
    return log.finish(0, "Some files may remain. To remove, run `sudo apt remove --purge samba -y` and `sudo apt autoremove -y`.")


def _upgrade_samba(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    if not _apt_update(log) or not _apt_upgrade(log, ["samba"]):
        return log.finish(1, "apt upgrade failed. Is internet connected?")
    return log.finish(0)


def _install_ttyd(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    TTYD_DIR.mkdir(parents=True, exist_ok=True)
    license_url = "https://raw.githubusercontent.com/tsl0922/ttyd/refs/heads/main/LICENSE"
    if not _download_file(license_url, TTYD_DIR / "LICENSE", log):
        return log.finish(1, "Download failed. Is internet connected?")
    download_url = _latest_ttyd_url()
    if not download_url:
        return log.finish(1, "Unable to locate ttyd release URL.")
    if not _download_file(download_url, TTYD_DIR / "ttyd", log):
        return log.finish(1, "Download failed. Is internet connected?")
    (TTYD_DIR / "ttyd").chmod(0o755)
    log.add("Generating SSL keys...")
    key_result = _generate_ttyd_keys()
    if key_result.returncode != 0:
        log.add_result(key_result)
        return log.finish(1, "Failed to generate SSL keys.")
    log.add_result(_run(["systemctl", "enable", "ttyd"]))
    log.add_result(_run(["systemctl", "start", "ttyd"]))
    return log.finish(0, f"ttyd service started and should be available at https://{_hostname()}.local:7681")


def _uninstall_ttyd(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    log.add_result(_run(["systemctl", "disable", "ttyd"]))
    log.add_result(_run(["systemctl", "stop", "ttyd"]))
    if TTYD_DIR.exists():
        shutil.rmtree(TTYD_DIR, ignore_errors=True)
        log.add(f"Removed {TTYD_DIR}.")
    return log.finish(0, "Binary removed. Service disabled, but service file retained. SSL keys retained.")


def _upgrade_ttyd(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    log.add_result(_run(["systemctl", "stop", "ttyd"]))
    download_url = _latest_ttyd_url()
    if not download_url:
        return log.finish(1, "Unable to locate ttyd release URL.")
    if not _download_file(download_url, TTYD_DIR / "ttyd", log):
        return log.finish(1, "Download failed. Is internet connected?")
    log.add_result(_run(["systemctl", "start", "ttyd"]))
    return log.finish(0, "New binary downloaded and service restarted.")


def _latest_ttyd_url() -> str | None:
    try:
        with urllib.request.urlopen("https://api.github.com/repos/tsl0922/ttyd/releases/latest", timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    for asset in payload.get("assets", []):
        url = asset.get("browser_download_url", "")
        if "armhf" in url:
            return url
    return None


def _download_file(url: str, dest: Path, log: _ActionLog) -> bool:
    log.add(f"Downloading {url} -> {dest}")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp, dest.open("wb") as handle:
            handle.write(resp.read())
        return True
    except Exception as exc:
        log.add(str(exc))
        return False


def _generate_ttyd_keys() -> CommandResult:
    if shutil.which("openssl") is None:
        return CommandResult(returncode=1, stdout="openssl not found")
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
            f"/CN={_hostname()}",
            "-addext",
            f"subjectAltName=DNS:{_hostname()}",
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


def _generate_ttyd_keys_action(interactive: bool) -> PackageActionResult:
    log = _ActionLog()
    result = _generate_ttyd_keys()
    log.add_result(result)
    if result.returncode != 0:
        return log.finish(1, "Failed to generate SSL keys.")
    return log.finish(0, "SSL keys generated.")


def _hostname() -> str:
    try:
        return os.uname().nodename
    except AttributeError:
        return "localhost"


CONTACT_SPEC = PackageSpec(
    key="contact_client",
    name="Contact",
    author="pdxlocations",
    description=(
        "A Text-Based Console UI for Meshtastic Nodes. Formerly called Curses Client.\n"
        "After install, run `contact` to launch."
    ),
    url="https://github.com/pdxlocations/contact/",
    options="xiuglNADUOLGTCI",
    location=CONTACT_DIR,
    license_path=CONTACT_DIR / "LICENSE",
    license_name="GPL3",
    conflicts='"Full control" Meshtastic software, such as TC²-BBS and Meshing Around - but only while running.',
    install=_install_contact,
    uninstall=_uninstall_contact,
    upgrade=_upgrade_contact,
    run=_run_contact,
    check_installed=lambda: CONTACT_DIR.exists(),
)

CONTROL_SPEC = PackageSpec(
    key="control_for_meshtastic",
    name="Control for Meshtastic",
    author="pdxlocations",
    description=(
        "Control for Meshtastic is a fully-featured Meshtastic configuration tool made for running in console, utilizing Curses.\n\n"
        "Control is installed by default on Femtofox and is used for Meshtastic configuration in mpwrd-config. Uninstallation is not recommended."
    ),
    url="https://github.com/pdxlocations/control",
    options="hxiuglNADUOLGTIC",
    location=CONTROL_DIR,
    license_path=CONTROL_DIR / "LICENSE",
    license_name="GPL3",
    conflicts='"Full control" Meshtastic software, such as TC²-BBS and Meshing Around - but only while running.',
    install=_install_control,
    uninstall=_uninstall_control,
    upgrade=_upgrade_control,
    run=_run_control,
    check_installed=lambda: CONTROL_DIR.exists(),
)

MESHING_SPEC = PackageSpec(
    key="meshing_around",
    name="Meshing Around",
    author="Spud",
    description=(
        "Meshing Around is a feature-rich bot designed to enhance your Meshtastic network experience with a variety of powerful tools and fun features. "
        "Connectivity and utility through text-based message delivery. Whether you're looking to perform network tests, send messages, or even play games, mesh_bot.py has you covered."
    ),
    url="https://github.com/SpudGunMan/meshing-around",
    options="xiugedsrNADUOSELGTCI",
    service_names=("mesh_bot",),
    location=MESHING_DIR,
    license_path=MESHING_DIR / "LICENSE",
    license_name="GPL3",
    conflicts='TC²-BBS, other "full control" Meshtastic software, Control (only when running).',
    install=_install_meshing,
    init=_init_meshing,
    uninstall=_uninstall_meshing,
    upgrade=_upgrade_meshing,
    check_installed=lambda: MESHING_DIR.exists(),
)

MOSQUITTO_BROKER_SPEC = PackageSpec(
    key="mosquitto_mqtt_broker",
    name="Mosquitto MQTT Broker",
    author="Eclipse Foundation",
    description=(
        "Eclipse Mosquitto is an open source (EPL/EDL licensed) message broker that implements the MQTT protocol versions 5.0, 3.1.1 and 3.1. "
        "Mosquitto is lightweight and is suitable for use on all devices from low power single board computers to full servers.\n\n"
        "The MQTT protocol provides a lightweight method of carrying out messaging using a publish/subscribe model. This makes it suitable for Internet of Things messaging such as with low power sensors or mobile devices such as phones, embedded computers or microcontrollers.\n\n"
        "The Mosquitto project also provides a C library for implementing MQTT clients, and the very popular mosquitto_pub and mosquitto_sub command line MQTT clients.\n\n"
        "Mosquitto is part of the Eclipse Foundation, and is an iot.eclipse.org project. The development is driven by Cedalo."
    ),
    url="https://mosquitto.org/",
    options="xiugedsrNADUOSEGTPCI",
    service_names=("mosquitto",),
    license_path=Path("/usr/share/doc/mosquitto/copyright"),
    license_name="EPL/EDL",
    install=_install_mosquitto_broker,
    uninstall=_uninstall_mosquitto_broker,
    upgrade=_upgrade_mosquitto_broker,
    check_installed=lambda: _dpkg_installed("mosquitto"),
)

MOSQUITTO_CLIENT_SPEC = PackageSpec(
    key="mosquitto_mqtt_client",
    name="Mosquitto MQTT Client",
    author="Eclipse Foundation",
    description=(
        "Eclipse Mosquitto is an open source (EPL/EDL licensed) message broker that implements the MQTT protocol versions 5.0, 3.1.1 and 3.1. "
        "Mosquitto is lightweight and is suitable for use on all devices from low power single board computers to full servers.\n\n"
        "The MQTT protocol provides a lightweight method of carrying out messaging using a publish/subscribe model. This makes it suitable for Internet of Things messaging such as with low power sensors or mobile devices such as phones, embedded computers or microcontrollers.\n\n"
        "The Mosquitto project also provides a C library for implementing MQTT clients, and the very popular mosquitto_pub and mosquitto_sub command line MQTT clients.\n\n"
        "Mosquitto is part of the Eclipse Foundation, and is an iot.eclipse.org project. The development is driven by Cedalo."
    ),
    url="https://mosquitto.org/",
    options="xiugedsrNADUOSEGTPCI",
    license_path=Path("/usr/share/doc/mosquitto-clients/copyright"),
    license_name="EPL/EDL",
    install=_install_mosquitto_client,
    uninstall=_uninstall_mosquitto_client,
    upgrade=_upgrade_mosquitto_client,
    check_installed=lambda: _dpkg_installed("mosquitto-clients"),
)

SAMBA_SPEC = PackageSpec(
    key="samba",
    name="Samba File Sharing",
    author="Software Freedom Conservancy",
    description=(
        "Femtofox comes with Samba preinstalled but disabled. To enable file sharing, run Initialize in the mpwrd-config Samba menu to set a Samba password.\n\n"
        f"To connect to network share, enter `\\\\{_hostname()}\\\\home` in Windows, "
        f"`smb://{_hostname()}/home` in MacOS or `smbclient //{_hostname()}/home -U {_primary_user()}` in Linux. "
        f"Default configuration shares /home/{_primary_user()}. Edit `/etc/samba/smb.conf` to add other shares.\n\n"
        "Troubleshooting: if Windows refuses to connect after succeeding previously, hit [win]+R and enter `net use * /delete`."
    ),
    url="https://www.samba.org/",
    options="xiuagedsrNADUOSEGTPCI",
    service_names=("smbd", "nmbd"),
    license_path=Path("/usr/share/doc/samba/copyright"),
    license_name="GPL3",
    install=_install_samba,
    init=_init_samba,
    uninstall=_uninstall_samba,
    upgrade=_upgrade_samba,
    check_installed=lambda: _dpkg_installed("samba"),
)

TC2_SPEC = PackageSpec(
    key="tc2_bbs",
    name="TC²-BBS",
    author="The Comms Channel",
    description=(
        "The TC²-BBS system integrates with Meshtastic devices. The system allows for message handling, bulletin boards, mail systems, and a channel directory."
    ),
    url="https://github.com/TheCommsChannel/TC2-BBS-mesh",
    options="xiugedsrNADUOSELGTCI",
    service_names=("mesh-bbs",),
    location=TC2_DIR,
    license_path=TC2_DIR / "LICENSE",
    license_name="GPL3",
    conflicts='Meshing Around, other "full control" Meshtastic software, Control (only when running).',
    install=_install_tc2,
    uninstall=_uninstall_tc2,
    upgrade=_upgrade_tc2,
    check_installed=lambda: TC2_DIR.exists(),
)

TTYD_SPEC = PackageSpec(
    key="ttyd",
    name="ttyd Web Terminal",
    author="Shuanglei Tao",
    description=(
        "ttyd is a simple command-line tool for sharing terminal over the web.\n\n"
        "When running, ttyd is available at https://{hostname}.local:7681\n"
        "ttyd is installed and enabled by default on Foxbuntu.\n\n"
        "SSL encryption is provided by keys generated during first-boot or during installation. Your browser may give a warning (net::ERR_CERT_AUTHORITY_INVALID) "
        "about the self-signed encryption certificate. This is normal. In Chromium (Chrome, Edge) click \"Advanced\" and \"Continue to femtofox.local (unsafe)\""
    ).format(hostname=_hostname()),
    url="https://github.com/tsl0922/ttyd",
    options="hxiugedsrNADUOSELGTCIk",
    service_names=("ttyd",),
    location=TTYD_DIR,
    license_path=TTYD_DIR / "LICENSE",
    license_name="MIT",
    install=_install_ttyd,
    uninstall=_uninstall_ttyd,
    upgrade=_upgrade_ttyd,
    check_installed=lambda: TTYD_DIR.exists(),
    extra_actions=(ExtraAction("k", "Generate SSL keys", _generate_ttyd_keys_action),),
)


PACKAGE_SPECS: list[PackageSpec] = [
    CONTACT_SPEC,
    CONTROL_SPEC,
    MESHING_SPEC,
    MOSQUITTO_BROKER_SPEC,
    MOSQUITTO_CLIENT_SPEC,
    SAMBA_SPEC,
    TC2_SPEC,
    TTYD_SPEC,
]

PACKAGE_LOOKUP = {spec.key: spec for spec in PACKAGE_SPECS}


def list_package_specs() -> list[PackageSpec]:
    return list(PACKAGE_SPECS)


def get_package_spec(key: str) -> PackageSpec:
    if key not in PACKAGE_LOOKUP:
        raise KeyError(key)
    return PACKAGE_LOOKUP[key]


def package_license_text(spec: PackageSpec) -> str:
    if spec.license_path:
        text = _read_license(spec.license_path)
        if text:
            return f"Contents of {spec.license_path}:\n\n{text}"
    return ""


def manage_full_control_conflicts(action: str) -> CommandResult:
    if action not in {"stop", "start"}:
        return CommandResult(returncode=1, stdout=f"Unsupported action: {action}")
    lines: list[str] = []
    for spec in list_package_specs():
        if not spec.service_names:
            continue
        if not spec.conflicts or "full control" not in spec.conflicts.lower():
            continue
        if spec.check_installed and not spec.check_installed():
            continue
        for service in spec.service_names:
            enabled = _run(["systemctl", "is-enabled", service]).returncode == 0
            if not enabled:
                continue
            verb = "stop" if action == "stop" else "restart"
            result = _run(["systemctl", verb, service])
            output = result.stdout.strip() or f"{verb} requested"
            lines.append(f"{spec.name}: {output}")
    return CommandResult(returncode=0, stdout="\n".join(lines).strip())
