"""Gateway configuration (JSON file) and default locations.

Linux  : /etc/dgi-gateway/gateway.json, data in /var/lib/dgi-gateway/
Windows: %PROGRAMDATA%\\DGI Gateway\\gateway.json and data in the same folder
Override with the DGI_GATEWAY_HOME environment variable (tests, portable installs).
"""
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse


class ConfigError(Exception):
    pass


def home_dir() -> str:
    if os.environ.get("DGI_GATEWAY_HOME"):
        return os.environ["DGI_GATEWAY_HOME"]
    if sys.platform == "win32":  # pragma: no cover
        return os.path.join(os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "DGI Gateway")
    return "/var/lib/dgi-gateway"


def config_path() -> str:
    if os.environ.get("DGI_GATEWAY_HOME") or sys.platform == "win32":
        return os.path.join(home_dir(), "gateway.json")
    return "/etc/dgi-gateway/gateway.json"


@dataclass
class GatewayConfig:
    site_url: str = ""
    gateway_id: str = ""
    serial_port: str = ""            # explicit port (exigence 1.23) ; empty = search by NID
    baudrate: int = 115200
    ca_file: str = ""                # internal CA for on-premises sites (never disables TLS checks)
    poll_interval_s: float = 2.0
    heartbeat_interval_s: float = 60.0
    http_timeout_s: float = 20.0
    expected_nid: str = ""           # cached from the server configuration
    expected_nif: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def key_path(self) -> str:
        return os.path.join(home_dir(), "gateway.key")

    @property
    def journal_path(self) -> str:
        return os.path.join(home_dir(), "journal.sqlite3")

    @property
    def status_path(self) -> str:
        return os.path.join(home_dir(), "status.json")

    def validate(self) -> None:
        u = urlparse(self.site_url or "")
        insecure_ok = os.environ.get("DGI_GATEWAY_ALLOW_HTTP_FOR_TESTS") == "1"
        if u.scheme != "https" and not (insecure_ok and u.scheme == "http"):
            raise ConfigError("l'URL du site ERPNext doit commencer par https:// (HTTP clair refuse)")
        if not u.hostname:
            raise ConfigError("URL du site ERPNext invalide")
        if not self.gateway_id:
            raise ConfigError("identifiant de passerelle manquant")

    @classmethod
    def load(cls, path: str | None = None) -> "GatewayConfig":
        path = path or config_path()
        if not os.path.exists(path):
            raise ConfigError(f"configuration absente ({path}) : lancez 'dgi-gateway init'")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: str | None = None) -> str:
        path = path or config_path()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        if sys.platform != "win32":
            os.chmod(path, 0o640)
        return path
