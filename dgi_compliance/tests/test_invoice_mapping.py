"""
Unit tests for the pure-Python mappers. These do not require a live Frappe
site — they validate logic that should not change unless DGI publishes a
new specification.

Run from the bench:

    bench --site mycompany.frappe.cloud run-tests --app dgi_compliance \
        --module dgi_compliance.tests.test_invoice_mapping
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch


class TestRedactToken(unittest.TestCase):
    def test_authorization_is_redacted(self):
        from dgi_compliance.utils.mapping import redact_token
        out = redact_token({"Authorization": "Bearer abc.def.ghi",
                            "Accept": "application/json"})
        self.assertEqual(out["Authorization"], "Bearer ***")
        self.assertEqual(out["Accept"], "application/json")


class TestInvoiceType(unittest.TestCase):
    def test_default_invoice_is_FV(self):
        from dgi_compliance.utils import mapping
        si = MagicMock()
        si.get.side_effect = lambda k, *a: None if k == "dgi_invoice_type" else False
        self.assertEqual(mapping.map_invoice_type(si), "FV")

    def test_return_invoice_is_FA_by_default(self):
        from dgi_compliance.utils import mapping
        si = MagicMock()
        si.get.side_effect = lambda k, *a: (
            None if k == "dgi_invoice_type" else
            (True if k == "is_return" else None)
        )
        self.assertEqual(mapping.map_invoice_type(si), "FA")

    def test_explicit_override_wins(self):
        from dgi_compliance.utils import mapping
        si = MagicMock()
        si.get.side_effect = lambda k, *a: "EV" if k == "dgi_invoice_type" else None
        self.assertEqual(mapping.map_invoice_type(si), "EV")


class TestTaxGroupResolution(unittest.TestCase):
    def test_default_when_no_template(self):
        from dgi_compliance.utils import mapping
        self.assertEqual(mapping.map_tax_group(None), "A")

    @patch("dgi_compliance.utils.mapping.frappe")
    def test_lookup_via_mapping(self, frappe_mock):
        frappe_mock.db.get_value.return_value = "B"
        from dgi_compliance.utils import mapping
        self.assertEqual(mapping.map_tax_group("Some Template"), "B")


if __name__ == "__main__":
    unittest.main()
