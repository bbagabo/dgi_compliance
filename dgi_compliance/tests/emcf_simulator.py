"""In-process simulator of the DGI e-MCF API (API e-MCF v1.3) for automated tests.

It replaces `requests.request` inside dgi_compliance.edef.client and reproduces the documented
behaviour: pending requests (max 10, error 63 on a duplicate invoice number), recomputation of
the amounts per tax group, CONFIRM checks (errors 50/51), CANCEL, credit-note reference check,
status / information endpoints. Failures can be injected per call (timeout, HTTP 500, 401,
invalid JSON, response lost after the request was applied, tampered amounts).
"""
import json
import random
import string
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING
from unittest import mock

import requests

RATES = {"A": 0, "B": 16, "C": 5, "D": 0, "E": 0, "F": 16, "G": 5, "H": 0, "I": 0, "J": 0, "K": 0,
         "L": 0, "M": 0, "N": 0, "O": 1, "P": 1, "Q": 8, "R": 8, "S": 0}
NIM = "CD01005082-1"


def _r2(v):
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _ceil2(v):
    return float(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_CEILING))


class FakeResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.ok = 200 <= status < 400
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class EMCFSimulator:
    def __init__(self):
        self.pending = {}        # uid -> {"rn", "request", "totals"}
        self.registered = {}     # code_def -> {"rn", "type", "total", "items"}
        self.calls = []
        self.faults = []         # list of (matcher, fault) consumed in order
        self.tamper_total = 0.0
        self.counter = 0
        self.patcher = None

    # ---------------- control ----------------
    def start(self):
        self.patcher = mock.patch("dgi_compliance.edef.client.requests.request", side_effect=self.handle)
        self.patcher.start()
        return self

    def stop(self):
        if self.patcher:
            self.patcher.stop()
            self.patcher = None

    def inject(self, method, path_part, fault):
        """fault in: timeout | http500 | http401 | invalid_json | lost (applied, then timeout)."""
        self.faults.append((method.upper(), path_part, fault))

    def _take_fault(self, method, path):
        for i, (m, part, fault) in enumerate(self.faults):
            if m == method and part in path:
                return self.faults.pop(i)[2]
        return None

    def count(self, method, part):
        return sum(1 for c in self.calls if c[0] == method and part in c[1])

    # ---------------- arithmetic (spec SFE 1.5) ----------------
    def compute(self, req):
        mode = (req.get("mode") or "ttc").lower()
        acc = {}
        for it in req.get("items") or []:
            g = it["taxGroup"]
            a = acc.setdefault(g, {"amount": 0.0, "ts": 0.0})
            a["amount"] = _r2(a["amount"] + _r2(float(it["price"]) * float(it["quantity"])))
            a["ts"] = _r2(a["ts"] + float(it.get("taxSpecificAmount") or 0))
        out = {"total": 0.0, "vtotal": 0.0, "ts": 0.0}
        for letter in "abcdefghijklmnopqrs":
            out["t" + letter] = RATES[letter.upper()]
        for g, a in acc.items():
            r = RATES[g]
            if mode == "ttc":
                base = a["amount"] + _r2(a["ts"] * (1 + r / 100.0))
                total = _r2(base)
                vat = total if g == "N" else (_ceil2(total * r / (100 + r)) if r else 0.0)
                ht = _r2(total - vat)
            else:
                ht = _r2(a["amount"] + a["ts"])
                vat = ht if g == "N" else (_ceil2(ht * r / 100.0) if r else 0.0)
                total = _r2(ht + vat)
                if g == "N":
                    total, ht = ht, 0.0
            low = g.lower()
            out["ta" + low], out["ha" + low], out["va" + low] = total, ht, vat
            out["total"] = _r2(out["total"] + total)
            out["vtotal"] = _r2(out["vtotal"] + vat)
            out["ts"] = _r2(out["ts"] + a["ts"])
        if req.get("curRate"):
            out["curTotal"] = _r2(out["total"] / float(req["curRate"]))
        return out

    # ---------------- validation ----------------
    def _error(self, code, desc):
        return FakeResponse(200, {"uid": None, "errorCode": str(code), "errorDesc": desc})

    def _validate(self, req):
        items = req.get("items") or []
        if not items:
            return self._error(8, "La facture doit contenu les articles")
        if not req.get("isf"):
            return self._error(32, "ISF ne peut pas etre vide")
        if req.get("mode") not in ("ttc", "ht"):
            return self._error(33, "Mode de prix doit etre ttc ou ht")
        if not (req.get("operator") or {}).get("name"):
            return self._error(13, "Les informations de l'operateur sont manquantes")
        for it in items:
            if it.get("taxGroup") not in RATES:
                return self._error(9, "La groupe de taxation au niveau des articles n'est pas valide")
            if not (it.get("name") or "").strip():
                return self._error(15, "Le nom de l'article ne peut pas etre vide")
            if float(it["price"]) < 0 or float(it["quantity"]) < 0:
                return self._error(18, "Quantite ou prix negatif non autorise")
            if float(it["price"]) == 0 or float(it["quantity"]) == 0:
                return self._error(19, "Quantite et prix ne peuvent pas etre 0")
            if it.get("taxSpecificAmount") is not None and not it.get("taxSpecificValue"):
                return self._error(22, "taxSpecificValue obligatoire")
        for p in req.get("payment") or []:
            if p.get("name") not in ("ESPECES", "VIREMENT", "CARTEBANCAIRE", "MOBILEMONEY", "CHEQUES", "CREDIT", "AUTRE"):
                return self._error(7, "Le type de paiement n'est pas valide")
        if req.get("type") in ("FA", "EA"):
            ref = req.get("reference")
            if not ref:
                return self._error(4, "La reference de la facture originale est manquante")
            if ref != "RRR":
                orig = self.registered.get(ref)
                if not orig:
                    return self._error(11, "Erreur de validation de la facture d'avoir")
        return None

    # ---------------- dispatcher ----------------
    def handle(self, method, url, headers=None, data=None, timeout=None, **kw):
        path = url.split("/edef", 1)[-1] if "/edef" in url else url.split(".cd", 1)[-1]
        method = method.upper()
        self.calls.append((method, path, data))
        fault = self._take_fault(method, path)
        if fault == "timeout":
            raise requests.Timeout("simulated timeout")
        if fault == "http500":
            return FakeResponse(500, {"message": "Internal Server Error"})
        if fault == "http401":
            return FakeResponse(401, "")
        if fault == "invalid_json":
            return FakeResponse(200, "<html>proxy error</html>")
        if not (headers or {}).get("Authorization", "").strip():
            return FakeResponse(401, "")
        body = json.loads(data) if data else None
        resp = self._route(method, path, body)
        if fault == "lost":
            raise requests.ConnectionError("simulated connection reset after processing")
        return resp

    def _route(self, method, path, body):
        if method == "GET" and path.rstrip("/") == "/api/info/status":
            return FakeResponse(200, {
                "status": True, "version": "1.3", "nif": "A0700190Q", "nim": NIM,
                "tokenValid": "2027-06-30T23:59:59+01:00", "serverDateTime": datetime.now().isoformat(),
                "emcfList": [{"nim": NIM, "status": "Actif", "shopName": "MAGASIN GOMBE",
                              "address1": "19284/4b, Avenue des Poids Lourds", "address2": "Kingabwa/Limete",
                              "address3": "Kinshasa", "contact1": "+243815105198",
                              "contact2": "shop@dgitest.cd", "contact3": ""}]})
        if method == "GET" and path.rstrip("/") == "/api/info/taxGroups":
            return FakeResponse(200, {k.lower(): v for k, v in RATES.items()})
        if method == "GET" and path.rstrip("/") == "/api/info/currencyRates":
            return FakeResponse(200, [{"type": "USD", "description": "Dollar americain",
                                       "date": "2026-10-17T08:00:00+01:00", "rate": 2850.0}])
        for cat in ("invoiceTypes", "paymentTypes", "clientTypes", "referenceTypes", "itemTypes"):
            if method == "GET" and path.rstrip("/") == f"/api/info/{cat}":
                return FakeResponse(200, [])
        if path.rstrip("/") == "/api/invoice":
            if method == "GET":
                return FakeResponse(200, {
                    "status": True, "version": "1.3", "nif": "A0700190Q", "nim": NIM,
                    "pendingRequestsCount": len(self.pending),
                    "pendingRequestsList": [{"date": datetime.now().isoformat(), "uid": u} for u in self.pending]})
            if method == "POST":
                return self._create(body)
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "invoice":
            uid = parts[2]
            if method == "GET" and len(parts) == 3:
                p = self.pending.get(uid)
                if not p:
                    return self._error(20, "La facture n'existe pas ou elle est deja finalisee / annulee")
                return FakeResponse(200, {**p["request"], "totals": {**p["totals"], "uid": uid}})
            if method == "PUT" and len(parts) == 4:
                action = parts[3].upper()
                if action == "CONFIRM":
                    return self._confirm(uid, body or {})
                if action == "CANCEL":
                    if uid not in self.pending:
                        return self._error(20, "La facture n'existe pas ou elle est deja finalisee / annulee")
                    self.pending.pop(uid)
                    return FakeResponse(200, {"dateTime": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                                              "nim": NIM})
        return FakeResponse(404, {"message": "not found"})

    def _create(self, req):
        err = self._validate(req)
        if err:
            return err
        for uid, p in self.pending.items():
            if p["rn"] and p["rn"] == req.get("rn"):
                return self._error(63, f"La facture existe deja dans les factures en attente avec numero "
                                       f"de la facture '{req.get('rn')}' uid '{uid}'")
        if len(self.pending) >= 10:
            return self._error(1, "Le nombre maximum de factures en attente est depasse")
        totals = self.compute(req)
        if self.tamper_total:
            totals["total"] = _r2(totals["total"] + self.tamper_total)
        uid = str(uuid.uuid4())
        self.pending[uid] = {"rn": req.get("rn"), "request": req, "totals": totals}
        return FakeResponse(200, {"uid": uid, **totals})

    def _confirm(self, uid, body):
        p = self.pending.get(uid)
        if not p:
            return self._error(20, "La facture n'existe pas ou elle est deja finalisee / annulee")
        if abs(float(body.get("total") or 0) - p["totals"]["total"]) > 0.001:
            return self._error(50, "Le montant TTC declare ne correspond pas a la valeur calculee")
        if abs(float(body.get("vtotal") or 0) - p["totals"]["vtotal"]) > 0.001:
            return self._error(51, "Le montant TVA declare ne correspond pas a la valeur calculee")
        self.pending.pop(uid)
        self.counter += 1
        rnd = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(16))
        code = "TEST-FACT-" + "-".join(rnd[i:i + 4] for i in range(0, 16, 4))
        now = datetime.now()
        req = p["request"]
        self.registered[code] = {"rn": p["rn"], "type": req.get("type"), "total": p["totals"]["total"],
                                 "items": req.get("items")}
        return FakeResponse(200, {
            "dateTime": now.strftime("%d/%m/%Y %H:%M:%S"),
            "qrCode": f"RDCDEF01;{NIM};{code.replace('-', '')};A0700190Q;{now:%Y%m%d%H%M%S}",
            "codeDEFDGI": code,
            "counters": f"{self.counter}/{self.counter + 3} {req.get('type')}",
            "nim": NIM,
        })
