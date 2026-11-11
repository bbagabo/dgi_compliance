"""Integration tests v5.1/v5.2 (HL2) - MCF channel through the site gateways.

Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_hl2_mcf

Two points de vente fiscaux (Lemba, Ngaliema), each with its MCF (protocol simulator of
dgi_gateway) and its gateway (real key pair, real enrolment and approval). The gateway code is
the one shipped in dgi_gateway/ (Executor + journal); only the HTTP transport is replaced by
direct calls to the server functions (the HTTP path is covered by tests/e2e_gateway_http.py).
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.emcf_simulator import EMCFSimulator

_ROOT = os.path.dirname(frappe.get_app_path("dgi_compliance"))
sys.path.insert(0, os.path.join(_ROOT, "dgi_gateway"))

from dgi_gateway.device import MCFDevice  # noqa: E402
from dgi_gateway.fiscalize import Executor  # noqa: E402
from dgi_gateway.identity import Identity  # noqa: E402
from dgi_gateway.journal import Journal  # noqa: E402
from dgi_gateway.simulator import MCFSimulator  # noqa: E402

from dgi_compliance.fiscal import operations as ops  # noqa: E402
from dgi_compliance.fiscal.mcf_payload import MCFPayloadError  # noqa: E402
from dgi_compliance.fiscal import fallback as FB  # noqa: E402
from dgi_compliance.gateway import auth, signing  # noqa: E402

NID_L, NID_N = "CC300L0001", "CC300N0002"
NIF = "A0700190Q"
PVF_L, PVF_N, PVF_E = "HL2 LEMBA", "HL2 NGALIEMA", "HL2 GOMBE EMCF"
PVF_K, NID_K, GW_K = "HL2 KINTAMBO", "CC300K0003", "GW-KINT-01"     # e-MCF principal, MCF de secours
GW_L, GW_N = "GW-LEMBA-01", "GW-NGAL-01"
SERVICE = "Service (poste dedie)"
LINES = [("DGI-B-LAIT", 2, 10000), ("DGI-A-EAU", 3, 1500)]


def _upsert(doctype, name, values):
    if doctype == "DGI Point of Sale" and values.get("primary_channel") and not values.get("priority_reason"):
        values = dict(values, priority_reason="Tests HL2 : canal impose pour le scenario")   # v5.3.0
    if frappe.db.exists(doctype, name):
        doc = frappe.get_doc(doctype, name)
        doc.update(values)
        doc.flags.dgi_channel_switch = True
        doc.save(ignore_permissions=True)
        return doc
    doc = frappe.get_doc(dict(values, doctype=doctype))
    doc.insert(ignore_permissions=True)
    return doc


class SiteGateway:
    """The gateway of one site, in process: real Executor + journal + key, simulated MCF."""

    def __init__(self, gateway_id, nid, tmp):
        self.gateway_id = gateway_id
        self.sim = MCFSimulator(nid=nid, nif=NIF, rates={"A": 0, "B": 16, "C": 5, "O": 1, "P": 1})
        self.dev = MCFDevice(self.sim, response_timeout=0.5)
        self.journal = Journal(os.path.join(tmp, gateway_id + ".sqlite3"))
        self.executor = Executor(self.dev, self.journal, pending_interval=0.01)
        self.identity = Identity.create(gateway_id, os.path.join(tmp, gateway_id + ".key"), overwrite=True)

    @property
    def doc(self):
        return frappe.get_doc("DGI Gateway", self.gateway_id)

    def lease(self):
        return ops.lease_for_gateway(self.doc)

    def send(self, result):
        return ops.apply_result(self.doc, result)

    def cycle(self):
        """lease -> execute on the MCF -> result. Returns (lease message, result, answer)."""
        msg = self.lease()
        if not msg.get("operation_id"):
            return msg, None, None
        res = self.executor.execute(msg)
        return msg, res, self.send(res)


class HL2Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        cls.emcf = EMCFSimulator().start()
        fx.setup_all()
        cls.tmp = tempfile.mkdtemp(prefix="dgi_hl2_")
        s = frappe.get_single("DGI Compliance Settings")
        s.mcf_lease_seconds = 180
        s.mcf_max_attempts = 3
        s.mcf_credit_policy = "Declarer avec la lettre credit du MCF"
        s.mcf_credit_letter = ""
        s.switch_requires_two_people = 0
        s.switch_requires_dgi_reference = 0
        s.station_approval_two_people = 1
        s.emcf_retry_minutes = 5
        s.mcf_totals_tolerance = 1
        s.flags.ignore_mandatory = True
        s.save(ignore_permissions=True)
        cls.sites = {}
        for pvf, nid, gw in ((PVF_L, NID_L, GW_L), (PVF_N, NID_N, GW_N)):
            _upsert("DGI MCF Device", nid, {"nid": nid, "status": "Declare"})
            _upsert("DGI Point of Sale", pvf, {"pos_name": pvf, "company": fx.COMPANY, "is_active": 1,
                                               "is_sales_location": 1, "nid": nid, "primary_channel": "MCF",
                                               "active_channel": "MCF", "mcf_device": nid,
                                               "nid_requirement": "Mandatory", "token_requirement": "Optional"})
            frappe.db.set_value("DGI MCF Device", nid, {"status": "Actif", "point_of_sale": pvf, "last_tc": 0})
            site = SiteGateway(gw, nid, cls.tmp)
            cls._enrol(site, pvf)
            cls.sites[pvf] = site
        # Kintambo: e-MCF principal with its MCF as automatic fallback
        _upsert("DGI MCF Device", NID_K, {"nid": NID_K, "status": "Declare"})
        _upsert("DGI Point of Sale", PVF_K, {"pos_name": PVF_K, "company": fx.COMPANY, "is_active": 1,
                                             "is_sales_location": 1, "nid": NID_K, "primary_channel": "e-MCF",
                                             "active_channel": "e-MCF", "mcf_device": NID_K, "token": "TOKEN-KINT",
                                             "fallback_policy": "Automatique",
                                             "nid_requirement": "Optional", "token_requirement": "Optional"})
        frappe.db.set_value("DGI MCF Device", NID_K, {"status": "Actif", "point_of_sale": PVF_K, "last_tc": 0,
                                                     "tax_rates": None})
        cls.kintambo = SiteGateway(GW_K, NID_K, cls.tmp)
        cls._enrol(cls.kintambo, PVF_K)
        _upsert("DGI Point of Sale", PVF_E, {"pos_name": PVF_E, "company": fx.COMPANY, "is_active": 1,
                                             "is_sales_location": 0, "primary_channel": "e-MCF",
                                             "nid_requirement": "Optional", "token_requirement": "Optional",
                                             "active_channel": "e-MCF", "token": "TOKEN-GOMBE-PVF"})
        frappe.db.commit()

    @classmethod
    def _enrol(cls, site, pvf):
        if frappe.db.exists("DGI Gateway", site.gateway_id):
            gw = frappe.get_doc("DGI Gateway", site.gateway_id)
            if gw.status == "Active":
                from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import revoke_gateway
                revoke_gateway(gw.name, "reinitialisation des tests")
            frappe.db.set_value("DGI Gateway", gw.name, "status", "Nouvelle")
        else:
            frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": site.gateway_id, "kind": SERVICE,
                            "point_of_sale": pvf}).insert(ignore_permissions=True)
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import (
            enroll_gateway, generate_enrollment_code)
        code = generate_enrollment_code(site.gateway_id)["code"]
        st = site.dev.status()
        res = enroll_gateway(site.gateway_id, code, site.identity.public_key_b64,
                             {"mid": st["mid"], "nif": st["nif"]}, {"hostname": "test"})
        assert res["fingerprint"] == site.identity.fingerprint
        fx.approve_as_second_manager(site.gateway_id, site.identity.fingerprint)

    @classmethod
    def tearDownClass(cls):
        cls.emcf.stop()
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        frappe.clear_cache(doctype="DGI Compliance Settings")
        # each test starts with empty queues
        frappe.db.sql("""update `tabDGI Fiscal Operation` set status='Cancelled'
                         where status in ('Queued','Leased','Uncertain','Checking','Dispute')""")
        frappe.cache.delete(FB._key(PVF_K))
        frappe.db.commit()

    # ---------------------------------------------------------------- helpers
    def invoice(self, pvf=PVF_L, lines=LINES, **extra):
        return fx.make_invoice("CLIENT PM SARL", lines, template="DGI TVA HT", dgi_point_of_sale=pvf, **extra)

    def request(self, si):
        from dgi_compliance.edef.tasks import normalize_sales_invoice
        return normalize_sales_invoice(si.name)

    def reload(self, si):
        return frappe.get_doc("Sales Invoice", si.name)

    def op_of(self, si):
        return frappe.get_doc("DGI Fiscal Operation", self.reload(si).custom_dgi_operation)

    def expire(self, op_name):
        frappe.db.set_value("DGI Fiscal Operation", op_name, "lease_expires_at", add_to_date(now_datetime(), seconds=-5))


class TestRoutingAndIsolation(HL2Base):
    def test_H01_lemba_invoice_goes_to_lemba_mcf_only(self):
        L, N = self.sites[PVF_L], self.sites[PVF_N]
        si = self.invoice()
        res = self.request(si)
        self.assertEqual(res["stage"], "queued-mcf")
        op = self.op_of(si)
        self.assertEqual((op.status, op.mcf_device, op.point_of_sale), ("Queued", NID_L, PVF_L))
        self.assertTrue(N.lease().get("idle"))                       # Ngaliema gateway sees nothing
        msg, result, answer = L.cycle()
        self.assertEqual(msg["device"]["nid"], NID_L)
        self.assertEqual(result["outcome"], "normalized", result)
        si = self.reload(si)
        self.assertEqual(si.custom_dgi_status, "Normalized")
        self.assertTrue(si.custom_dgi_nim.startswith(NID_L + "-"))
        self.assertTrue(si.custom_dgi_qr_code.startswith("RDCDEF01;" + si.custom_dgi_nim + ";"))
        self.assertEqual(len(si.custom_dgi_code_def), 29)            # 24 characters printed in groups of 4
        self.assertEqual(si.custom_dgi_channel, "MCF")
        si.submit()
        self.assertEqual(self.reload(si).docstatus, 1)
        self.assertEqual(len(L.sim.invoices), 1)
        self.assertEqual(L.sim.invoices[0]["fn"], si.name)

    def test_H02_submit_before_normalization_queues_and_explains(self):
        si = self.invoice()
        with self.assertRaises(frappe.ValidationError) as cm:
            si.submit()
        self.assertIn("transmise au MCF", str(cm.exception))
        self.assertEqual(self.op_of(si).status, "Queued")          # committed despite the refused submit
        self.sites[PVF_L].cycle()
        si = self.reload(si)
        si.submit()
        self.assertEqual(self.reload(si).custom_dgi_status, "Normalized")

    def test_H03_barrier_one_operation_per_device(self):
        L = self.sites[PVF_L]
        a, b = self.invoice(), self.invoice()
        self.request(a)
        self.request(b)
        m1 = L.lease()
        m2 = L.lease()                                              # same gateway asks again
        self.assertEqual(m1["operation_id"], m2["operation_id"])    # same operation, never the second one
        res = L.executor.execute(m1)
        L.send(res)
        m3 = L.lease()
        self.assertEqual(m3["fn"], b.name)

    def test_H04_foreign_gateway_cannot_settle(self):
        L, N = self.sites[PVF_L], self.sites[PVF_N]
        si = self.invoice()
        self.request(si)
        msg = L.lease()
        res = L.executor.execute(msg)
        with self.assertRaises(frappe.PermissionError):
            N.send(res)
        L.send(res)
        self.assertEqual(self.reload(si).custom_dgi_status, "Normalized")

    def test_H05_result_signed_by_another_mcf_is_disputed(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        msg = L.lease()
        res = L.executor.execute(msg)
        forged = json.loads(json.dumps(res))
        forged["data"]["mid"] = NID_N + "-1"
        out = L.send(forged)
        self.assertEqual(out["status"], "Dispute")
        self.assertEqual(self.reload(si).custom_dgi_status, "Error")
        si2 = self.invoice()
        self.request(si2)
        self.assertTrue(L.lease().get("idle"))                      # dispute blocks the device
        from dgi_compliance.fiscal.api import resolve_dispute
        resolve_dispute(self.op_of(si).name, "Rejected", "Verifie sur le portail DGI : absente")
        self.assertEqual(L.lease()["fn"], si2.name)

    def test_H06_duplicate_result_is_idempotent(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        msg = L.lease()
        res = L.executor.execute(msg)
        L.send(res)
        again = L.send(res)
        self.assertTrue(again.get("duplicate"))


class TestReconciliation(HL2Base):
    def test_H07_lost_result_then_expired_lease_is_recovered_without_double(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        msg = L.lease()
        L.executor.execute(msg)                                     # MCF normalized, result never sent
        self.expire(self.op_of(si).name)
        ops.expire_leases()
        self.assertEqual(self.op_of(si).status, "Uncertain")
        check = L.lease()
        self.assertEqual(check["op_type"], "CHECK")
        fresh = Executor(L.dev, Journal(os.path.join(self.tmp, "lost-journal.sqlite3")), pending_interval=0.01)
        res = fresh.execute(check)                                   # journal lost: C1h decides
        self.assertTrue(res["data"]["recovered"])
        L.send(res)
        self.assertEqual(self.reload(si).custom_dgi_status, "Normalized")
        self.assertEqual(sum(1 for i in L.sim.invoices if i["fn"] == si.name), 1)

    def test_H08_check_proves_non_normalization_and_requeues(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        first = self.op_of(si).name
        L.lease()                                                    # leased, gateway dies before sending anything
        self.expire(first)
        ops.expire_leases()
        check = L.lease()
        res = Executor(L.dev, Journal(os.path.join(self.tmp, "j-h08.sqlite3"))).execute(check)
        self.assertEqual(res["error"]["code"], "NON_NORMALISEE")
        out = L.send(res)
        self.assertTrue(out["requeued"])
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", first, "status"), "Rejected")
        L.cycle()
        si = self.reload(si)
        self.assertEqual(si.custom_dgi_status, "Normalized")
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", si.custom_dgi_operation, "attempt"), 2)

    def test_H09_stale_rejection_after_new_lease_is_ignored(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        msg = L.lease()
        stale = {"operation_id": msg["operation_id"], "lease_token": "ancien-jeton", "outcome": "rejected",
                 "error": {"code": "X", "message": "obsolete", "retryable": True}, "proven_not_normalized": True}
        self.assertTrue(L.send(stale).get("ignored"))
        self.assertEqual(self.op_of(si).status, "Leased")


class TestGuardsAndChannels(HL2Base):
    def test_H10_group_q_needs_an_updated_mcf(self):
        """H6: groups beyond P go to the MCF when the MCF announces them (C1h); refused when it does not."""
        rates_v1 = {k: 0.0 for k in "ABCDEFGHIJKLMNOP"}
        frappe.db.set_value("DGI MCF Device", NID_L, "tax_rates", json.dumps(rates_v1))
        si = self.invoice(lines=[("DGI-Q-BOISSON", 2, 1000)])
        with self.assertRaises(MCFPayloadError):
            self.request(si)
        self.assertEqual(self.reload(si).custom_dgi_status, "Error")
        frappe.db.set_value("DGI MCF Device", NID_L, "tax_rates", json.dumps(dict(rates_v1, Q=8.0, R=0.0, S=0.0)))
        si = self.invoice(lines=[("DGI-Q-BOISSON", 2, 1000)])
        self.assertEqual(self.request(si)["stage"], "queued-mcf")
        payload = json.loads(self.op_of(si).payload)
        self.assertEqual(payload["items"][0]["tax_group"], "Q")
        frappe.db.set_value("DGI MCF Device", NID_L, "tax_rates", None)

    def test_H11_queued_draft_change_cancels_operation_leased_draft_is_frozen(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        si = self.reload(si)
        si.items[0].qty = 5
        si.save()
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", {"sales_invoice": si.name}, "status"), "Cancelled")
        self.request(si)
        L.lease()
        si = self.reload(si)
        si.items[0].qty = 7
        with self.assertRaises(frappe.ValidationError):
            si.save()

    def test_H12_emcf_point_of_sale_uses_its_own_token(self):
        seen = []
        original = self.emcf.handle

        def spy(method, url, headers=None, **kw):
            seen.append((headers or {}).get("Authorization"))
            return original(method, url, headers=headers, **kw)

        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=spy):
            si = self.invoice(pvf=PVF_E)
            res = self.request(si)
        self.assertTrue(res.get("ok"), res)
        self.assertIn("Bearer TOKEN-GOMBE-PVF", seen)
        self.assertNotIn("Bearer " + fx.TOKEN, seen)

    def test_H13_channel_switch_rules(self):
        L = self.sites[PVF_L]
        frappe.db.set_value("DGI Point of Sale", PVF_L, {"switch_allowed": 0, "dgi_switch_reference": None})
        sw = frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_L, "to_channel": "e-MCF",
                             "reason": "Panne MCF"}).insert()
        with self.assertRaises(frappe.ValidationError):
            sw.submit()                                              # not allowed by the DGI yet
        frappe.db.set_value("DGI Point of Sale", PVF_L, {"switch_allowed": 1, "dgi_switch_reference": "DGI/TEST/1"})
        si = self.invoice()
        self.request(si)
        L.lease()                                                    # an operation in flight
        sw = frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_L, "to_channel": "e-MCF",
                             "reason": "Panne MCF"}).insert()
        with self.assertRaises(frappe.ValidationError):
            sw.submit()
        L.send(L.executor.execute(L.lease()))
        queued = self.invoice()
        self.request(queued)
        sw = frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_L, "to_channel": "e-MCF",
                             "reason": "Panne MCF"}).insert()
        sw.submit()
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_L, "active_channel"), "e-MCF")
        self.assertEqual(self.op_of(queued).status, "Cancelled")
        back = frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_L, "to_channel": "MCF",
                               "reason": "Retour au canal principal"}).insert()
        frappe.db.set_value("DGI Gateway", GW_L, "last_seen", now_datetime())
        back.submit()
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_L, "active_channel"), "MCF")

    def test_H14_active_channel_cannot_be_edited_directly(self):
        pvf = frappe.get_doc("DGI Point of Sale", PVF_N)
        pvf.active_channel = "e-MCF"
        with self.assertRaises(frappe.ValidationError):
            pvf.save()


class TestGatewaySecurity(HL2Base):
    def test_H15_signature_and_replay(self):
        L = self.sites[PVF_L]
        path = "/api/method/dgi_compliance.gateway.api.lease"
        body = b"{}"
        h = L.identity.sign_headers("POST", path, body)
        pub = frappe.db.get_value("DGI Gateway", GW_L, "public_key")
        args = (pub, "POST", path, GW_L, h[signing.HEADER_TIMESTAMP], h[signing.HEADER_NONCE])
        self.assertTrue(auth.verify_signature(*args, body, h[signing.HEADER_SIGNATURE]))
        self.assertFalse(auth.verify_signature(*args, b'{"x":1}', h[signing.HEADER_SIGNATURE]))
        other = frappe.db.get_value("DGI Gateway", GW_N, "public_key")
        self.assertFalse(auth.verify_signature(other, *args[1:], body, h[signing.HEADER_SIGNATURE]))
        nonce = "n-" + str(time.time())
        self.assertTrue(auth._claim_nonce(GW_L, nonce))
        self.assertFalse(auth._claim_nonce(GW_L, nonce))            # replay refused

    def test_H16_enrolment_refuses_wrong_device_and_reused_code(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import (
            enroll_gateway, generate_enrollment_code)
        gid = "GW-LEMBA-TEST"
        if not frappe.db.exists("DGI Gateway", gid):
            frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": gid, "kind": SERVICE, "point_of_sale": PVF_L}).insert()
        ident = Identity.create(gid, os.path.join(self.tmp, gid + ".key"), overwrite=True)
        code = generate_enrollment_code(gid)["code"]
        with self.assertRaises(frappe.ValidationError):              # MCF of Ngaliema plugged at Lemba
            enroll_gateway(gid, code, ident.public_key_b64, {"mid": NID_N + "-1", "nif": NIF}, {})
        with self.assertRaises(frappe.AuthenticationError):          # the code was consumed
            enroll_gateway(gid, code, ident.public_key_b64, {"mid": NID_L + "-1", "nif": NIF}, {})
        code = generate_enrollment_code(gid)["code"]
        frappe.db.set_value("DGI Gateway", gid, "enrollment_expires", add_to_date(now_datetime(), minutes=-1))
        with self.assertRaises(frappe.AuthenticationError):          # expired
            enroll_gateway(gid, code, ident.public_key_b64, {"mid": NID_L + "-1", "nif": NIF}, {})
        self.assertNotEqual(frappe.db.get_value("DGI Gateway", gid, "status"), "Active")

    def test_H17_heartbeat_detects_wrong_device(self):
        L = self.sites[PVF_L]
        before = frappe.db.count("DGI Gateway Event", {"event_code": "NID_INATTENDU"})
        ops.record_heartbeat(L.doc, {"mid": NID_N + "-1", "nif": NIF, "dgi_last_sync": "20261030120000",
                                     "dgi_pending": "3", "flags": []})
        self.assertEqual(frappe.db.count("DGI Gateway Event", {"event_code": "NID_INATTENDU"}), before + 1)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_L, "dgi_pending"), 3)


# =============================================================================== v5.2
def _down(*_a, **_k):
    raise requests.ConnectionError("e-MCF injoignable (test)")


class TestFallbackEMCFtoMCF(HL2Base):
    """e-MCF principal, MCF de secours (H1/H2 confirmes)."""

    def request_down(self, si):
        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=_down), \
                mock.patch("dgi_compliance.edef.client.time.sleep"):
            return self.request(si)

    def test_H18_emcf_down_invoice_goes_to_its_mcf_then_back(self):
        K = self.kintambo
        si = self.invoice(pvf=PVF_K)
        res = self.request_down(si)
        self.assertEqual(res["stage"], "queued-mcf", res)
        op = self.op_of(si)
        self.assertEqual((op.channel, op.mcf_device, op.fallback), ("MCF", NID_K, 1))
        self.assertTrue(op.fallback_reason)
        self.assertTrue(FB.is_active(PVF_K))
        self.assertTrue(frappe.db.get_value("DGI Point of Sale", PVF_K, "fallback_since"))
        self.assertTrue(frappe.db.exists("DGI Gateway Event", {"event_code": "SECOURS_MCF", "point_of_sale": PVF_K}))
        # next invoice: straight to the MCF, the e-MCF is not even called
        calls = []
        with mock.patch("dgi_compliance.edef.client.requests.request",
                        side_effect=lambda *a, **k: calls.append(a) or _down()):
            si2 = self.invoice(pvf=PVF_K)
            self.assertEqual(self.request(si2)["stage"], "queued-mcf")
        self.assertEqual(calls, [])
        # the Kintambo station normalizes the first one (never the MCF of another site)
        msg, _res, _answer = K.cycle()
        self.assertEqual(msg["fn"], si.name)
        self.assertEqual(self.reload(si).custom_dgi_status, "Normalized")
        self.assertEqual(self.reload(si).custom_dgi_channel, "MCF")
        self.assertTrue(self.reload(si).custom_dgi_nim.startswith(NID_K))
        # the e-MCF answers again: fallback closed, invoice still waiting goes back to the e-MCF
        self.assertTrue(FB.emcf_available(PVF_K))
        with mock.patch("frappe.enqueue", side_effect=lambda method, **kw: FB.renormalize(kw["invoice"])):
            moved = FB.deactivate(PVF_K, "test")
        self.assertEqual(moved, 1)
        self.assertFalse(FB.is_active(PVF_K))
        si2 = self.reload(si2)
        self.assertEqual(si2.custom_dgi_status, "Normalized")
        self.assertEqual(si2.custom_dgi_channel, "e-MCF")
        self.assertFalse(si2.custom_dgi_nim.startswith(NID_K))

    def test_H19_no_fallback_after_an_unanswered_confirm(self):
        """CONFIRM sent without answer: the e-MCF may have normalized it -> never sent to the MCF."""
        for _i in range(3):
            self.emcf.inject("PUT", "/api/invoice", "timeout")
        with mock.patch("dgi_compliance.edef.client.time.sleep"):
            si = self.invoice(pvf=PVF_K)
            res = self.request(si)
        self.assertFalse(res.get("ok"))
        self.assertIn("INCERTAINE", (self.reload(si).custom_dgi_error or "").upper())
        self.assertFalse(frappe.db.exists("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "MCF"}))
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "e-MCF"},
                                             ["status", "confirm_sent"]), ("Uncertain", 1))          # v5.3 journal
        self.assertFalse(FB.is_active(PVF_K))

    def test_H20_no_fallback_on_a_refusal_of_the_emcf(self):
        from dgi_compliance.tests.emcf_simulator import FakeResponse
        refused = lambda *a, **k: FakeResponse(200, {"errorCode": "9", "errorDesc": "groupe de taxation invalide"})  # noqa: E731
        with mock.patch("dgi_compliance.edef.client.requests.request", side_effect=refused):
            si = self.invoice(pvf=PVF_K)
            res = self.request(si)
        self.assertFalse(res.get("ok"))
        self.assertFalse(frappe.db.exists("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "MCF"}))
        self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "e-MCF"},
                                             "status"), "Rejected")                                   # v5.3 journal
        self.assertFalse(FB.is_active(PVF_K))

    def test_H21_policy_manual_means_no_automatic_fallback(self):
        frappe.db.set_value("DGI Point of Sale", PVF_K, "fallback_policy", "Manuelle")
        frappe.clear_document_cache("DGI Point of Sale", PVF_K)
        try:
            si = self.invoice(pvf=PVF_K)
            res = self.request_down(si)
            self.assertFalse(res.get("ok"))
            self.assertFalse(frappe.db.exists("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "MCF"}))
            self.assertEqual(frappe.db.get_value("DGI Fiscal Operation", {"sales_invoice": si.name, "channel": "e-MCF"},
                                                 ["status", "error_code"]), ("Cancelled", "NON_RECUE"))  # v5.3 journal
        finally:
            frappe.db.set_value("DGI Point of Sale", PVF_K, "fallback_policy", "Automatique")
            frappe.clear_document_cache("DGI Point of Sale", PVF_K)


class TestCreditAndCounters(HL2Base):
    def test_H22_credit_sale_uses_the_credit_letter_of_the_mcf(self):
        """H5: credit sales are declared on the MCF too."""
        from dgi_compliance.fiscal.mcf_payload import CREDIT_AS_OTHER, CREDIT_WITH_LETTER, credit_letter
        s = frappe.get_single("DGI Compliance Settings")
        self.assertRaises(MCFPayloadError, credit_letter, {"mcf_credit_policy": CREDIT_WITH_LETTER, "mcf_credit_letter": ""})
        self.assertEqual(credit_letter({"mcf_credit_policy": CREDIT_WITH_LETTER, "mcf_credit_letter": "r"}), "R")
        self.assertEqual(credit_letter({"mcf_credit_policy": CREDIT_AS_OTHER}), "A")
        old = s.default_payment_type
        frappe.db.set_single_value("DGI Compliance Settings", {"default_payment_type": "CREDIT", "mcf_credit_letter": "R"})
        frappe.clear_cache(doctype="DGI Compliance Settings")
        try:
            si = self.invoice()
            self.request(si)
            payload = json.loads(self.op_of(si).payload)
            self.assertEqual({p["letter"] for p in payload["payments"]}, {"R"})
        finally:
            frappe.db.set_single_value("DGI Compliance Settings", {"default_payment_type": old, "mcf_credit_letter": ""})
            frappe.clear_cache(doctype="DGI Compliance Settings")

    def test_H23_forged_results_are_refused(self):
        L = self.sites[PVF_L]
        si = self.invoice()
        self.request(si)
        queued = self.op_of(si)
        forged = {"operation_id": queued.operation_id, "outcome": "normalized", "lease_token": "x",
                  "data": {"mid": NID_L + "-1", "nif": NIF, "fn": si.name, "sig": "A" * 24, "dt": "20261031120000",
                           "tc": "999", "ft": "FV"}}
        before = frappe.db.count("DGI Gateway Event", {"event_code": "RESULTAT_SANS_BAIL"})
        with self.assertRaises(frappe.PermissionError):              # never leased: refused
            L.send(forged)
        self.assertEqual(frappe.db.count("DGI Gateway Event", {"event_code": "RESULTAT_SANS_BAIL"}), before + 1)
        self.assertEqual(self.op_of(si).status, "Queued")
        # a real normalization, then a forged one replaying an old counter
        msg, res, _a = L.cycle()
        si2 = self.invoice()
        self.request(si2)
        msg2 = L.lease()
        last = frappe.db.get_value("DGI MCF Device", NID_L, "last_tc")
        replay = {"operation_id": msg2["operation_id"], "outcome": "normalized", "lease_token": msg2["lease_token"],
                  "data": dict(res["data"], fn=si2.name, sig="B" * 24, tc=str(last), recovered=False)}
        out = L.send(replay)
        self.assertEqual(out["status"], "Dispute")
        self.assertIn("non croissant", self.op_of(si2).error_message)

    def test_H24_heartbeat_counter_audit(self):
        L = self.sites[PVF_L]
        frappe.db.set_value("DGI MCF Device", NID_L, "last_tc", 10)
        frappe.cache.delete(frappe.cache.make_key(f"dgi_tc_audit|{NID_L}", shared=True))
        count = lambda code: frappe.db.count("DGI Gateway Event", {"event_code": code})  # noqa: E731
        a, b = count("COMPTEUR_INCOHERENT"), count("FACTURES_HORS_ERPNEXT")
        ops.record_heartbeat(L.doc, {"mid": NID_L + "-1", "nif": NIF, "tc": "9", "flags": []})
        self.assertEqual(count("COMPTEUR_INCOHERENT"), a + 1)
        ops.record_heartbeat(L.doc, {"mid": NID_L + "-1", "nif": NIF, "tc": "9", "flags": []})
        self.assertEqual(count("COMPTEUR_INCOHERENT"), a + 1)          # once per value
        ops.record_heartbeat(L.doc, {"mid": NID_L + "-1", "nif": NIF, "tc": "12", "flags": []})
        self.assertEqual(count("FACTURES_HORS_ERPNEXT"), b + 1)
        # rates announced by the MCF different from ERPNext
        c = count("TAUX_MCF_DIFFERENTS")
        ops.record_heartbeat(L.doc, {"mid": NID_L + "-1", "nif": NIF, "tc": "12", "flags": [],
                                     "tax_rates": {"A": 0.0, "B": 18.0}})
        self.assertEqual(count("TAUX_MCF_DIFFERENTS"), c + 1)
        self.assertIn('"B": 18.0', frappe.db.get_value("DGI MCF Device", NID_L, "tax_rates"))
        frappe.db.set_value("DGI MCF Device", NID_L, {"tax_rates": None, "last_tc": 0})


class TestBrowserStation(HL2Base):
    """Poste MCF (navigateur): ECDSA P-256 key, enrolled by a cashier, approved by someone else."""
    GW = "GW-WEB-LEMBA"

    def _p256(self):
        import base64
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        return key, base64.b64encode(raw).decode()

    def _station(self):
        if frappe.db.exists("DGI Gateway", self.GW):
            gw = frappe.get_doc("DGI Gateway", self.GW)
            if gw.status == "Active":
                from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import revoke_gateway
                revoke_gateway(gw.name, "test")
            frappe.db.set_value("DGI Gateway", self.GW, {"status": "Nouvelle", "enrolled_by": None})
        else:
            frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": self.GW, "kind": "Poste navigateur",
                            "point_of_sale": PVF_L}).insert()

    def test_H25_station_enrolment_rules(self):
        import base64
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import (
            approve_gateway, enroll_gateway, generate_enrollment_code)
        self._station()
        dev = {"mid": NID_L + "-1", "nif": NIF}
        # an Ed25519 (service) key is refused for a browser station
        ed = Identity.create(self.GW, os.path.join(self.tmp, "web.key"), overwrite=True)
        code = generate_enrollment_code(self.GW)["code"]
        with self.assertRaises(frappe.ValidationError):
            enroll_gateway(self.GW, code, ed.public_key_b64, dev, {}, enrolled_by="Administrator")
        # P-256 key, enrolled by a cashier
        key, pub = self._p256()
        code = generate_enrollment_code(self.GW)["code"]
        res = enroll_gateway(self.GW, code, pub, dev, {}, enrolled_by="Administrator")
        gw = frappe.get_doc("DGI Gateway", self.GW)
        self.assertEqual((gw.key_algorithm, gw.enrolled_by, gw.status), ("ECDSA-P256", "Administrator", "A approuver"))
        with self.assertRaises(frappe.ValidationError):              # fingerprint is mandatory
            approve_gateway(self.GW, "")
        with self.assertRaises(frappe.ValidationError):              # two-person rule
            approve_gateway(self.GW, res["fingerprint"])
        frappe.db.set_value("DGI Gateway", self.GW, "enrolled_by", "Guest")       # another person
        approve_gateway(self.GW, res["fingerprint"])
        self.assertEqual(frappe.db.get_value("DGI Gateway", self.GW, "status"), "Active")
        # a WebCrypto-style signature (r||s) is accepted, the same with another body is not
        path, body, ts, nonce = "/api/method/dgi_compliance.gateway.api.lease", b"{}", str(int(time.time())), "ab" * 16
        der = key.sign(signing.canonical("POST", path, self.GW, ts, nonce, body, signing.SCHEME_ECDSA_P256),
                       ec.ECDSA(hashes.SHA256()))
        r, s_ = decode_dss_signature(der)
        sig = base64.b64encode(r.to_bytes(32, "big") + s_.to_bytes(32, "big")).decode()
        self.assertTrue(auth.verify_signature(pub, "POST", path, self.GW, ts, nonce, body, sig, auth.ALG_P256))
        self.assertFalse(auth.verify_signature(pub, "POST", path, self.GW, ts, nonce, b'{"x":1}', sig, auth.ALG_P256))
        self.assertFalse(auth.verify_signature(pub, "POST", path, self.GW, ts, nonce, body, sig, auth.ALG_ED25519))

    def test_H26_operator_token(self):
        tok = auth.issue_operator_token(self.GW, "Administrator")
        self.assertEqual(auth.operator_of(self.GW, tok), "Administrator")
        self.assertIsNone(auth.operator_of("GW-OTHER", tok))         # bound to its station
        self.assertTrue(auth.user_may_operate("Administrator"))
        self.assertFalse(auth.user_may_operate("Guest"))
        auth.revoke_operator_token(self.GW, tok)
        self.assertIsNone(auth.operator_of(self.GW, tok))
