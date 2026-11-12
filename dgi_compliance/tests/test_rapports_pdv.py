"""Tests v5.4.2 - rapports X / Z / A par point de vente fiscal, lignes a zero, annulation d'une demande
d'approbation et rejet d'une approbation de Poste MCF.

Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_rapports_pdv

P01-P06: reports. G01-G05: Poste MCF (DGI Fiscal Manager or DGI Approbateur).
"""
import json
import time

import frappe
from frappe.utils import add_to_date, flt, now_datetime

from dgi_compliance.dgi_compliance.doctype.dgi_rapport_fiscal.dgi_rapport_fiscal import report_view
from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_deux_utilisateurs import (CAISSE, CAISSE_SEULE, GERANT, TwoUsersBase, as_user, set_rule)
from dgi_compliance.tests.test_hl1_emcf import HL1Base
from dgi_compliance.tests.test_hl2_mcf import _upsert

PVF_B = "RAPPORT PDV B"
HIDE = "report_hide_zero_rows"


def _report(kind, start=None):
    doc = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": kind, "company": fx.COMPANY,
                          "from_datetime": start})
    return doc.insert(ignore_permissions=True)


class TestReportsPerPointOfSale(HL1Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _upsert("DGI Point of Sale", PVF_B, {"pos_name": PVF_B, "company": fx.COMPANY, "is_active": 1,
                                             "is_sales_location": 0, "nid_requirement": "Optional",
                                             "token_requirement": "Optional", "fallback_policy": "Aucune",
                                             "pos_code": "MAG-B", "pos_city": "Lubumbashi",
                                             # e-MCF whatever the other suites left behind
                                             "primary_channel": None, "priority_reason": None,
                                             "forced_switch": None, "active_channel": "e-MCF"})
        frappe.db.commit()

    def setUp(self):
        super().setUp()
        set_rule(HIDE, 1)
        self.start = now_datetime()
        time.sleep(1.1)

    def tearDown(self):
        set_rule(HIDE, 1)

    def sale(self, pvf=None, lines=None, **extra):
        if pvf:
            extra["dgi_point_of_sale"] = pvf
        si = fx.make_invoice("CLIENT PM SARL", lines or [("DGI-B-LAIT", 2, 10000)], submit=True, **extra)
        si = self.assertNormalized(si)
        self.assertEqual(si.dgi_point_of_sale, pvf or fx.HL1_PVF)
        return si

    # ---------- P01: two points de vente -> one block each, then the synthesis ----------
    def test_P01_two_points_of_sale_then_synthesis(self):
        self.sale(lines=[("DGI-B-LAIT", 2, 10000)])                      # HL1 SIEGE: 20 000 HT + 16 %
        self.sale(PVF_B, lines=[("DGI-C-FARINE", 1, 45000)])             # PDV B: 45 000 HT + 5 %
        self.sale(PVF_B, lines=[("DGI-A-EAU", 4, 1500)])                 # PDV B: 6 000 exonere
        x = _report("X périodique", self.start)
        data = json.loads(x.report_data)
        pvs = {p["point_of_sale"]: p for p in data["points_of_sale"]}
        self.assertEqual(set(pvs), {fx.HL1_PVF, PVF_B})
        self.assertEqual(pvs[fx.HL1_PVF]["by_type"]["FV"]["count"], 1)
        self.assertEqual(pvs[PVF_B]["by_type"]["FV"]["count"], 2)
        self.assertEqual(pvs[PVF_B]["code"], "MAG-B")
        self.assertAlmostEqual(pvs[fx.HL1_PVF]["by_type"]["FV"]["total"], 23200, delta=0.01)
        self.assertAlmostEqual(pvs[PVF_B]["by_type"]["FV"]["total"], 47250 + 6000, delta=0.01)
        # the synthesis (top level) is the sum of the points de vente, for every figure
        for key in ("count", "total", "ht", "vat", "ts"):
            self.assertAlmostEqual(data["by_type"]["FV"][key],
                                   sum(p["by_type"]["FV"][key] for p in pvs.values()), delta=0.01, msg=key)
        for g in ("A", "B", "C"):
            self.assertAlmostEqual(data["by_type_group"]["FV"][g]["total"],
                                   sum(p["by_type_group"]["FV"][g]["total"] for p in pvs.values()), delta=0.01)
        self.assertAlmostEqual(data["summary"]["net_total"],
                               sum(p["summary"]["net_total"] for p in pvs.values()), delta=0.01)
        self.assertEqual(x.invoice_count, 3)
        view = report_view(x)
        self.assertEqual([b["heading"] for b in view["blocks"]],
                         [f"Point de vente : {fx.HL1_PVF}", f"Point de vente : {PVF_B}",
                          "Synthèse : tous les points de vente réunis (2)"])
        self.assertEqual([b["synthesis"] for b in view["blocks"]], [False, False, True])
        self.assertIn("MAG-B", view["blocks"][1]["identity"])
        html = frappe.get_print("DGI Rapport Fiscal", x.name)
        for label in (f"Point de vente : {fx.HL1_PVF}", f"Point de vente : {PVF_B}",
                      "Synthèse : tous les points de vente réunis (2)"):
            self.assertIn(label, html)
        self.assertEqual(html.count("Section 1: Totaux par type de facture"), 3)
        self.assertLess(html.index(f"Point de vente : {PVF_B}"), html.index("Synthèse :"))

    # ---------- P02: a single point de vente -> that one only, no synthesis ----------
    def test_P02_single_point_of_sale_no_synthesis(self):
        self.sale(PVF_B, lines=[("DGI-B-LAIT", 1, 10000)])
        x = _report("X périodique", self.start)
        data = json.loads(x.report_data)
        self.assertEqual([p["point_of_sale"] for p in data["points_of_sale"]], [PVF_B])
        view = report_view(x)
        self.assertEqual(len(view["blocks"]), 1)
        self.assertEqual(view["blocks"][0]["heading"], "")
        self.assertIn(PVF_B, view["point_of_sale"])
        html = frappe.get_print("DGI Rapport Fiscal", x.name)
        self.assertNotIn("Synthèse", html)
        self.assertEqual(html.count("Section 1: Totaux par type de facture"), 1)
        self.assertIn("Point de vente", html)
        self.assertIn(PVF_B, html)

    # ---------- P03: rows without amount are hidden (setting), the stored data stay complete ----------
    def test_P03_zero_rows_hidden(self):
        self.sale(PVF_B, lines=[("DGI-B-LAIT", 1, 10000)])
        x = _report("X périodique", self.start)
        data = json.loads(x.report_data)
        self.assertEqual(list(data["by_type"]), ["FV", "FT", "FA", "EV", "ET", "EA"])       # complete data
        self.assertEqual(len(data["by_type_group"]["FT"]), 19)
        block = report_view(x)["blocks"][0]
        self.assertEqual([t["type"] for t in block["types"]], ["FV"])
        self.assertEqual([g["group"] for g in block["types"][0]["groups"]], ["B"])
        self.assertEqual([p["label"] for p in block["payments"]], ["ESPECES"])
        self.assertEqual(block["reductions"], [])
        self.assertEqual(block["natures"], [])
        html = frappe.get_print("DGI Rapport Fiscal", x.name)
        self.assertIn("Aucune réduction sur la période", html)
        self.assertIn("Aucune facture d'avoir sur la période", html)
        self.assertNotIn(">MOBILE MONEY<", html)
        # setting off: every row, as on the DGI e-UF report
        set_rule(HIDE, 0)
        block = report_view(x)["blocks"][0]
        self.assertEqual([t["type"] for t in block["types"]], ["FV", "FT", "FA", "EV", "ET", "EA"])
        self.assertTrue(all(len(t["groups"]) == 19 for t in block["types"]))
        self.assertEqual(len(block["payments"]), 7)
        self.assertEqual(len(block["reductions"]), 6)
        self.assertEqual(len(block["natures"]), 4)
        self.assertIn(">MOBILE MONEY<", frappe.get_print("DGI Rapport Fiscal", x.name))

    # ---------- P04: Z report per point de vente; an empty period prints ----------
    def test_P04_z_report_and_empty_period(self):
        self.sale(lines=[("DGI-B-LAIT", 1, 10000)])
        self.sale(PVF_B, lines=[("DGI-B-LAIT", 3, 10000)])
        z = _report("Z")
        data = json.loads(z.report_data)
        pvs = {p["point_of_sale"]: p for p in data["points_of_sale"]}
        self.assertIn(PVF_B, pvs)
        self.assertEqual(data["summary"]["count"], sum(p["summary"]["count"] for p in pvs.values()))
        self.assertAlmostEqual(flt(z.total_ttc), sum(p["summary"]["net_total"] for p in pvs.values()), delta=0.01)
        z2 = _report("Z")                                               # nothing since the previous Z
        data2 = json.loads(z2.report_data)
        self.assertEqual(data2["points_of_sale"], [])
        view = report_view(z2)
        self.assertEqual(len(view["blocks"]), 1)
        self.assertEqual(view["blocks"][0]["types"], [])
        html = frappe.get_print("DGI Rapport Fiscal", z2.name)
        self.assertIn("Aucune facture sur la période", html)
        self.assertNotIn("Synthèse", html)

    # ---------- P05: A report per point de vente ----------
    def test_P05_a_report_per_point_of_sale(self):
        _report("A")                                                    # closes the previous A period
        time.sleep(1.1)
        self.sale(lines=[("DGI-B-LAIT", 2, 10000)])
        self.sale(PVF_B, lines=[("DGI-B-LAIT", 5, 10000), ("DGI-C-FARINE", 1, 45000)])
        a = _report("A")
        data = json.loads(a.report_data)
        pvs = {p["point_of_sale"]: p for p in data["points_of_sale"]}
        self.assertEqual(set(pvs), {fx.HL1_PVF, PVF_B})
        qty = lambda rows, code: sum(r["qty_sold"] for r in rows if r["code"] == code)    # noqa: E731
        self.assertEqual(qty(pvs[fx.HL1_PVF]["articles"], "DGI-B-LAIT"), 2)
        self.assertEqual(qty(pvs[PVF_B]["articles"], "DGI-B-LAIT"), 5)
        self.assertEqual(qty(data["articles"], "DGI-B-LAIT"), 7)
        self.assertEqual([r["code"] for r in pvs[fx.HL1_PVF]["articles"]], ["DGI-B-LAIT"])
        self.assertAlmostEqual(data["summary"]["amount"], sum(p["summary"]["amount"] for p in pvs.values()), delta=0.01)
        view = report_view(a)
        self.assertEqual(len(view["blocks"]), 3)
        self.assertTrue(view["blocks"][2]["synthesis"])
        html = frappe.get_print("DGI Rapport Fiscal", a.name)
        self.assertEqual(html.count("Détail par article"), 3)
        self.assertIn("Synthèse : tous les points de vente réunis (2)", html)

    # ---------- P06: a report generated before v5.4.2 (no detail per point de vente) still prints ----------
    def test_P06_report_of_an_older_version(self):
        self.sale(lines=[("DGI-B-LAIT", 1, 10000)])
        self.sale(PVF_B, lines=[("DGI-B-LAIT", 1, 10000)])
        x = _report("X périodique", self.start)
        data = json.loads(x.report_data)
        data.pop("points_of_sale")
        frappe.db.set_value("DGI Rapport Fiscal", x.name, "report_data", json.dumps(data), update_modified=False)
        x.reload()
        view = report_view(x)
        self.assertEqual(len(view["blocks"]), 1)
        self.assertEqual(view["point_of_sale"], "")
        html = frappe.get_print("DGI Rapport Fiscal", x.name)
        self.assertNotIn("Synthèse", html)
        self.assertIn("Section 5:", html)


    # ---------- P07: the setting is on after an installation or an update (no stored default for a Single) ----------
    def test_P07_setting_on_by_default(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_rapport_fiscal.dgi_rapport_fiscal import hide_zero_rows
        from dgi_compliance.patches.v5_4.rapports_par_pdv import execute
        frappe.db.delete("Singles", {"doctype": "DGI Compliance Settings", "field": HIDE})
        frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
        execute()
        frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
        self.assertTrue(hide_zero_rows())
        set_rule(HIDE, 0)
        execute()                                                       # a choice already made is kept
        frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
        self.assertFalse(hide_zero_rows())


class TestWithdrawStation(TwoUsersBase):
    def gw(self, gid):
        return frappe.get_doc("DGI Gateway", gid)

    def events(self, gid, code):
        return frappe.get_all("DGI Gateway Event", filters={"gateway": gid, "event_code": code},
                              fields=["message", "level", "details"])

    def cancel(self, user, gid, reason="Mauvais poste enrole"):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import cancel_enrollment_request
        return as_user(user, cancel_enrollment_request, gid, reason)

    def reject(self, user, gid, reason="Poste vole"):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import reject_approval
        return as_user(user, reject_approval, gid, reason)

    # ---------- G01: the approver (cashier holding DGI Approbateur) cancels a pending request ----------
    def test_G01_approver_cancels_pending_request(self):
        gid = self.station()
        fp = self.enrol(gid, GERANT)
        self.assertEqual(self.gw(gid).status, "A approuver")
        self.assertEqual(self.cancel(CAISSE, gid)["status"], "Nouvelle")
        gw = self.gw(gid)
        self.assertEqual(gw.status, "Nouvelle")
        for field in ("public_key", "key_fingerprint", "enrolled_by", "approved_by", "enrollment_code_hash"):
            self.assertFalse(gw.get(field), field)
        ev = self.events(gid, "DEMANDE_ANNULEE")
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0].level, "Alerte")
        self.assertIn(CAISSE, ev[0].message)
        self.assertIn("Mauvais poste enrole", ev[0].message)
        self.assertEqual(json.loads(ev[0].details)["fingerprint"], fp)
        # the old fingerprint cannot be approved any more
        with self.assertRaises(frappe.ValidationError):
            self.approve(CAISSE, gid, fp)

    # ---------- G02: the manager cancels too, even the request he enrolled himself ----------
    def test_G02_manager_cancels_own_request(self):
        gid = self.station()
        self.enrol(gid, GERANT)
        self.assertEqual(self.cancel(GERANT, gid)["status"], "Nouvelle")

    # ---------- G03: no role, no reason, wrong status -> refused ----------
    def test_G03_refusals(self):
        gid = self.station()
        fp = self.enrol(gid, GERANT)
        with self.assertRaises(frappe.PermissionError):
            self.cancel(CAISSE_SEULE, gid)                       # DGI Caisse MCF only
        with self.assertRaises(frappe.ValidationError):
            self.cancel(CAISSE, gid, reason="  ")                # reason is mandatory
        with self.assertRaises(frappe.ValidationError):
            self.reject(CAISSE, gid)                             # not approved yet
        self.assertEqual(self.gw(gid).status, "A approuver")
        self.approve(CAISSE, gid, fp)
        with self.assertRaises(frappe.ValidationError):
            self.cancel(CAISSE, gid)                             # already approved: 'Rejeter l'approbation'
        with self.assertRaises(frappe.PermissionError):
            self.reject(CAISSE_SEULE, gid)
        gw = self.gw(gid)
        with self.assertRaises(frappe.PermissionError):
            self.reject(gw.api_user, gid, "compte technique")
        self.assertEqual(self.gw(gid).status, "Active")

    # ---------- G04: an approval is rejected: the poste stops, then is enrolled and approved again ----------
    def test_G04_reject_approval_then_enrol_again(self):
        gid = self.station()
        fp = self.enrol(gid, GERANT)
        self.approve(CAISSE, gid, fp)
        gw = self.gw(gid)
        self.assertEqual(gw.status, "Active")
        api_user = gw.api_user
        self.assertTrue(frappe.db.get_value("User", api_user, "enabled"))
        self.assertEqual(self.reject(CAISSE, gid)["status"], "Nouvelle")       # by the approver who approved it
        gw = self.gw(gid)
        self.assertEqual(gw.status, "Nouvelle")
        self.assertFalse(gw.public_key)
        self.assertFalse(gw.approved_by)
        self.assertFalse(frappe.db.get_value("User", api_user, "enabled"))
        ev = self.events(gid, "APPROBATION_REJETEE")
        self.assertEqual(len(ev), 1)
        self.assertEqual(json.loads(ev[0].details)["approved_by"], CAISSE)
        # a session can no longer be opened on it
        from dgi_compliance.gateway.station import open_session
        res = as_user(CAISSE, open_session, gid)
        self.assertEqual(res["status"], "Nouvelle")
        self.assertNotIn("operator_token", res)
        # new code, new key, new approval by another person
        fp2 = self.enrol(gid, CAISSE)
        self.assertNotEqual(fp2, fp)
        self.approve(GERANT, gid, fp2)
        gw = self.gw(gid)
        self.assertEqual(gw.status, "Active")
        self.assertTrue(frappe.db.get_value("User", gw.api_user, "enabled"))

    # ---------- G05: the manager rejects an approval ----------
    def test_G05_manager_rejects_approval(self):
        gid = self.station()
        fp = self.enrol(gid, CAISSE)
        self.approve(GERANT, gid, fp)
        self.assertEqual(self.reject(GERANT, gid, "Caisse remplacee")["status"], "Nouvelle")
        self.assertIn("Caisse remplacee", self.events(gid, "APPROBATION_REJETEE")[0].message)
