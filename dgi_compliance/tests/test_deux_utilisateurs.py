"""Tests v5.4.0 - separation des taches avec deux utilisateurs (fiscal.approvals).

Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_deux_utilisateurs

A company with two people: the manager (DGI Fiscal Manager) and the cashier (DGI Caisse MCF +
DGI Approbateur). Each four-eyes action works whoever initiates it, the initiator never approves,
a technical account (gateway, MCF agent) never approves, and a one-person company can switch the
rule off with every self-approval journalized. Scenarios U01-U09 (livrable v5.4.0, section 7).
"""
import base64
import unittest

import frappe

from dgi_compliance.fiscal import approvals as A
from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_hl2_mcf import NIF, _upsert

GERANT = "gerant.deux@test.local"
CAISSE = "caisse.deux@test.local"           # DGI Caisse MCF + DGI Approbateur
CAISSE_SEULE = "caisse.seule@test.local"    # DGI Caisse MCF only
COMPTABLE = "comptable.deux@test.local"     # Accounts Manager
PVF_U, NID_U = "U DEUX UTILISATEURS", "CC300U0009"
PVF_VA, PVF_VB, PVF_SW = "U VALIDATION A", "U VALIDATION B", "U BASCULE"


def _user(email, roles):
    if frappe.db.exists("User", email):
        u = frappe.get_doc("User", email)
        u.set("roles", [])
    else:
        u = frappe.get_doc({"doctype": "User", "email": email, "first_name": email.split("@")[0],
                            "send_welcome_email": 0})
    for r in roles:
        u.append("roles", {"role": r})
    u.flags.no_welcome_mail = True
    u.save(ignore_permissions=True) if not u.is_new() else u.insert(ignore_permissions=True)
    frappe.clear_cache(user=email)


def _p256():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.b64encode(raw).decode()


def as_user(user, fn, *args, **kwargs):
    frappe.set_user(user)
    try:
        return fn(*args, **kwargs)
    finally:
        frappe.set_user("Administrator")


def set_rule(setting, value):
    frappe.db.set_single_value("DGI Compliance Settings", setting, value)
    frappe.clear_document_cache("DGI Compliance Settings", "DGI Compliance Settings")
    frappe.clear_cache(doctype="DGI Compliance Settings")


class TwoUsersBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        fx.setup_all()
        _user(GERANT, ["DGI Fiscal Manager", "Accounts User", "Sales User"])
        _user(CAISSE, ["DGI Caisse MCF", "DGI Approbateur", "Accounts User", "Sales User"])
        _user(CAISSE_SEULE, ["DGI Caisse MCF", "Accounts User"])
        _user(COMPTABLE, ["Accounts Manager", "Accounts User"])
        _upsert("DGI MCF Device", NID_U, {"nid": NID_U, "status": "Declare"})
        _upsert("DGI Point of Sale", PVF_U, {"pos_name": PVF_U, "company": fx.COMPANY, "is_active": 1,
                                             "is_sales_location": 0, "primary_channel": "MCF",
                                             "priority_reason": "Tests v5.4.0", "active_channel": "MCF",
                                             "mcf_device": NID_U, "fallback_policy": "Aucune",
                                             "nid_requirement": "Optional", "token_requirement": "Optional"})
        frappe.db.set_value("DGI MCF Device", NID_U, {"status": "Actif", "point_of_sale": PVF_U, "nif": NIF})
        for pvf in (PVF_VA, PVF_VB):
            _upsert("DGI Point of Sale", pvf, {"pos_name": pvf, "company": fx.COMPANY, "is_active": 1,
                                               "is_sales_location": 0, "nid_requirement": "Optional",
                                               "token_requirement": "Optional", "mcf_device": None})
        _upsert("DGI Point of Sale", PVF_SW, {"pos_name": PVF_SW, "company": fx.COMPANY, "is_active": 1,
                                              "is_sales_location": 0, "primary_channel": "MCF",
                                              "priority_reason": "Tests v5.4.0", "switch_allowed": 1,
                                              "fallback_policy": "Aucune", "nid_requirement": "Optional",
                                              "token_requirement": "Optional"})
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        set_rule(A.RULE_STATION, 1)
        set_rule(A.RULE_SWITCH, 1)
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        frappe.set_user("Administrator")
        set_rule(A.RULE_SWITCH, 0)          # as the HL2 tests expect it
        set_rule(A.RULE_STATION, 1)
        frappe.db.commit()

    # ---------------------------------------------------------------- helpers
    def station(self):
        gid = "POSTE-U-" + frappe.generate_hash(length=6).upper()
        frappe.get_doc({"doctype": "DGI Gateway", "gateway_id": gid, "kind": "Poste navigateur",
                        "point_of_sale": PVF_U}).insert(ignore_permissions=True)
        return gid

    def enrol(self, gid, by):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import enroll_gateway, generate_enrollment_code
        code = as_user(GERANT, generate_enrollment_code, gid)["code"]
        return enroll_gateway(gid, code, _p256(), {"mid": NID_U + "-1", "nif": NIF}, {"hostname": "caisse"},
                              enrolled_by=by)["fingerprint"]

    def approve(self, user, gid, fp):
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import approve_gateway
        return as_user(user, approve_gateway, gid, fp)

    def observe(self, nid, by):
        from dgi_compliance.fiscal.registry import record_observation
        frappe.db.delete("DGI MCF Device", nid)
        return record_observation({"mid": nid + "-1", "nif": NIF, "taxpayer_name":
                                   frappe.db.get_value("Company", fx.COMPANY, "company_name")}, by)


class TestTwoUsers(TwoUsersBase):
    def test_U01_role_is_restricted_to_approving(self):
        self.assertTrue(frappe.db.get_value("Role", "DGI Approbateur", "desk_access"))
        frappe.set_user(CAISSE)
        try:
            self.assertTrue(frappe.has_permission("DGI Gateway", "read"))
            self.assertTrue(frappe.has_permission("DGI Channel Switch", "submit"))
            self.assertFalse(frappe.has_permission("DGI Channel Switch", "create"))
            self.assertFalse(frappe.has_permission("DGI Point of Sale", "write"))
            self.assertFalse(frappe.has_permission("DGI Compliance Settings", "write"))
            self.assertTrue(A.can_approve())
            self.assertFalse(A.is_manager())
        finally:
            frappe.set_user("Administrator")
        from dgi_compliance.dgi_compliance.doctype.dgi_gateway.dgi_gateway import generate_enrollment_code
        with self.assertRaises(frappe.PermissionError):                     # no enrolment code
            as_user(CAISSE, generate_enrollment_code, self.station())

    def test_U02_station_enrolled_by_the_manager_is_approved_by_the_cashier(self):
        gid = self.station()
        fp = self.enrol(gid, by=GERANT)                                      # the manager is at the cash desk
        with self.assertRaises(frappe.ValidationError):                     # he never approves his own
            self.approve(GERANT, gid, fp)
        with self.assertRaises(frappe.PermissionError):                     # a cashier without the role neither
            self.approve(CAISSE_SEULE, gid, fp)
        self.approve(CAISSE, gid, fp)
        gw = frappe.get_doc("DGI Gateway", gid)
        self.assertEqual((gw.status, gw.approved_by, gw.enrolled_by, gw.code_issued_by),
                         ("Active", CAISSE, GERANT, GERANT))
        ev = frappe.get_all("DGI Gateway Event", filters={"gateway": gid, "event_code": "APPROUVEE"},
                            fields=["message", "details"])[0]
        self.assertIn("DGI Approbateur", ev.message)
        self.assertIn(GERANT, ev.message)

    def test_U03_station_enrolled_by_the_cashier_is_approved_by_the_manager(self):
        gid = self.station()
        fp = self.enrol(gid, by=CAISSE)
        with self.assertRaises(frappe.ValidationError):
            self.approve(CAISSE, gid, fp)
        self.approve(GERANT, gid, fp)
        self.assertEqual(frappe.db.get_value("DGI Gateway", gid, ["status", "approved_by"]), ("Active", GERANT))

    def test_U04_mcf_validation_in_both_directions(self):
        from dgi_compliance.fiscal.registry import validate_device
        frappe.db.set_value("DGI Point of Sale", PVF_VA, "mcf_device", None)
        frappe.db.set_value("DGI Point of Sale", PVF_VB, "mcf_device", None)
        # proposed by the cashier, validated by the manager
        self.observe("CC300U0101", CAISSE)
        with self.assertRaises(frappe.ValidationError):
            as_user(CAISSE, validate_device, "CC300U0101", PVF_VA)
        as_user(GERANT, validate_device, "CC300U0101", PVF_VA)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", "CC300U0101", ["status", "validated_by"]), ("Actif", GERANT))
        # proposed by the manager (search from his PC), validated by the cashier-approver
        self.observe("CC300U0102", GERANT)
        with self.assertRaises(frappe.ValidationError):
            as_user(GERANT, validate_device, "CC300U0102", PVF_VB)
        with self.assertRaises(frappe.PermissionError):                     # channel derogation: managers only
            as_user(CAISSE, validate_device, "CC300U0102", PVF_VB, None, "MCF", "motif")
        as_user(CAISSE, validate_device, "CC300U0102", PVF_VB)
        self.assertEqual(frappe.db.get_value("DGI MCF Device", "CC300U0102", ["status", "validated_by"]), ("Actif", CAISSE))
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_VB, "mcf_device"), "CC300U0102")
        with self.assertRaises(frappe.PermissionError):                     # a plain cashier cannot validate
            self.observe("CC300U0103", GERANT)
            as_user(CAISSE_SEULE, validate_device, "CC300U0103", PVF_VB)

    def test_U05_channel_switch_requested_by_the_manager_approved_by_the_cashier(self):
        frappe.db.set_value("DGI Point of Sale", PVF_SW, {"forced_switch": None, "active_channel": "MCF"})
        frappe.clear_document_cache("DGI Point of Sale", PVF_SW)
        sw = as_user(GERANT, lambda: frappe.get_doc({"doctype": "DGI Channel Switch", "point_of_sale": PVF_SW,
                                                     "to_channel": "e-MCF", "reason": "Maintenance"}).insert())
        with self.assertRaises(frappe.ValidationError):
            as_user(GERANT, lambda: frappe.get_doc("DGI Channel Switch", sw.name).submit())
        with self.assertRaises(frappe.PermissionError):
            as_user(CAISSE_SEULE, lambda: frappe.get_doc("DGI Channel Switch", sw.name).submit())
        def tamper():
            d = frappe.get_doc("DGI Channel Switch", sw.name)
            d.reason = "Panne MCF"
            d.save()
        with self.assertRaises(frappe.PermissionError):                     # the approver never edits the request
            as_user(CAISSE, tamper)
        as_user(CAISSE, lambda: frappe.get_doc("DGI Channel Switch", sw.name).submit())
        sw = frappe.get_doc("DGI Channel Switch", sw.name)
        self.assertEqual((sw.docstatus, sw.approved_by, sw.owner), (1, CAISSE, GERANT))
        self.assertEqual(frappe.db.get_value("DGI Point of Sale", PVF_SW, "active_channel"), "e-MCF")
        self.assertTrue(frappe.db.exists("DGI Gateway Event", {"event_code": "BASCULE_CANAL", "point_of_sale": PVF_SW,
                                                               "message": ["like", f"%approuvee par {CAISSE}%"]}))

    def test_U06_technical_accounts_never_approve(self):
        gid = self.station()
        fp = self.enrol(gid, by=CAISSE)
        self.approve(GERANT, gid, fp)
        api_user = frappe.db.get_value("DGI Gateway", gid, "api_user")
        self.assertTrue(A.is_technical_account(api_user))
        self.assertFalse(A.is_technical_account("Administrator"))
        self.assertFalse(A.is_technical_account(CAISSE))
        other = self.station()
        fp2 = self.enrol(other, by=GERANT)
        with self.assertRaises(frappe.PermissionError):
            self.approve(api_user, other, fp2)
        # even a technical account that would receive a manager role stays refused
        u = frappe.get_doc("User", api_user)
        u.append("roles", {"role": "DGI Fiscal Manager"})
        u.save(ignore_permissions=True)
        frappe.clear_cache(user=api_user)
        with self.assertRaises(frappe.PermissionError):
            self.approve(api_user, other, fp2)
        self.assertEqual(frappe.db.get_value("DGI Gateway", other, "status"), "A approuver")

    def test_U07_one_person_company_self_approval_is_journalized(self):
        set_rule(A.RULE_STATION, 0)
        try:
            gid = self.station()
            fp = self.enrol(gid, by=GERANT)
            self.approve(GERANT, gid, fp)
            self.assertEqual(frappe.db.get_value("DGI Gateway", gid, "status"), "Active")
            self.assertTrue(frappe.db.exists("DGI Gateway Event", {"gateway": gid, "event_code": "AUTO_APPROBATION",
                                                                   "level": "Alerte"}))
        finally:
            set_rule(A.RULE_STATION, 1)

    def test_U08_only_a_system_manager_changes_the_rules_and_it_is_journalized(self):
        frappe.set_user(COMPTABLE)
        try:
            s = frappe.get_doc("DGI Compliance Settings")
            s.station_approval_two_people = 0
            s.flags.ignore_mandatory = True
            s.save()
        finally:
            frappe.set_user("Administrator")
        self.assertEqual(frappe.db.get_single_value("DGI Compliance Settings", "station_approval_two_people"), 1)
        before = frappe.db.count("DGI Gateway Event", {"event_code": "PARAMETRE_CONTROLE"})
        s = frappe.get_doc("DGI Compliance Settings")
        s.station_approval_two_people = 0
        s.flags.ignore_mandatory = True
        s.save(ignore_permissions=True)
        ev = frappe.get_all("DGI Gateway Event", filters={"event_code": "PARAMETRE_CONTROLE"},
                            fields=["level", "message"], order_by="creation desc", limit=1)[0]
        self.assertEqual(frappe.db.count("DGI Gateway Event", {"event_code": "PARAMETRE_CONTROLE"}), before + 1)
        self.assertEqual(ev.level, "Alerte")                                 # a rule was weakened
        self.assertIn("station_approval_two_people", ev.message)
        set_rule(A.RULE_STATION, 1)

    def test_U09_separation_report_counts_two_persons_and_no_machine(self):
        rep = as_user(CAISSE, A.separation_report)
        self.assertTrue(rep["ok"], rep)
        self.assertIn(GERANT, rep["persons"])
        self.assertIn(CAISSE, rep["persons"])
        for tech in rep["technical_accounts"]:
            self.assertNotIn(tech, rep["persons"])
        station = next(c for c in rep["controls"] if c["setting"] == A.RULE_STATION)
        self.assertIn(CAISSE, station["approvers"])
        self.assertNotIn(CAISSE_SEULE, station["approvers"])
