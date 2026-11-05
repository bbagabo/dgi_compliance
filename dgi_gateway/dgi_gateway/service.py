"""Main loop of the gateway service.

Order of each cycle (one MCF, one operation at a time):
  1. open the MCF (explicit port, else search by NID among serial ports) and check NID/NIF;
  2. reconcile the journal: an interrupted operation is settled through C1h BEFORE anything else;
  3. deliver the results not yet acknowledged by ERPNext;
  4. heartbeat (C1h/C2h summary, alerts) every heartbeat_interval_s;
  5. lease -> execute -> journal -> result ; the answer may carry the next operation.
While an operation stays uncertain, no new operation is leased (barrier, same rule as ERPNext).
"""
import json
import logging
import os
import platform
import socket
import time
from datetime import datetime, timezone

from . import __version__
from .api_client import ServerError, Unreachable
from .device import LinkError, MCFDevice, SerialTransport, list_serial_ports
from .fiscalize import Executor, WrongDevice

log = logging.getLogger("dgi_gateway.service")


def host_info() -> dict:
    return {"hostname": socket.gethostname(), "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(), "gateway_version": __version__}


class GatewayService:
    def __init__(self, config, server, journal, *, device_factory=None, sleep=time.sleep, clock=time.monotonic):
        self.cfg = config
        self.server = server
        self.journal = journal
        self.device_factory = device_factory or self._open_serial
        self.sleep = sleep
        self.clock = clock
        self.dev = None
        self.executor = None
        self.last_heartbeat = -1e9
        self.state = {"started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "server": "inconnu", "device": "inconnu", "blocked": False}
        self._stop = False

    # ------------------------------------------------------------------ device
    def _open_serial(self, port: str | None, expected_nid: str | None):
        """Explicit port first; otherwise probe every serial port and keep the one whose NID is
        the expected one (never 'the first port that answers')."""
        candidates = [port] if port else [p["port"] for p in list_serial_ports()]
        seen = []
        for name in candidates:
            try:
                dev = MCFDevice(SerialTransport(name, self.cfg.baudrate))
                st = dev.status()
            except (LinkError, Exception) as e:  # noqa: BLE001
                seen.append(f"{name}: {e}")
                continue
            if not expected_nid or st["nid"] == expected_nid:
                return dev
            seen.append(f"{name}: NID {st['nid']}")
            dev.close()
        raise LinkError("MCF attendu introuvable (" + "; ".join(seen or ["aucun port serie"]) + ")")

    def ensure_device(self) -> bool:
        if self.dev is not None:
            return True
        try:
            self.dev = self.device_factory(self.cfg.serial_port or None, self.cfg.expected_nid or None)
            self.executor = Executor(self.dev, self.journal, tolerance=float(self.cfg.extra.get("tolerance", 1.0)),
                                     pending_timeout=float(self.cfg.extra.get("pending_timeout_s", 90)))
            self.state["device"] = f"connecte ({self.dev.port})"
            return True
        except LinkError as e:
            self.state["device"] = f"absent : {e}"
            log.warning("MCF indisponible : %s", e)
            return False

    def drop_device(self):
        if self.dev is not None:
            self.dev.close()
        self.dev, self.executor = None, None

    # ------------------------------------------------------------------ cycle steps
    def reconcile_journal(self) -> bool:
        """True when no operation remains uncertain (leasing allowed)."""
        for entry in self.journal.open_operations():
            op = dict(entry.get("payload") or {}, operation_id=entry["op_id"], lease_token=entry.get("lease_token"))
            res = self.executor.check(op)
            log.info("rapprochement de %s : %s", entry["op_id"], res["outcome"])
            if res["outcome"] == "uncertain":
                self.state["blocked"] = True
                return False
        self.state["blocked"] = False
        return True

    def flush_results(self):
        for entry in self.journal.undelivered():
            try:
                self.server.result(entry["result"])
                self.journal.mark_delivered(entry["op_id"])
            except ServerError as e:
                if e.status in (404, 409, 410):        # unknown or closed on the server: keep locally only
                    log.warning("resultat %s refuse (%s) : conserve dans le journal", entry["op_id"], e)
                    self.journal.mark_delivered(entry["op_id"])
                else:
                    raise

    def heartbeat(self, force=False):
        if not force and self.clock() - self.last_heartbeat < self.cfg.heartbeat_interval_s:
            return
        snap = {"gateway_version": __version__, "host": host_info(), "device_state": self.state["device"],
                "blocked": self.state["blocked"], "open_operations": len(self.journal.open_operations())}
        if self.dev is not None:
            try:
                st = self.dev.status()
                srv = self.dev.server_status()
                snap.update({"mid": st["mid"], "nid": st["nid"], "nif": st["nif"], "tc": st["tc"],
                             "last_invoice": st["dfn"], "last_sig": st["dfs"], "tax_rates": st.get("tax_rates") or {},
                             "flags": st["flags"], "invoice_open": st["invoice_open"],
                             "device_time": st["dt"], "dgi_last_sync": srv["last_sync"],
                             "dgi_pending": srv["pending"], "dgi_sent": srv["sent"], "dgi_state": srv["state"],
                             "port": self.dev.port})
            except LinkError as e:
                snap["device_error"] = str(e)
                self.drop_device()
        snap["gateway_time"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.server.heartbeat(snap)
        self.last_heartbeat = self.clock()

    def process(self, op: dict) -> dict | None:
        if not op or not op.get("operation_id"):
            return None
        dev_info = op.get("device") or {}
        if self.cfg.expected_nid and dev_info.get("nid") and dev_info["nid"] != self.cfg.expected_nid:
            log.error("operation %s pour le NID %s refusee : passerelle liee a %s",
                      op["operation_id"], dev_info["nid"], self.cfg.expected_nid)
            self.server.event("NID_OPERATION_INATTENDU", "operation destinee a un autre MCF", {"op": op["operation_id"]})
            return None
        res = self.executor.execute(op)
        try:
            answer = self.server.result(res) or {}
            self.journal.mark_delivered(op["operation_id"])
        except (ServerError, Unreachable) as e:
            log.warning("resultat %s non livre (%s) : nouvel essai au prochain cycle", op["operation_id"], e)
            return None
        return answer.get("next")

    def refresh_config(self):
        conf = self.server.config() or {}
        if conf.get("status") != "Active":
            raise ServerError(403, f"passerelle {conf.get('status') or 'inconnue'} cote ERPNext")
        nid, nif = conf.get("expected_nid") or "", conf.get("expected_nif") or ""
        changed = (nid != self.cfg.expected_nid) or (nif != self.cfg.expected_nif)
        self.cfg.expected_nid, self.cfg.expected_nif = nid, nif
        # a port typed in ERPNext (DGI Gateway > Port serie) wins; an empty value keeps the port
        # configured on the site with 'dgi-gateway init --port' (or the NID-based discovery)
        if conf.get("serial_port") and conf["serial_port"] != self.cfg.serial_port:
            self.cfg.serial_port, changed = conf["serial_port"], True
        if conf.get("baudrate") and int(conf["baudrate"]) != self.cfg.baudrate:
            self.cfg.baudrate, changed = int(conf["baudrate"]), True
        for k in ("tolerance", "pending_timeout_s"):
            if conf.get(k) is not None:
                self.cfg.extra[k] = conf[k]
        if conf.get("poll_interval_s"):
            self.cfg.poll_interval_s = float(conf["poll_interval_s"])
        if changed:
            self.drop_device()
        return conf

    def run_once(self) -> float:
        """One cycle. Returns the delay before the next one."""
        try:
            if not self.ensure_device():
                self.heartbeat()
                return max(self.cfg.poll_interval_s, 10.0)
            if not self.reconcile_journal():
                self.heartbeat()
                return max(self.cfg.poll_interval_s, 5.0)
            self.flush_results()
            self.heartbeat()
            try:
                self.executor.check_device(self.cfg.expected_nid or None, self.cfg.expected_nif or None)
            except WrongDevice as e:
                self.server.event(e.code, str(e), {"status": {k: e.status.get(k) for k in ("mid", "nif")}})
                self.drop_device()
                return 15.0
            op = self.server.lease() or {}
            worked = False
            while op.get("operation_id"):
                worked = True
                op = self.process(op) or {}
            self.state["server"] = "connecte"
            return 0.2 if worked else self.cfg.poll_interval_s
        except Unreachable as e:
            self.state["server"] = f"injoignable : {e}"
            log.warning("%s", e)
            return 10.0
        except LinkError as e:
            self.state["device"] = f"liaison perdue : {e}"
            self.drop_device()
            return 5.0
        except ServerError as e:
            self.state["server"] = str(e)
            log.error("%s", e)
            return 30.0 if e.status in (401, 403) else 10.0
        finally:
            self.publish_status()

    def publish_status(self):
        try:
            path = self.cfg.status_path
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(self.state, updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               version=__version__), f, ensure_ascii=False, indent=1)
            os.replace(tmp, path)
        except OSError:
            pass

    def stop(self):
        self._stop = True

    def run_forever(self):
        log.info("passerelle %s demarree (version %s)", self.cfg.gateway_id, __version__)
        conf_at = -1e9
        while not self._stop:
            if self.clock() - conf_at > 300:
                try:
                    self.refresh_config()
                    conf_at = self.clock()
                except (ServerError, Unreachable) as e:
                    self.state["server"] = str(e)
                    log.warning("configuration indisponible : %s", e)
                    self.sleep(15)
                    continue
            self.sleep(self.run_once())
        self.drop_device()
