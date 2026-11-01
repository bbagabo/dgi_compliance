"""stdio bridge for the Node tests of public/js/dgi_mcf.js: the JS port is checked against the
reference Python implementation (dgi_gateway: protocol, simulator) and the server signature check.

One JSON request per line on stdin, one JSON answer per line on stdout.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(REPO, "dgi_gateway"), REPO]

from dgi_gateway import protocol as P  # noqa: E402
from dgi_gateway.simulator import MCFSimulator  # noqa: E402

# frappe (imported by gateway.auth) writes its logs to ../logs relative to the working directory
import tempfile  # noqa: E402
_tmp = tempfile.mkdtemp(prefix="dgi_js_")
os.makedirs(os.path.join(_tmp, "logs"), exist_ok=True)
os.makedirs(os.path.join(_tmp, "sites"), exist_ok=True)
os.chdir(os.path.join(_tmp, "sites"))

sims = {}


def handle(req):
    op = req["op"]
    if op == "new":
        sims[req["id"]] = MCFSimulator(nid=req.get("nid", "SIM0000001"), nif=req.get("nif", "A0700190Q"),
                                       rates=req.get("rates"), groups=req.get("groups", 16),
                                       subtotal_layout=req.get("layout", "eltrade36"),
                                       fn_sig_order=req.get("fn_sig_order", "sig_fn"),
                                       extra_payment_letters=tuple(req.get("extra_payment_letters") or ()))
        return {"ok": True}
    sim = sims.get(req.get("id"))
    if op == "write":
        try:
            sim.write(bytes.fromhex(req["hex"]))
        except Exception as e:  # noqa: BLE001 (unplugged)
            return {"error": str(e)}
        out = bytes(sim._out)
        sim._out.clear()
        return {"hex": out.hex()}
    if op == "fault":
        value = req["value"]
        if req["name"] == "lose_response_for":
            value = set(value)
        setattr(sim, req["name"], value)
        return {"ok": True}
    if op == "load":
        for k in ("tc", "fvc", "frc", "last"):
            if k in req:
                setattr(sim, k, req[k])
        return {"ok": True}
    if op == "state":
        return {"invoices": [{k: v for k, v in i.items() if k in ("fn", "sig", "type", "tc")} for i in sim.invoices],
                "executed": sim.executed, "tc": sim.tc, "open": sim._inv is not None}
    if op == "py":
        fn = getattr(P, req["fn"])
        try:
            return {"value": fn(*req.get("args", []))}
        except P.ProtocolError as e:
            return {"error": "ProtocolError", "message": str(e)}
    if op == "frame":
        return {"hex": P.build_frame(req["seq"], req["cmd"], req["data"].encode("utf-8")).hex()}
    if op == "verify":
        from dgi_compliance.gateway import auth
        return {"value": auth.verify_signature(req["pub"], "POST", req["path"], req["gw"], req["ts"], req["nonce"],
                                               bytes.fromhex(req["body_hex"]), req["sig"], req["alg"])}
    if op == "fingerprint":
        from dgi_compliance.gateway import signing
        import base64
        return {"value": signing.fingerprint(base64.b64decode(req["pub"]))}
    return {"error": "unknown op"}


for line in sys.stdin:
    try:
        ans = handle(json.loads(line))
    except Exception as e:  # noqa: BLE001
        ans = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(ans, default=list) + "\n")
    sys.stdout.flush()
