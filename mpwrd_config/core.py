from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tempfile
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib
from typing import Any, Dict, List

import tomli_w


DEFAULT_CONFIG_PATH = Path("/etc/mpwrd-config.toml")
LEGACY_CONFIG_PATH = Path("/etc/femto-config.toml")


@dataclass
class WifiNetwork:
    ssid: str
    psk: str

    def to_dict(self) -> Dict[str, Any]:
        return {"ssid": self.ssid, "psk": self.psk}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WifiNetwork":
        return cls(ssid=str(data.get("ssid", "")), psk=str(data.get("psk", "")))


@dataclass
class NetworkingConfig:
    hostname: str = "mpwrd"
    wifi_enabled: bool = False
    country_code: str = "US"
    wifi: List[WifiNetwork] = field(default_factory=list)
    wifi_interface: str | None = None
    ethernet_interface: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "hostname": self.hostname,
            "wifi_enabled": self.wifi_enabled,
            "country_code": self.country_code,
        }
        if self.wifi:
            payload["wifi"] = [network.to_dict() for network in self.wifi]
        if self.wifi_interface:
            payload["wifi_interface"] = self.wifi_interface
        if self.ethernet_interface:
            payload["ethernet_interface"] = self.ethernet_interface
        return payload

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetworkingConfig":
        wifi_entries = [WifiNetwork.from_dict(item) for item in data.get("wifi", [])]
        return cls(
            hostname=str(data.get("hostname", "mpwrd")),
            wifi_enabled=bool(data.get("wifi_enabled", False)),
            country_code=str(data.get("country_code", "US")),
            wifi=wifi_entries,
            wifi_interface=str(data.get("wifi_interface")) if data.get("wifi_interface") else None,
            ethernet_interface=str(data.get("ethernet_interface")) if data.get("ethernet_interface") else None,
        )


@dataclass
class Config:
    networking: NetworkingConfig = field(default_factory=NetworkingConfig)

    def to_dict(self) -> Dict[str, Any]:
        return {"networking": self.networking.to_dict()}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        networking = NetworkingConfig.from_dict(data.get("networking", {}))
        return cls(networking=networking)


def _resolve_config_path(path: Path) -> Path:
    path = Path(path)
    if path == DEFAULT_CONFIG_PATH and not path.exists() and LEGACY_CONFIG_PATH.exists():
        return LEGACY_CONFIG_PATH
    return path


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    path = _resolve_config_path(path)
    if not path.exists():
        return Config()
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return Config.from_dict(payload)


def _serialize_config(config: Config) -> str:
    return tomli_w.dumps(config.to_dict())


def save_config(config: Config, path: Path = DEFAULT_CONFIG_PATH) -> bool:
    path = Path(path)
    content = _serialize_config(config)
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(content)
        temp_name = handle.name
    Path(temp_name).replace(path)
    return True


def config_to_toml(config: Config) -> str:
    return _serialize_config(config)
