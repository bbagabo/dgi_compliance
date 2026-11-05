"""Integration tests v5.3.2 - statut d'un MCF et test de connexion depuis un navigateur.

    bench --site <site> run-tests --module dgi_compliance.tests.test_statut_mcf

What the browser read (C1h + C2h, no fiscal effect) is compared by ERPNext with what it expects (NID, MID
validated, NIF of the company, rates, poste of the point de vente); the result is recorded on the MCF apart
from the signed heartbeat. Nothing is ever created or activated by a test. TEST SITES ONLY.
"""
import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from dgi_compliance.fiscal import mcf_status as S
from dgi_compliance.fiscal import operations as ops
from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_hl2_mcf import NIF
from dgi_compliance.tests.test_routage_dgi import CASHIER, RoutageBase, count

PVF_S = "S STATUT MCF"
NID_S = "ST00000001"
READER = "lecteur.statut@test.local"          # Accounts User only: may read, may not test


def obs(**over):
    """A reading as the page sends it: C1h + C2h, MCF clock 05/11/2026 18:48:55, last DGI connection 3 min before."""
    o = {"mid": NID_S + "-1", "nid": NID_S, "nif": NIF, "dt": "20261105184855", "tc": "3", "fvc": "0", "frc": "0",
         "group_count": 16, "tax_rates": {"A": 0.0, "B": 16.0}, "flags": [], "invoice_open": False,
         "dgi": {"sent": "3", "pending": "3", "last_sync": "20261105184512", "state": "CON", "error": "Operation terminee!"},
         "port": "USB 03eb:6119", "elapsed_ms": 449, "clock_offset_s": 12}
    o.update(over)
    return o


def st_unsent():
    return S.mcf_status(NID_S)["test"]["unsent"]


class TestStatutMCF(RoutageBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("User", READER):
            frappe.get_doc({"doctype": "User", "email": READER, "first_name": "Lecteur", "send_welcome_email": 0,
                            "roles": [{"role": "Accounts User"}]}).insert(ignore_permissions=True)
        if not frappe.db.exists("DGI Point of Sale", PVF_S):
            frappe.get_doc({"doctype": "DGI Point of Sale", "pos_name": PVF_S, "company": fx.COMPANY, "is_active": 1,
                            "is_sales_location": 0, "nid_requirement": "Optional", "token_requirement": "Optional",
                            "pos_address": "Avenue Bobozo", "pos_city": "Kinshasa"}).insert(ignore_permissions=True)

    def setUp(self):
        super().setUp()
        for g in frappe.get_all("DGI Gateway", filters={"point_of_sale": PVF_S}, pluck="name"):
            frappe.db.delete("DGI Gateway", g)
        frappe.db.set_value("DGI Point of Sale", PVF_S, "mcf_device", None)
        if frappe.db.exists("DGI MCF Device", NID_S):
            frappe.db.delete("DGI MCF Device", NID_S)
        dev = frappe.get_doc({"doctype": "DGI MCF Device", "nid": NID_S, "status": "Actif", "point_of_sale": PVF_S,
                              "last_mid": NID_S + "-1", "validated_mid": NID_S + "-1", "nif": NIF})
        dev.flags.dgi_registration = True
        dev.insert(ignore_permissions=True)
        frappe.db.set_value("DGI Point of Sale", PVF_S, "mcf_device", NID_S)
        frappe.db.commit()

    def as_user(self, user, fn, *a, **k):
        frappe.set_user(user)
        try:
            return fn(*a, **k)
        finally:
            frappe.set_user("Administrator")

    def checks(self, res):
        return {c["code"]: c for c in res["checks"]}

    # ------------------------------------------------------------------------------------------- test
    def test_S01_desk_test_records_the_result_apart_from_the_heartbeat(self):
        before = count("TEST_CONNECTIVITE", mcf_device=NID_S)
        res = self.as_user(CASHIER, S.record_desk_test, json.dumps(obs()), NID_S, "Fiche MCF")
        c = self.checks(res)
        for code in ("LECTURE", "STATUT", "MID", "NIF", "TAUX", "ETAT", "HORLOGE", "DGI"):
            self.assertEqual(c[code]["level"], "ok", (code, c[code]))
        self.assertEqual(c["POSTE"]["level"], "warn")                 # no poste yet: invoices cannot reach the MCF
        self.assertIn("Aucun poste MCF", c["POSTE"]["message"])
        self.assertEqual(res["result"], "Avertissement")
        dev = frappe.get_doc("DGI MCF Device", NID_S)
        self.assertEqual((dev.connection_status, dev.last_test_by, dev.test_dgi_state, dev.test_dgi_unsent,
                          dev.test_clock_offset_s, dev.status), ("Avertissement", CASHIER, "CON", "0", "12", "Actif"))
        self.assertIn("USB 03eb:6119", dev.last_test_origin)
        # last DGI connection 3 min 43 s before the MCF clock, expressed in ERPNext time
        age = (get_datetime(dev.last_connectivity_test) - get_datetime(dev.test_dgi_last_sync)).total_seconds()
        self.assertAlmostEqual(age, 223, delta=2)
        self.assertFalse(dev.last_status_on)                       # the signed heartbeat fields are untouched
        self.assertFalse(dev.dgi_last_sync)
        self.assertEqual(count("TEST_CONNECTIVITE", mcf_device=NID_S), before + 1)

    def test_S02_dc_minus_ec_and_one_day_alert(self):
        o = obs(dgi={"sent": "40", "pending": "49", "last_sync": "20261102100000", "state": "DIS"})
        res = S.record_desk_test(json.dumps(o), NID_S, "Fiche MCF")
        c = self.checks(res)
        self.assertEqual(c["DGI"]["level"], "warn")
        self.assertIn("9 transaction(s) non envoyee(s)", c["DGI"]["message"])
        self.assertIn("plus de 1 jour", c["DGI"]["message"])
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "test_dgi_unsent"), "9")
        self.assertEqual(st_unsent(), 9)
        st = S.mcf_status(NID_S)
        self.assertTrue(st["dgi_sync_old"])
        self.assertGreater(st["dgi_sync_age_s"], 3 * 86400)

    def test_S03_another_nif_or_mid_is_a_gap_and_the_mcf_must_be_validated_again(self):
        res = S.record_desk_test(json.dumps(obs(nif="B9999999Z")), NID_S, "Fiche MCF")
        self.assertEqual(res["result"], "Ecart")
        self.assertEqual(self.checks(res)["NIF"]["level"], "fail")
        self.assertEqual(res["identity_event"], "NIF_MCF_DIFFERENT")
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "status"), "A revalider")
        self.setUp()
        res = S.record_desk_test(json.dumps(obs(mid=NID_S + "-2")), NID_S, "Fiche MCF")
        self.assertEqual((res["result"], res["identity_event"]), ("Ecart", "MID_CHANGE"))

    def test_S04_another_mcf_plugged_fails_without_touching_its_identity(self):
        res = S.record_desk_test(json.dumps(obs(mid="ZZ00000009-1", nid="ZZ00000009")), NID_S, "Fiche MCF")
        self.assertEqual(res["result"], "Echec")
        self.assertEqual(self.checks(res)["NID"]["level"], "fail")
        self.assertFalse(frappe.db.exists("DGI MCF Device", "ZZ00000009"))
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "status"), "Actif")

    def test_S05_unknown_mcf_is_neither_created_nor_recorded(self):
        before = count("TEST_CONNECTIVITE")
        res = S.record_desk_test(json.dumps(obs(mid="ZZ00000008-1", nid="ZZ00000008")), None, "Poste MCF (sans poste)")
        self.assertEqual((res["known"], res["result"]), (False, "Ecart"))
        self.assertEqual(self.checks(res)["FICHE"]["level"], "fail")
        self.assertFalse(frappe.db.exists("DGI MCF Device", "ZZ00000008"))
        self.assertEqual(count("TEST_CONNECTIVITE"), before)

    def test_S06_mcf_not_answering_is_recorded_as_a_failure(self):
        res = S.record_desk_test(json.dumps({"error": "port serie ferme ou MCF debranche"}), NID_S, "Point de vente")
        self.assertEqual(res["result"], "Echec")
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "connection_status"), "Echec")
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "status"), "Actif")
        st = S.mcf_status(point_of_sale=PVF_S)
        self.assertEqual((st["indicator"], st["test"]["result"]), ("red", "Echec"))

    def test_S07_rights(self):
        with self.assertRaises(frappe.PermissionError):
            self.as_user(READER, S.record_desk_test, json.dumps(obs()), NID_S, "Fiche MCF")
        st = self.as_user(READER, S.mcf_status, NID_S)              # reading the state is allowed
        self.assertEqual((st["known"], st["can_test"], st["can_manage"]), (True, False, False))
        with self.assertRaises(frappe.PermissionError):
            self.as_user(CASHIER, S.create_station, PVF_S)
        self.assertFalse(frappe.get_all("DGI Gateway", filters={"point_of_sale": PVF_S}))

    # ------------------------------------------------------------------------------------------- status
    def test_S08_status_follows_the_poste_and_the_last_test(self):
        st = S.mcf_status(NID_S)
        self.assertEqual((st["label"], st["indicator"], st["can_create_station"]), ("Jamais teste", "gray", True))
        S.record_desk_test(json.dumps(obs()), NID_S, "Fiche MCF")
        st = S.mcf_status(NID_S)
        self.assertEqual(st["indicator"], "orange")
        self.assertTrue(st["label"].startswith("Joignable"))
        self.assertFalse(st["dgi_sync_old"])
        created_before = count("POSTE_CREE", point_of_sale=PVF_S)
        res = S.create_station(PVF_S)
        self.assertEqual((res["name"], res["created"]), ("POSTE-" + NID_S, True))
        self.assertEqual(S.create_station(PVF_S), {"name": "POSTE-" + NID_S, "created": False})   # idempotent
        gw = frappe.get_doc("DGI Gateway", "POSTE-" + NID_S)
        self.assertEqual((gw.kind, gw.status, gw.mcf_device), ("Poste navigateur", "Nouvelle", NID_S))
        self.assertEqual(count("POSTE_CREE", point_of_sale=PVF_S), created_before + 1)
        st = S.mcf_status(NID_S)
        self.assertFalse(st["can_create_station"])
        self.assertEqual([s["name"] for s in st["stations"]], ["POSTE-" + NID_S])
        res = S.record_desk_test(json.dumps(obs()), NID_S, "Fiche MCF")
        self.assertIn("Nouvelle", self.checks(res)["POSTE"]["message"])
        # an approved poste that sends its heartbeat: "En ligne"
        frappe.db.set_value("DGI Gateway", gw.name, {"status": "Active", "last_seen": now_datetime()})
        st = S.mcf_status(NID_S)
        self.assertEqual(st["indicator"], "green")
        self.assertTrue(st["label"].startswith("En ligne"))
        frappe.db.set_value("DGI Gateway", gw.name, "last_seen", add_to_date(now_datetime(), minutes=-30))
        frappe.db.set_value("DGI MCF Device", NID_S, "last_connectivity_test", add_to_date(now_datetime(), hours=-2))
        st = S.mcf_status(NID_S)
        self.assertEqual((st["indicator"], st["label"]), ("red", "Poste hors ligne"))

    def test_S09_heartbeat_counts_unsent_transactions_as_dc_minus_ec(self):
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": "GW-S-STATUT", "kind": "Poste navigateur",
                        "point_of_sale": PVF_S}).insert(ignore_permissions=True)
        gw = frappe.get_doc("DGI Gateway", "GW-S-STATUT")
        ops.record_heartbeat(gw, {"mid": NID_S + "-1", "nif": NIF, "dgi_last_sync": "20261105184512",
                                  "dgi_pending": "49", "dgi_sent": "40", "flags": [], "gateway_version": "web-0.2.1"})
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "dgi_pending"), 9)
        ops.record_heartbeat(gw, {"mid": NID_S + "-1", "nif": NIF, "dgi_pending": "3"})     # older gateway: DC only
        self.assertEqual(frappe.db.get_value("DGI MCF Device", NID_S, "dgi_pending"), 3)
