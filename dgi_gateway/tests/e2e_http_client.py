"""End-to-end test over real HTTP: gateway (this package) <-> ERPNext (bench serve) <-> simulated MCF.

Usage (test site only, HTTP allowed for the test with DGI_GATEWAY_ALLOW_HTTP_FOR_TESTS=1):
    python e2e_http_client.py enroll  <site_url> <code>
    python e2e_http_client.py run     <site_url> <cycles>
    python e2e_http_client.py tamper  <site_url>       # signature / replay checks
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dgi_gateway.api_client import ServerClient, ServerError  # noqa: E402
from dgi_gateway.config import GatewayConfig  # noqa: E402
from dgi_gateway.device import MCFDevice  # noqa: E402
from dgi_gateway.identity import Identity  # noqa: E402
from dgi_gateway.journal import Journal  # noqa: E402
from dgi_gateway.service import GatewayService, host_info  # noqa: E402
from dgi_gateway.simulator import MCFSimulator  # noqa: E402

HOME = os.environ.setdefault("DGI_GATEWAY_HOME", os.path.join(tempfile.gettempdir(), "dgi-e2e"))
GW, NID, NIF = "GW-E2E-01", "E2E0000001", "A0700190Q"
STATE = os.path.join(HOME, "sim_state.json")


def sim():
    s = MCFSimulator(nid=NID, nif=NIF, rates={"A": 0, "B": 16, "C": 5, "O": 1, "P": 1})
    if os.path.exists(STATE):
        st = json.load(open(STATE))
        s.tc, s.fvc, s.frc, s.last = st["tc"], st["fvc"], st["frc"], st["last"]
    return s


def save(s):
    json.dump({"tc": s.tc, "fvc": s.fvc, "frc": s.frc, "last": s.last}, open(STATE, "w"))


def main():
    cmd, url = sys.argv[1], sys.argv[2]
    cfg = GatewayConfig(site_url=url, gateway_id=GW)
    cfg.validate()
    if cmd == "enroll":
        ident = Identity.create(GW, cfg.key_path, overwrite=True)
        dev = MCFDevice(sim())
        st = dev.status()
        res = ServerClient(url, ident).enroll(sys.argv[3], ident.public_key_b64, {"mid": st["mid"], "nif": st["nif"]}, host_info())
        print("ENROLL", res, "local fingerprint", ident.fingerprint)
        cfg.save()
        return
    ident = Identity.load(GW, cfg.key_path)
    server = ServerClient(url, ident)
    if cmd == "run":
        s = sim()
        svc = GatewayService(cfg, server, Journal(cfg.journal_path), device_factory=lambda port, nid: MCFDevice(s))
        print("CONFIG", svc.refresh_config())
        for _ in range(int(sys.argv[3])):
            svc.run_once()
        save(s)
        print("SIM_INVOICES", [(i["fn"], i["sig"]) for i in s.invoices])
        print("STATE", svc.state)
        return
    if cmd == "tamper":
        # 1) replay: same headers and body sent twice
        captured = {}

        def opener_once(method, path, headers, body):
            captured.update(headers=headers, body=body, path=path)
            import urllib.request
            req = urllib.request.Request(url + path, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                return e.code, e.read()
        c = ServerClient(url, ident, opener=opener_once)
        c.config()
        try:
            c._opener("POST", captured["path"], captured["headers"], captured["body"])
            status, raw = c._opener("POST", captured["path"], captured["headers"], captured["body"])
            print("REPLAY", status, raw[:160])
        except Exception as e:  # noqa: BLE001
            print("REPLAY exception", e)
        # 2) tampered body with a valid signature of another body
        h = ident.sign_headers("POST", "/api/method/dgi_compliance.gateway.api.lease", b"{}")
        status, raw = opener_once("POST", "/api/method/dgi_compliance.gateway.api.lease", h, b'{"x":1}')
        print("TAMPER", status, raw[:160])
        # 3) unknown key (another machine pretending to be the gateway)
        other = Identity.create(GW, os.path.join(HOME, "intrus.key"), overwrite=True)
        try:
            ServerClient(url, other).lease()
            print("INTRUS accepted ?!")
        except ServerError as e:
            print("INTRUS", e.status, str(e)[:120])


if __name__ == "__main__":
    main()
