"""Integration test v5.5.2 (option Matrice E) - a credit note may repeat the TAX lines of the invoice.

    bench --site <site> run-tests --module dgi_compliance.tests.test_avoirs_tax_552

  W04  total credit note (RAN) of a sale with a TVA specifique line (N, type TAX): accepted and normalized
"""
import frappe

from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_hl1_emcf import HL1Base


class TestAvoirsTax(HL1Base):
    def test_W04_credit_note_with_tax_line(self):
        from dgi_compliance.patches.v5_5 import avoirs_lignes_tax
        avoirs_lignes_tax.execute()
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-M-ESSENCE", 10, 2390), ("DGI-N-TVA-ESS", 10, 250)], submit=True)
        si = self.assertNormalized(si)
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
        ret = make_sales_return(si.name)
        ret.custom_dgi_reference_type = "RAN"
        ret.insert(ignore_permissions=True)
        ret.submit()
        ret = self.assertNormalized(ret)
        req = self.request_of(ret)
        self.assertEqual((req["type"], req["referenceType"]), ("FA", "RAN"))
        self.assertEqual(sorted(i["taxGroup"] for i in req["items"]), ["M", "N"])
        self.assertEqual(self.response_of(ret)["vtotal"], 2500.0)
