"""Integration tests v5.0.x (HL1) - e-MCF flow, invoice print, X/Z/A reports.

Run on a TEST site (data is committed by the normalization flow):
    bench --site <site> set-config allow_tests true
    bench --site <site> run-tests --module dgi_compliance.tests.test_hl1_emcf

The e-MCF is simulated (tests/emcf_simulator.py): no request leaves the server.
Scenario ids T01..T26 follow the HL1 test plan; CTxx refer to the DGI official test cases.
"""
import json
import unittest

import frappe
from frappe.utils import flt

from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.emcf_simulator import EMCFSimulator, NIM


def reload(si):
    return frappe.get_doc("Sales Invoice", si.name)


class HL1Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frappe.set_user("Administrator")
        cls.sim = EMCFSimulator().start()
        fx.setup_all()
        from dgi_compliance.edef.sync import sync_all
        sync_all()
        frappe.db.commit()

    @classmethod
    def tearDownClass(cls):
        cls.sim.stop()
        frappe.db.commit()

    def setUp(self):
        frappe.set_user("Administrator")
        self.sim.faults.clear()
        self.sim.tamper_total = 0.0
        frappe.clear_cache(doctype="DGI Compliance Settings")

    def assertNormalized(self, si):
        si = reload(si)
        self.assertEqual(si.custom_dgi_status, "Normalized", si.custom_dgi_error)
        self.assertTrue(si.custom_dgi_code_def)
        self.assertEqual(si.docstatus, 1)
        return si

    def request_of(self, si):
        return json.loads(reload(si).custom_dgi_response)["request"]

    def response_of(self, si):
        return json.loads(reload(si).custom_dgi_response)["invoice"]


class TestNormalisation(HL1Base):
    # ---------- T01 / CT1 ----------
    def test_T01_standard_invoice(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 3, 10000), ("DGI-C-FARINE", 1, 45000)], submit=True)
        si = self.assertNormalized(si)
        req = self.request_of(si)
        self.assertEqual(req["type"], "FV")
        self.assertEqual(req["mode"], "ht")
        self.assertEqual([i["taxGroup"] for i in req["items"]], ["B", "C"])
        self.assertEqual(req["client"]["type"], "PM")
        self.assertEqual(req["operator"]["id"], "Administrator")
        self.assertEqual(req["payment"], [{"name": "ESPECES", "amount": flt(si.base_grand_total)}])
        resp = self.response_of(si)
        self.assertAlmostEqual(resp["total"], flt(si.base_grand_total), delta=1)
        self.assertEqual(si.custom_dgi_nim, NIM)
        self.assertTrue(si.custom_dgi_qr_image.startswith("data:image/png;base64,"))
        self.assertTrue(si.custom_dgi_normalized_on)

    # ---------- T02: several tax rates, groups Q and O (FACNO v2.0) ----------
    def test_T02_multiple_rates_and_new_groups(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 10000), ("DGI-C-FARINE", 1, 45000),
                                                ("DGI-A-EAU", 3, 1500), ("DGI-Q-BOISSON", 4, 2500),
                                                ("DGI-O-RIZ", 1, 98000)], submit=True)
        si = self.assertNormalized(si)
        resp = self.response_of(si)
        self.assertEqual(resp["vab"], 3200.0)
        self.assertEqual(resp["vaq"], 800.0)
        self.assertEqual(resp["vao"], 980.0)
        self.assertEqual(resp["vaa"], 0.0)

    # ---------- T03 / CT25: line discount ----------
    def test_T03_line_discount(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 10000, 10), ("DGI-A-EAU", 3, 1500)], submit=True)
        si = self.assertNormalized(si)
        item = self.request_of(si)["items"][0]
        self.assertEqual(item["price"], 9000.0)
        self.assertEqual(item["originalPrice"], 10000.0)
        self.assertEqual(item["priceModification"], "Remise 10%")

    # ---------- T04: global discount, HT and TTC ----------
    def test_T04_global_discount_ht(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 10000), ("DGI-C-FARINE", 1, 45000)],
                             apply_discount_on="Net Total", additional_discount_percentage=5, submit=True)
        si = self.assertNormalized(si)
        self.assertAlmostEqual(self.response_of(si)["total"], flt(si.base_grand_total), delta=1)

    def test_T04_global_discount_ttc(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 11600), ("DGI-C-FARINE", 1, 47250)],
                             template="DGI TVA TTC", apply_discount_on="Grand Total",
                             additional_discount_percentage=5, submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["mode"], "ttc")
        self.assertAlmostEqual(self.response_of(si)["total"], flt(si.base_grand_total), delta=1)

    def test_T04_ttc_prices_as_entered(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 3, 11600)], template="DGI TVA TTC", submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["items"][0]["price"], 11600.0)

    # ---------- T05: exempt invoice ----------
    def test_T05_exempt_invoice(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-A-EAU", 10, 1500)], submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.response_of(si)["vtotal"], 0.0)

    # ---------- T06 / CT26: USD invoice ----------
    def test_T06_usd_invoice(self):
        si = fx.make_invoice("CLIENT USD SARL", [("DGI-B-LAIT", 2, 10), ("DGI-C-FARINE", 1, 20)], currency="USD",
                             submit=True)
        si = self.assertNormalized(si)
        req = self.request_of(si)
        self.assertEqual(req["curCode"], "USD")
        self.assertEqual(req["curRate"], fx.USD_RATE)
        self.assertEqual(req["items"][0]["price"], 28500.0)
        self.assertEqual(req["payment"][0]["currencyCode"], "USD")
        self.assertNotIn("curCode", req["payment"][0])

    def test_T06_invoice_rate_is_transmitted(self):
        """v5.0.1: the rate sent to the e-MCF (curRate, payment currencyRate, CDF prices) is ALWAYS the
        exchange rate entered on the invoice, even far from the e-MCF reference rate (2 850) and even
        when an old Currency Rule says 'Manuel'. No +/-1 % control any more."""
        s = frappe.get_single("DGI Compliance Settings")
        saved = [r.as_dict() for r in s.currency_rules]
        s.append("currency_rules", {"currency": "USD", "invoice_type": "Any", "is_allowed": 1,
                                    "rate_source": "Manuel", "manual_rate": 3000})
        s.flags.ignore_mandatory = True
        s.save(ignore_permissions=True)
        try:
            si = fx.make_invoice("CLIENT USD SARL", [("DGI-B-LAIT", 1, 10)], currency="USD", rate=2350.5,
                                 submit=True)
            si = self.assertNormalized(si)
            req = self.request_of(si)
            self.assertEqual(req["curCode"], "USD")
            self.assertEqual(req["curRate"], 2350.5)
            self.assertEqual(req["items"][0]["price"], 23505.0)
            self.assertEqual(req["payment"][0]["currencyRate"], 2350.5)
            self.assertAlmostEqual(self.response_of(si)["total"], flt(si.base_grand_total), delta=1)
            ctx = frappe.get_attr("dgi_compliance.edef.invoice_print.dgi_invoice_context")(si)
            self.assertEqual(ctx["cur_rate_txt"], "2 350.50")
            html = frappe.get_print("Sales Invoice", si.name, "DGI Facture Normalisee")
            self.assertIn("Taux de change de la facture : 1 USD = 2 350.50 CDF", html)
        finally:
            s = frappe.get_single("DGI Compliance Settings")
            s.set("currency_rules", [{k: r.get(k) for k in ("currency", "invoice_type", "is_allowed",
                                                         "rate_source", "manual_rate", "notes")} for r in saved])
            s.flags.ignore_mandatory = True
            s.save(ignore_permissions=True)

    def test_T06_usd_payments_equal_fiscal_total(self):
        """Rate with decimals: ERPNext total 2 416 045.12 CDF, fiscal total (VAT rounded up) .13.
        The payments transmitted add up to the fiscal total (SFE 1.1.19)."""
        si = fx.make_invoice("CLIENT USD SARL", [("DGI-B-LAIT", 2, 300), ("DGI-C-FARINE", 1, 150)],
                             currency="USD", rate=2830.75, submit=True)
        si = self.assertNormalized(si)
        req, resp = self.request_of(si), self.response_of(si)
        self.assertEqual(req["curRate"], 2830.75)
        self.assertEqual(resp["total"], 2416045.13)
        self.assertAlmostEqual(sum(p["amount"] for p in req["payment"]), resp["total"], places=2)
        self.assertTrue(all(p["currencyRate"] == 2830.75 for p in req["payment"]))

    def test_T06_usd_amount_mismatch_cancelled(self):
        """Amounts recomputed by the e-MCF differ from the SFE beyond the tolerance -> the request is
        cancelled (CANCEL) and the invoice is neither confirmed nor submitted."""
        self.sim.tamper_total = 150.0
        si = fx.make_invoice("CLIENT USD SARL", [("DGI-B-LAIT", 1, 10)], currency="USD", rate=2350.5)
        cancels, confirms = self.sim.count("PUT", "cancel"), self.sim.count("PUT", "confirm")
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        si = reload(si)
        self.assertEqual(si.docstatus, 0)
        self.assertIn("differents", si.custom_dgi_error)
        self.assertFalse(si.custom_dgi_uid)
        self.assertFalse(si.custom_dgi_code_def)
        self.assertEqual(len(self.sim.pending), 0)
        self.assertEqual(self.sim.count("PUT", "cancel"), cancels + 1)
        self.assertEqual(self.sim.count("PUT", "confirm"), confirms)

    # ---------- T07 / T08: POS payments ----------
    def test_T07_partial_payment_pos(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)], save=False, is_pos=1)
        si.append("payments", {"mode_of_payment": "Cash", "amount": 4000})
        si.insert(ignore_permissions=True)
        si.submit()
        si = self.assertNormalized(si)
        pay = self.request_of(si)["payment"]
        self.assertEqual(pay, [{"name": "ESPECES", "amount": 4000.0}, {"name": "CREDIT", "amount": 7600.0}])

    def test_T08_full_payment_two_modes(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)], save=False, is_pos=1)
        si.append("payments", {"mode_of_payment": "Cash", "amount": 6000})
        si.append("payments", {"mode_of_payment": "Credit Card", "amount": 5600})
        si.insert(ignore_permissions=True)
        si.submit()
        si = self.assertNormalized(si)
        pay = self.request_of(si)["payment"]
        self.assertEqual(sum(p["amount"] for p in pay), 11600.0)
        self.assertEqual({p["name"] for p in pay}, {"ESPECES", "CARTEBANCAIRE"})

    def test_T08_pos_invoice_submitted_directly(self):
        """POS flow: the invoice is inserted and submitted in one call (no draft in the database)."""
        si = fx.make_invoice("Jean Particulier", [("DGI-B-LAIT", 1, 10000)], save=False, is_pos=1)
        si.append("payments", {"mode_of_payment": "Cash", "amount": 11600, "account": fx.acc("Cash")})
        si.submit()
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["payment"], [{"name": "ESPECES", "amount": 11600.0}])

    def test_T08_direct_submit_failure_leaves_no_orphan(self):
        """Accounting failure on a one-call submission: nothing may be registered at the DGI."""
        posts = self.sim.count("POST", "/api/invoice")
        si = fx.make_invoice("Jean Particulier", [("DGI-B-LAIT", 1, 10000)], save=False, is_pos=1)
        si.append("payments", {"mode_of_payment": "Cash", "amount": 11600})  # no account -> GL error
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        frappe.db.rollback()
        self.assertEqual(self.sim.count("POST", "/api/invoice"), posts)

    def test_T08_direct_submit_emcf_down_rolls_back(self):
        self.sim.inject("POST", "/api/invoice", "http500")
        si = fx.make_invoice("Jean Particulier", [("DGI-B-LAIT", 1, 10000)], save=False, is_pos=1)
        si.append("payments", {"mode_of_payment": "Cash", "amount": 11600, "account": fx.acc("Cash")})
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        frappe.db.rollback()
        self.assertFalse(frappe.db.exists("Sales Invoice", {"name": si.name, "docstatus": 1}))

    # ---------- T09: credit invoice / explicit mode / default ----------
    def test_T09_selected_mode_replaces_default(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)], dgi_mode_of_payment="Wire Transfer",
                             submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["payment"][0]["name"], "VIREMENT")
        ctx = self._ctx(si)
        self.assertIn("Wire Transfer", ctx["general"]["payment_modes"])

    def test_T09_unmapped_mode_blocks(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)], dgi_mode_of_payment="Bank Draft")
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        self.assertEqual(reload(si).docstatus, 0)

    # ---------- T10 / T11 / T12: credit notes and cancellation ----------
    def _normalized_sale(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 4, 10000), ("DGI-C-FARINE", 2, 45000)], submit=True)
        return self.assertNormalized(si)

    def test_T10_total_credit_note_ran(self):
        si = self._normalized_sale()
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
        ret = make_sales_return(si.name)
        ret.custom_dgi_reference_type = "RAN"
        ret.insert(ignore_permissions=True)
        ret.submit()
        ret = self.assertNormalized(ret)
        req = self.request_of(ret)
        self.assertEqual(req["type"], "FA")
        self.assertEqual(req["reference"], si.custom_dgi_code_def)
        self.assertEqual(req["referenceType"], "RAN")
        self.assertTrue(all(i["quantity"] > 0 and i["price"] > 0 for i in req["items"]))

    def test_T11_partial_credit_note_ram(self):
        si = self._normalized_sale()
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
        ret = make_sales_return(si.name)
        ret.custom_dgi_reference_type = "RAM"
        ret.remove(ret.items[1])
        ret.items[0].qty = -1
        ret.insert(ignore_permissions=True)
        ret.submit()
        self.assertNormalized(ret)

    def test_T11_credit_note_price_must_match(self):
        si = self._normalized_sale()
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
        ret = make_sales_return(si.name)
        ret.custom_dgi_reference_type = "RAN"
        ret.items[0].rate = 9000
        ret.items[0].price_list_rate = 9000
        with self.assertRaises(frappe.ValidationError):
            ret.insert(ignore_permissions=True)

    def test_T11_credit_note_nature_required(self):
        si = self._normalized_sale()
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
        ret = make_sales_return(si.name)
        with self.assertRaises(frappe.ValidationError):
            ret.insert(ignore_permissions=True)

    def test_T12_cancel_normalized_invoice_blocked(self):
        si = self._normalized_sale()
        with self.assertRaises(frappe.ValidationError):
            reload(si).cancel()
        self.assertEqual(reload(si).docstatus, 1)

    def test_CT13_rrr_without_original(self):
        ret = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", -1, 1000)], save=False, is_return=1,
                              custom_dgi_reference_type="RRR")
        ret.insert(ignore_permissions=True)
        ret.submit()
        ret = self.assertNormalized(ret)
        self.assertEqual(self.request_of(ret)["reference"], "RRR")

    # ---------- T13 / T14: customers ----------
    def test_T13_customer_without_nif_pp(self):
        si = fx.make_invoice("Jean Particulier", [("DGI-B-LAIT", 1, 10000)], submit=True)
        si = self.assertNormalized(si)
        client = self.request_of(si)["client"]
        self.assertEqual(client["type"], "PP")
        self.assertNotIn("nif", client)

    def test_T14_customer_with_nif_pc_pl(self):
        for name, code in (("Marie Commercante", "PC"), ("Cabinet Avocat Liberal", "PL")):
            si = fx.make_invoice(name, [("DGI-B-SERV", 1, 50000)], submit=True)
            si = self.assertNormalized(si)
            self.assertEqual(self.request_of(si)["client"]["type"], code)
            self.assertTrue(self.request_of(si)["client"]["nif"])

    def test_T14_pm_without_nif_blocked(self):
        cust = frappe.db.get_value("Customer", {"customer_name": "CLIENT PM SARL"})
        frappe.db.set_value("Customer", cust, "tax_id", None)
        try:
            with self.assertRaises(frappe.ValidationError):
                fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        finally:
            frappe.db.set_value("Customer", cust, "tax_id", "A1234567B")
            frappe.db.commit()

    def test_CT14_ao_requires_exemption_reference(self):
        with self.assertRaises(frappe.ValidationError):
            fx.make_invoice("Ambassade de Test", [("DGI-D-EQUIP", 2, 30000)])
        si = fx.make_invoice("Ambassade de Test", [("DGI-D-EQUIP", 2, 30000)], dgi_comment_a="EXO/DGI/2026/001",
                             submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["cmta"], "EXO/DGI/2026/001")
        self.assertEqual(self._ctx(si)["comments"][0]["label"], "Réf. Exo.")

    # ---------- T15..T21: availability, errors, duplicates, retry ----------
    def test_T15_emcf_unavailable(self):
        self.sim.inject("POST", "/api/invoice", "http500")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        si = reload(si)
        self.assertEqual(si.docstatus, 0)
        self.assertEqual(si.custom_dgi_status, "Error")
        self.assertFalse(si.custom_dgi_code_def)

    def test_T16_timeout_with_lost_response_no_duplicate(self):
        self.sim.inject("POST", "/api/invoice", "lost")
        before = self.sim.count("PUT", "confirm")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        posts_before = self.sim.count("POST", "/api/invoice")
        si.submit()
        si = self.assertNormalized(si)
        self.assertEqual(self.sim.count("POST", "/api/invoice") - posts_before, 1)
        self.assertEqual(self.sim.count("PUT", "confirm") - before, 1)
        self.assertTrue(frappe.db.exists("DGI Exchange Log", {"reference_invoice": si.name,
                                                               "direction": "reuse-pending"}))

    def test_T17_invalid_response(self):
        self.sim.inject("POST", "/api/invoice", "invalid_json")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        self.assertEqual(reload(si).custom_dgi_status, "Error")

    def test_T18_authentication_error(self):
        self.sim.inject("POST", "/api/invoice", "http401")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        self.assertIn("jeton", (reload(si).custom_dgi_error or "").lower())

    def test_T19_missing_token(self):
        from unittest import mock
        from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import (
            DGIComplianceSettings)
        with mock.patch.object(DGIComplianceSettings, "get_token", return_value=""):
            si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
            with self.assertRaises(frappe.ValidationError):
                si.submit()
        self.assertIn("jeton", (reload(si).custom_dgi_error or "").lower())

    def test_T20_double_normalization(self):
        from dgi_compliance.edef.tasks import normalize_sales_invoice
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        posts = self.sim.count("POST", "/api/invoice")
        r1 = normalize_sales_invoice(si.name)
        r2 = normalize_sales_invoice(si.name)
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(r1["code_def"], r2["code_def"])
        self.assertEqual(self.sim.count("POST", "/api/invoice") - posts, 1)
        reload(si).submit()
        self.assertNormalized(si)

    def test_T21_retry_after_failure(self):
        self.sim.inject("POST", "/api/invoice", "timeout")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        si = reload(si)
        si.submit()
        self.assertNormalized(si)

    def test_T21_confirm_lost_is_uncertain(self):
        self.sim.inject("PUT", "confirm", "lost")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        si = reload(si)
        self.assertTrue(si.custom_dgi_error.startswith("CONFIRMATION INCERTAINE"))
        posts = self.sim.count("POST", "/api/invoice")
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        self.assertEqual(self.sim.count("POST", "/api/invoice"), posts, "no new request may be created")

    # ---------- T22: rounding / amounts check ----------
    def test_T22_fractional_quantities(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-VRAC", 2.375, 1999.99), ("DGI-B-VRAC", 3.5, 1333.33)],
                             submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["items"][0]["quantity"], 2.375)

    def test_T22_tampered_amounts_cancelled(self):
        self.sim.tamper_total = 150.0
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        si = reload(si)
        self.assertIn("differents", si.custom_dgi_error)
        self.assertFalse(si.custom_dgi_uid)
        self.assertEqual(len(self.sim.pending), 0)

    # ---------- T23: missing mandatory data ----------
    def test_T23_zero_price_line_blocked(self):
        with self.assertRaises(frappe.ValidationError):
            fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 0)])

    def test_T23_missing_isf(self):
        frappe.db.set_single_value("DGI Compliance Settings", "isf", "")
        frappe.clear_cache(doctype="DGI Compliance Settings")
        try:
            si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
            with self.assertRaises(frappe.ValidationError):
                si.submit()
            self.assertIn("isf", reload(si).custom_dgi_error.lower())
        finally:
            frappe.db.set_single_value("DGI Compliance Settings", "isf", "TST-ISF-01")
            frappe.clear_cache(doctype="DGI Compliance Settings")
            frappe.db.commit()

    # ---------- T24: permissions and protection ----------
    def test_T24_fiscal_fields_protected(self):
        si = self._normalized_sale()
        doc = reload(si)
        doc.custom_dgi_code_def = "FAKE-CODE"
        with self.assertRaises(frappe.ValidationError):
            doc.save()

    def test_T24_normalized_draft_frozen_and_not_deletable(self):
        from dgi_compliance.edef.tasks import normalize_sales_invoice
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        normalize_sales_invoice(si.name)
        doc = reload(si)
        doc.items[0].qty = 2
        with self.assertRaises(frappe.ValidationError):
            doc.save()
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc("Sales Invoice", si.name)
        reload(si).submit()

    def test_T24_user_without_permission(self):
        from dgi_compliance.edef.tasks import normalize_sales_invoice
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                normalize_sales_invoice(si.name)
        finally:
            frappe.set_user("Administrator")

    def test_T24_override_never_skips_normalization(self):
        self.sim.inject("POST", "/api/invoice", "http500")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)], dgi_validation_override=1)
        with self.assertRaises(frappe.ValidationError):
            si.submit()
        self.assertEqual(reload(si).docstatus, 0)

    # ---------- CT8 / CT29 / FACNO v2 ----------
    def test_CT8_specific_tax(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-TS-BIERE", 3, 2730), ("DGI-A-EAU", 1, 1500)], submit=True)
        si = self.assertNormalized(si)
        item = self.request_of(si)["items"][0]
        self.assertEqual(item["taxSpecificValue"], "230")
        self.assertEqual(item["taxSpecificAmount"], 690.0)
        self.assertEqual(item["price"], 2500.0)
        self.assertAlmostEqual(self.response_of(si)["total"], flt(si.base_grand_total), delta=1)

    def test_CT29_hors_champ_code(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-A-HC", 1, 5000)], submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["items"][0]["code"], "HORS CHAMP")

    def test_CT15_export_invoice(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-E-CAFE", 3, 20000)], custom_dgi_export=1,
                             dgi_validation_override=1, submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(self.request_of(si)["type"], "EV")
        self.assertEqual(self._ctx(si)["export"], "EXPORTATION")

    # ---------- T25: print ----------
    def _ctx(self, si):
        from dgi_compliance.edef.invoice_print import dgi_invoice_context
        return dgi_invoice_context(reload(si))

    def test_T25_print_cdf(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 3, 10000), ("DGI-A-EAU", 2, 1500)],
                             dgi_mode_of_payment="Cash", submit=True)
        html = frappe.get_print("Sales Invoice", si.name, "DGI Facture Normalisee")
        for text in ("FACTURE DE VENTE", "RCCM", "Référence ISF", "Code magasin", "Référence NID",
                     "Mode de paiement", "Terme de paiement", "Type de facture DGI", "CODE DEF/DGI",
                     "DEF Compteurs", "francs congolais", "Nombre d'articles", reload(si).custom_dgi_code_def):
            self.assertIn(text, html)
        pdf = frappe.get_print("Sales Invoice", si.name, "DGI Facture Normalisee", as_pdf=True)
        self.assertTrue(pdf[:4] == b"%PDF")

    def test_T25_print_usd(self):
        si = fx.make_invoice("CLIENT USD SARL", [("DGI-B-LAIT", 2, 100), ("DGI-C-FARINE", 1, 50)], currency="USD",
                             submit=True)
        ctx = self._ctx(si)
        self.assertTrue(ctx["foreign"])
        self.assertEqual(ctx["totals"]["currency"], "USD")
        self.assertAlmostEqual(ctx["totals"]["ttc"], flt(reload(si).grand_total), delta=0.01)
        self.assertAlmostEqual(ctx["cdf"]["ttc"], self.response_of(si)["total"], delta=0.01)
        self.assertIn("francs congolais", ctx["amount_in_words"])
        html = frappe.get_print("Sales Invoice", si.name, "DGI Facture Normalisee")
        self.assertIn("Équivalent en Francs Congolais", html)

    # ---------- T26: upgrade safety ----------
    def test_T26_upgrade_safe_customizations(self):
        for cf in ("Sales Invoice-dgi_mode_of_payment", "Sales Invoice-custom_dgi_response",
                   "Sales Invoice Item-dgi_specific_tax", "Company-dgi_rccm"):
            self.assertTrue(frappe.db.exists("Custom Field", cf), cf)
        self.assertEqual(frappe.db.get_value("Property Setter", {"doc_type": "Sales Invoice",
                         "property": "default_print_format"}, "value"), "DGI Facture Normalisee")
        hooks = frappe.get_hooks("doc_events")["Sales Invoice"]
        self.assertIn("dgi_compliance.edef.tasks.before_sales_invoice_submit", hooks["before_submit"])
        self.assertFalse(frappe.db.exists("DocType", {"name": "Sales Invoice", "custom": 1}))


class TestFiscalReports(HL1Base):
    def test_R01_R02_R03_reports(self):
        sale = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 10000), ("DGI-A-EAU", 1, 1500)], submit=True)
        sale = self.assertNormalized(sale)
        x = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": "X quotidien",
                            "company": fx.COMPANY}).insert(ignore_permissions=True)
        data = json.loads(x.report_data)
        self.assertGreaterEqual(data["by_type"]["FV"]["count"], 1)
        z = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": "Z",
                            "company": fx.COMPANY}).insert(ignore_permissions=True)
        self.assertTrue(z.name.startswith("Z-"))
        z2 = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": "Z",
                             "company": fx.COMPANY}).insert(ignore_permissions=True)
        self.assertEqual(z2.sequence_no, z.sequence_no + 1)
        self.assertEqual(json.loads(z2.report_data)["summary"]["count"], 0)
        a = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": "A",
                            "company": fx.COMPANY}).insert(ignore_permissions=True)
        self.assertTrue(any(r["code"] == "DGI-B-LAIT" for r in json.loads(a.report_data)["articles"]))
        for doc in (x, z, a):
            html = frappe.get_print("DGI Rapport Fiscal", doc.name, "DGI Rapport Fiscal")
            self.assertIn(fx.COMPANY, html)
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc("DGI Rapport Fiscal", z.name)
        z.reload()
        with self.assertRaises(frappe.ValidationError):
            z.save()

    def test_R04_print_layout_dgi_model(self):
        """v5.0.2: the report prints with the DGI layout (e-UF model), never the raw JSON."""
        self.assertEqual(frappe.get_meta("DGI Rapport Fiscal").default_print_format, "DGI Rapport Fiscal")
        self.assertTrue(frappe.get_meta("DGI Rapport Fiscal").get_field("report_data").print_hide)
        fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 2, 10000), ("DGI-A-EAU", 1, 1500)], submit=True)
        x = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": "X périodique",
                            "company": fx.COMPANY, "from_datetime": "2026-01-01 00:00:00"}).insert(
                                ignore_permissions=True)
        # Default print format of the DocType (no format passed), as the Print button does.
        html = frappe.get_print("DGI Rapport Fiscal", x.name)
        self.assertNotIn('"by_type"', html)
        self.assertNotIn("Donnees du rapport", html)
        for label in ("X rapport périodique", "Section 1: Totaux par type de facture",
                      "Section 2: Totaux par groupe de taxation par type de facture",
                      "Section 3: Totaux par type de paiement", "Section 4: Réductions", "Section 5:",
                      "Date du rapport", "Début de période", "Fin de période", "ISF", "NIF"):
            self.assertIn(label, html)
        view = frappe.get_attr("dgi_compliance.dgi_compliance.doctype.dgi_rapport_fiscal."
                               "dgi_rapport_fiscal.report_view")(x)
        # Every invoice type lists the 19 groups A..S, even without invoices (DGI model).
        self.assertEqual([t["type"] for t in view["types"]], ["FV", "FT", "FA", "EV", "ET", "EA"])
        self.assertTrue(all([g["group"] for g in t["groups"]][:19] == list("ABCDEFGHIJKLMNOPQRS")
                            for t in view["types"]))
        fv = view["types"][0]
        self.assertRegex(fv["total"], r"^\d{1,3}(\.\d{3})*,\d{2}$")
        self.assertIn("ESPECES", [p["label"] for p in view["payments"]])
        a = frappe.get_doc({"doctype": "DGI Rapport Fiscal", "report_type": "A",
                            "company": fx.COMPANY}).insert(ignore_permissions=True)
        html_a = frappe.get_print("DGI Rapport Fiscal", a.name)
        self.assertIn(f"A rapport #{a.sequence_no}", html_a)
        for label in ("Code", "Désignation", "Prix", "Groupe de taxation", "Taux d'impôt", "Quantité vendue",
                      "Quantité retournée", "Quantité en stock"):
            self.assertIn(label, html_a)
        self.assertNotIn('"articles"', html_a)


class TestReportNumberFormat(unittest.TestCase):
    def test_dgi_number_format(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_rapport_fiscal.dgi_rapport_fiscal import _num, _qty
        self.assertEqual(_num(59202298067.07), "59.202.298.067,07")
        self.assertEqual(_num(0), "0,00")
        self.assertEqual(_num(-1500.5), "-1.500,50")
        self.assertEqual(_qty(2.375), "2,375")
        self.assertEqual(_qty(10), "10")
        self.assertEqual(_qty(1234.5), "1.234,5")
