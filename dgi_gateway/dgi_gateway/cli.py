"""Command line of the gateway.

  dgi-gateway init --site https://client.frappe.cloud --gateway-id GW-LEMBA-01 [--port COM3]
  dgi-gateway discover                 # serial ports answering C1h, with NID and NIF (read only)
  dgi-gateway enroll --code XXXX-XXXX  # key pair + enrolment request (then approval in ERPNext)
  dgi-gateway status                   # MCF state (C1h/C2h) and server configuration
  dgi-gateway run                      # service loop (used by systemd / the Windows service)
  dgi-gateway selftest                 # full normalization on the built-in MCF simulator
"""
import argparse
import json
import logging
import signal
import sys
import tempfile

from . import __version__
from .api_client import ServerClient
from .config import GatewayConfig, config_path
from .device import MCFDevice, SerialTransport, list_serial_ports
from .identity import Identity
from .journal import Journal
from .service import GatewayService, host_info


def _print(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_init(a):
    cfg = GatewayConfig(site_url=a.site, gateway_id=a.gateway_id, serial_port=a.port or "", ca_file=a.ca_file or "")
    cfg.validate()
    print("Configuration ecrite :", cfg.save())


def cmd_discover(_a):
    out = []
    for p in list_serial_ports():
        row = dict(p)
        try:
            dev = MCFDevice(SerialTransport(p["port"]))
            st = dev.status()
            row.update({"mcf": True, "nid": st["nid"], "mid": st["mid"], "nif": st["nif"]})
            dev.close()
        except Exception as e:  # noqa: BLE001
            row.update({"mcf": False, "detail": str(e)[:120]})
        out.append(row)
    _print(out)
    print("La decouverte ne vaut pas autorisation : seul le NID enregistre dans ERPNext est accepte.")


def _device(cfg):
    if cfg.serial_port:
        return MCFDevice(SerialTransport(cfg.serial_port, cfg.baudrate))
    svc = GatewayService(cfg, None, None)
    return svc._open_serial(None, cfg.expected_nid or None)


def cmd_enroll(a):
    cfg = GatewayConfig.load()
    cfg.validate()
    ident = Identity.create(cfg.gateway_id, cfg.key_path, overwrite=a.rotate)
    dev = _device(cfg)
    st = dev.status()
    dev.close()
    server = ServerClient(cfg.site_url, ident, timeout=cfg.http_timeout_s, ca_file=cfg.ca_file)
    res = server.enroll(a.code, ident.public_key_b64, {"mid": st["mid"], "nif": st["nif"]}, host_info())
    print("Empreinte de la cle de ce poste :", ident.fingerprint)
    print("Comparez-la avec celle affichee dans ERPNext avant d'approuver la passerelle.")
    _print(res)


def cmd_status(_a):
    cfg = GatewayConfig.load()
    out = {"version": __version__, "config": config_path(), "gateway_id": cfg.gateway_id}
    try:
        dev = _device(cfg)
        st, srv = dev.status(), dev.server_status()
        out["mcf"] = {k: st[k] for k in ("mid", "nid", "nif", "tc", "dfn", "flags", "invoice_open")}
        out["connexion_dgi"] = srv
        dev.close()
    except Exception as e:  # noqa: BLE001
        out["mcf_error"] = str(e)
    try:
        ident = Identity.load(cfg.gateway_id, cfg.key_path)
        out["fingerprint"] = ident.fingerprint
        out["server"] = ServerClient(cfg.site_url, ident, ca_file=cfg.ca_file).config()
    except Exception as e:  # noqa: BLE001
        out["server_error"] = str(e)
    _print(out)


def cmd_run(_a):
    cfg = GatewayConfig.load()
    cfg.validate()
    ident = Identity.load(cfg.gateway_id, cfg.key_path)
    server = ServerClient(cfg.site_url, ident, timeout=cfg.http_timeout_s, ca_file=cfg.ca_file)
    svc = GatewayService(cfg, server, Journal(cfg.journal_path))

    def _graceful(signum, _frame):
        # finish the current MCF exchange, then stop (systemd stop / Ctrl+C)
        logging.getLogger("dgi_gateway").info("arret demande (signal %s)", signum)
        svc.stop()
    for sig in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, sig):
            signal.signal(getattr(signal, sig), _graceful)
    svc.run_forever()


def cmd_selftest(_a):
    from .fiscalize import Executor
    from .simulator import MCFSimulator
    sim = MCFSimulator(nid="SIM0000001", nif="A0700190Q")
    dev = MCFDevice(sim)
    with tempfile.TemporaryDirectory() as d:
        ex = Executor(dev, Journal(d + "/j.sqlite3"))
        op = {"operation_id": "SELFTEST-1", "lease_token": "t", "device": {"nid": "SIM0000001", "nif": "A0700190Q"},
              "invoice": {"fn": "SELFTEST-0001", "nif": "A0700190Q", "isf": "ABC-DEF-01", "invoice_type": "FV",
                          "pmode": "TTC", "operator": {"id": "1", "name": "Test"}, "client": {"ctype": "PP"},
                          "items": [{"name": "Lait", "itype": "BIE", "tax_group": "B", "tax_rate": 16,
                                     "amount": 1160, "price": 1160, "qty": 1}],
                          "payments": [{"letter": "E", "amount": 1160}], "totals": {"mv": 1160, "mt": 160}}}
        res = ex.execute(op)
    _print({"outcome": res["outcome"], "code_def": res["data"].get("sig"), "qr": res["data"].get("qr")})
    return 0 if res["outcome"] == "normalized" else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dgi-gateway", description="Passerelle fiscale MCF pour ERPNext")
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log-file", help="journal d'execution dans un fichier tournant (service Windows)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--site", required=True)
    p.add_argument("--gateway-id", required=True)
    p.add_argument("--port")
    p.add_argument("--ca-file")
    p.set_defaults(fn=cmd_init)
    sub.add_parser("discover").set_defaults(fn=cmd_discover)
    p = sub.add_parser("enroll")
    p.add_argument("--code", required=True)
    p.add_argument("--rotate", action="store_true")
    p.set_defaults(fn=cmd_enroll)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    a = ap.parse_args(argv)
    handlers = None
    if a.log_file:
        from logging.handlers import RotatingFileHandler
        handlers = [RotatingFileHandler(a.log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return a.fn(a) or 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
