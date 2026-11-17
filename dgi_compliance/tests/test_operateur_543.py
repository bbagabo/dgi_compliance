"""Tests v5.4.3 - "Operateur DGI" of an invoice normalized by an MCF.

Run on a TEST site:
    bench --site <site> run-tests --module dgi_compliance.tests.test_operateur_543

Before 5.4.3 the operator code kept by the MCF (16 characters: 'caisse.operateur' for
'caisse.operateur543@test.local') was written into custom_dgi_operator, a Link to User: the normalized invoice
could not be submitted. The suites of 5.4.2 did not see it: they normalize as 'Administrator', a user name
without '@' that stays a valid User once shortened.
"""
import json

import frappe

from dgi_compliance.tests.test_hl2_mcf import HL2Base, PVF_L

USER = "caisse.operateur543@test.local"
MCF_CODE = "caisse.operateur"          # local part cut to the 16 characters accepted by the MCF


def _user():
    if not frappe.db.exists("User", USER):
        u = frappe.get_doc({"doctype": "User", "email": USER, "first_name": "Caisse 543", "send_welcome_email": 0,
                            "roles": [{"role": "Accounts User"}, {"role": "DGI Caisse MCF"},
                                      {"role": "DGI Fiscal Manager"}]})
        u.flags.no_welcome_mail = True
        u.insert(ignore_permissions=True)
    return USER


class TestOperatorOfMCFInvoices(HL2Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _user()
        frappe.db.commit()

    def normalized_by_user(self):
        frappe.set_user(USER)
        try:
            si = self.invoice()
            self.assertEqual(self.request(si)["stage"], "queued-mcf")
        finally:
            frappe.set_user("Administrator")
        _msg, result, _answer = self.sites[PVF_L].cycle()
        self.assertEqual(result["outcome"], "normalized", result)
        return self.reload(si)

    def test_O01_operator_is_the_user_and_the_invoice_can_be_submitted(self):
        si = self.normalized_by_user()
        self.assertEqual(si.custom_dgi_status, "Normalized")
        self.assertEqual(si.custom_dgi_operator, USER)
        payload = json.loads(self.op_of(si).payload)
        self.assertEqual(payload["operator"]["id"], MCF_CODE)              # what the MCF received is unchanged
        si.submit()                                                        # failed before 5.4.3 (Link to User)
        self.assertEqual(self.reload(si).docstatus, 1)

    def test_O02_print_shows_the_code_recorded_by_the_mcf(self):
        from dgi_compliance.edef.invoice_print import dgi_invoice_context
        si = self.normalized_by_user()
        general = dgi_invoice_context(si)["general"]
        self.assertEqual(general["operator_id"], MCF_CODE)
        self.assertEqual(general["operator_name"], "Caisse 543")

    def test_O03_operator_falls_back_to_the_creator(self):
        from dgi_compliance.fiscal.operations import operator_user
        si = self.normalized_by_user()
        op = self.op_of(si)
        op.requested_by = "Guest"
        self.assertEqual(operator_user(op), si.owner)
        op.requested_by = "nobody.543@test.local"                          # not a User
        self.assertEqual(operator_user(op), si.owner)

    def test_O04_patch_repairs_invoices_normalized_before_543(self):
        from dgi_compliance.patches.v5_4 import operateur_mcf
        si = self.normalized_by_user()
        frappe.db.set_value("Sales Invoice", si.name, "custom_dgi_operator", MCF_CODE, update_modified=False)
        before = frappe.db.count("DGI Gateway Event", {"event_code": "OPERATEUR_CORRIGE"})
        operateur_mcf.execute()
        si = self.reload(si)
        self.assertEqual(si.custom_dgi_operator, USER)
        self.assertEqual(frappe.db.count("DGI Gateway Event", {"event_code": "OPERATEUR_CORRIGE"}), before + 1)
        operateur_mcf.execute()                                            # idempotent
        self.assertEqual(frappe.db.count("DGI Gateway Event", {"event_code": "OPERATEUR_CORRIGE"}), before + 1)
        si.submit()
        self.assertEqual(self.reload(si).docstatus, 1)
