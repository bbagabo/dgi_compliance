"""HTTPS client of the gateway towards ERPNext (dgi_compliance.gateway.api).

Only OUTBOUND calls. TLS certificate checks are always on (an internal CA can be added for
on-premises sites with ca_file). Every call except `enroll` is signed (see signing.py).
"""
import json
import logging
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse

log = logging.getLogger("dgi_gateway.api")
API_PREFIX = "/api/method/dgi_compliance.gateway.api."


class ServerError(Exception):
    def __init__(self, status: int, message: str, exc_type: str = ""):
        self.status, self.exc_type = status, exc_type
        super().__init__(f"HTTP {status} : {message}")


class Unreachable(Exception):
    """ERPNext cannot be reached (network down, DNS, timeout)."""


class ServerClient:
    def __init__(self, site_url: str, identity=None, *, timeout: float = 20.0, ca_file: str = "", opener=None):
        self.base = site_url.rstrip("/")
        self.identity = identity
        self.timeout = timeout
        self._ctx = ssl.create_default_context(cafile=ca_file or None)
        self._opener = opener                        # tests: callable(method, path, headers, body) -> (status, bytes)

    def _call(self, name: str, payload: dict | None = None, *, signed: bool = True) -> dict:
        path = API_PREFIX + name
        body = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": "dgi-gateway"}
        if signed:
            if not self.identity:
                raise ServerError(0, "identite de passerelle absente")
            headers.update(self.identity.sign_headers("POST", path, body))
        if self._opener:
            status, raw = self._opener("POST", path, headers, body)
        else:
            req = urllib.request.Request(self.base + path, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                raise Unreachable(f"{urlparse(self.base).hostname} injoignable : {e}") from e
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            data = {}
        if status >= 400:
            msg = data.get("exception") or data.get("message") or ""
            if not msg and data.get("_server_messages"):
                try:
                    msg = json.loads(json.loads(data["_server_messages"])[0]).get("message", "")
                except Exception:  # noqa: BLE001
                    msg = str(data.get("_server_messages"))[:300]
            raise ServerError(status, str(msg)[:500] or "refus du serveur", data.get("exc_type") or "")
        return data.get("message") if isinstance(data, dict) else data

    # ------------------------------------------------------------------ endpoints
    def enroll(self, code: str, public_key_b64: str, device: dict, host: dict) -> dict:
        return self._call("enroll", {"gateway_id": self.identity.gateway_id, "code": code,
                                     "public_key": public_key_b64, "device": device, "host": host},
                          signed=False)

    def config(self) -> dict:
        return self._call("config")

    def heartbeat(self, snapshot: dict) -> dict:
        return self._call("heartbeat", {"snapshot": snapshot})

    def lease(self) -> dict:
        return self._call("lease")

    def result(self, result: dict) -> dict:
        return self._call("result", {"result": result})

    def event(self, code: str, message: str, details: dict | None = None) -> dict:
        return self._call("event", {"code": code, "message": message, "details": details or {}})
