"""Integration tests v5.5.2 - group N (TVA specifique) and the DGI table "Types d'article".

Run on a TEST site (data is committed by the normalization flow):
    bench --site <site> set-config allow_tests true
    bench --site <site> run-tests --module dgi_compliance.tests.test_groupe_n_552

Sale of 100 litres of petrol: 2 390 CDF excl. VAT per litre in group M (vente reglementee) and 250 CDF of
TVA specifique per litre in group N (item of type TAX). Amounts for the test only.
  W01  e-MCF, invoice in CDF: N is VAT in the request, the e-MCF answer and the printed invoice
  W02  invoice in USD: the printed invoice counts N in the VAT total and prints it as VAT, not as HT
  W03  DGI table "Types d'article": BIE or SER refused in L and N, TAX refused outside L and N
"""
import frappe
from frappe.utils import flt

from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_hl1_emcf import HL1Base, reload

FUEL_CDF = [("DGI-M-ESSENCE", 100, 2390), ("DGI-N-TVA-ESS", 100, 250)]


def group(table, letter):
    return next((g for g in table if g["group"] == letter), None)


class TestGroupeN(HL1Base):
    # ---------- W01 ----------
    def test_W01_emcf_cdf_n_is_vat(self):
        si = fx.make_invoice("CLIENT PM SARL", FUEL_CDF, submit=True)
        si = self.assertNormalized(si)
        items = self.request_of(si)["items"]
        self.assertEqual([(i["taxGroup"], i["type"], i["price"], i["quantity"]) for i in items],
                         [("M", "BIE", 2390.0, 100.0), ("N", "TAX", 250.0, 100.0)])
        resp = self.response_of(si)
        self.assertEqual((resp["total"], resp["vtotal"]), (264000.0, 25000.0))
        from dgi_compliance.edef.invoice_print import dgi_invoice_context
        ctx = dgi_invoice_context(reload(si))
        self.assertEqual((ctx["totals"]["ttc"], ctx["totals"]["vat"], ctx["totals"]["ht"]), (264000.0, 25000.0, 239000.0))
        n = group(ctx["totals"]["table"], "N")
        self.assertEqual((n["ht"], n["vat"]), (0.0, 25000.0))

    # ---------- W02 ----------
    def test_W02_usd_invoice_prints_n_as_vat(self):
        si = fx.make_invoice("CLIENT USD SARL", [("DGI-M-ESSENCE", 100, 1), ("DGI-N-TVA-ESS", 100, 0.1)],
                             currency="USD", submit=True)
        si = self.assertNormalized(si)
        self.assertEqual(flt(si.total_taxes_and_charges), 0.0)       # ERPNext: N is an item, not a tax row
        from dgi_compliance.edef.invoice_print import dgi_invoice_context
        ctx = dgi_invoice_context(reload(si))
        self.assertTrue(ctx["foreign"])
        self.assertEqual((ctx["totals"]["ttc"], ctx["totals"]["vat"], ctx["totals"]["ht"]), (110.0, 10.0, 100.0))
        n = group(ctx["totals"]["table"], "N")
        self.assertEqual((n["ht"], n["vat"]), (0.0, 10.0))
        self.assertAlmostEqual(ctx["cdf"]["vat"], self.response_of(si)["vtotal"], delta=1)

    # ---------- W03 ----------
    def test_W03_item_types_of_the_dgi_table(self):
        from dgi_compliance.edef.mapper import validate_invoice_request
        base = {"nif": "A0700190Q", "isf": "ABC-DEF-01", "mode": "ht", "type": "FV", "operator": {"name": "x"}}
        for itype, grp, ok in (("BIE", "M", True), ("TAX", "N", True), ("TAX", "L", True), ("BIE", "N", False),
                               ("SER", "L", False), ("TAX", "B", False)):
            errors = validate_invoice_request(dict(base, items=[{"name": "X", "type": itype, "taxGroup": grp,
                                                                 "price": 100, "quantity": 1}]))
            self.assertEqual(not any("Types d'article" in e for e in errors), ok, (itype, grp, errors))
        frappe.db.set_value("Item", "DGI-L-ACCISE", "dgi_item_type", "BIE")
        try:
            si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000), ("DGI-L-ACCISE", 1, 500)])
            with self.assertRaises(frappe.ValidationError):
                si.submit()
            si = reload(si)
            self.assertEqual(si.docstatus, 0)
            self.assertIn("Types d'article", si.custom_dgi_error or "")
        finally:
            frappe.db.set_value("Item", "DGI-L-ACCISE", "dgi_item_type", "TAX")
            frappe.db.commit()
