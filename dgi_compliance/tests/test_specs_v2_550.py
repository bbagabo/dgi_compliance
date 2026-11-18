"""Integration tests v5.5.0 - DGI specifications v2 (October 2026).

Run on a TEST site (data is committed by the normalization flow):
    bench --site <site> set-config allow_tests true
    bench --site <site> run-tests --module dgi_compliance.tests.test_specs_v2_550

The e-MCF is simulated with the API v2 field names (tests/emcf_simulator.py: trq..trz, ts = specific tax).
  V01  item hors champ -> group S with its real item code (A is now "Exonere")
  V02  option "communique n°010" -> group A + code HORS CHAMP
  V03  item hors champ on a VAT template -> refused (no silent change of group)
  V04  groups B (specific tax) + Q + R + S normalized on an e-MCF v2 (rates read in trq..trz, not in ts)
  V05  reserved groups T-Z: refused in the mapping, in the request, catalogue reserved and inactive
  V06  e-MCF answer without the rate of a group -> rate of the local catalogue
  V07  MCF channel: group Q on an MCF of protocol v1 -> refused with the protocol v2 message
  V08  migration patch: idempotent, written to the journal
"""
import json

import frappe
from frappe.utils import flt

from dgi_compliance.tests import fixtures as fx
from dgi_compliance.tests.test_hl1_emcf import HL1Base, reload


class TestSpecificationsV2(HL1Base):
    def _settings_value(self, field, value):
        frappe.db.set_single_value("DGI Compliance Settings", field, value)
        frappe.clear_cache(doctype="DGI Compliance Settings")

    def tearDown(self):
        self._settings_value("hors_champ_rule", "Groupe S (specifications v2)")
        frappe.db.set_value("Item", "DGI-B-LAIT", "dgi_hors_champ", 0)
        frappe.db.commit()

    # ---------- V01 ----------
    def test_V01_hors_champ_in_group_s(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-A-HC", 1, 5000), ("DGI-S-HC", 2, 1500),
                                                ("DGI-A-EAU", 1, 1500)], submit=True)
        si = self.assertNormalized(si)
        items = self.request_of(si)["items"]
        self.assertEqual([(i["taxGroup"], i["code"]) for i in items],
                         [("S", "DGI-A-HC"), ("S", "DGI-S-HC"), ("A", "DGI-A-EAU")])
        resp = self.response_of(si)
        self.assertEqual(resp["tas"], 8000.0)
        self.assertEqual(resp["taa"], 1500.0)
        from dgi_compliance.edef.invoice_print import dgi_invoice_context
        ctx = dgi_invoice_context(reload(si))
        self.assertIn("S", json.dumps(ctx, default=str))

    # ---------- V02 ----------
    def test_V02_rule_of_the_communique_010_as_an_option(self):
        self._settings_value("hors_champ_rule", "Groupe A + code HORS CHAMP (communique n°010)")
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-A-HC", 1, 5000)], submit=True)
        si = self.assertNormalized(si)
        item = self.request_of(si)["items"][0]
        self.assertEqual((item["taxGroup"], item["code"]), ("A", "HORS CHAMP"))

    # ---------- V03 ----------
    def test_V03_hors_champ_on_a_vat_item_is_refused(self):
        frappe.db.set_value("Item", "DGI-B-LAIT", "dgi_hors_champ", 1)
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-B-LAIT", 1, 10000)])
        with self.assertRaises(frappe.ValidationError) as ctx:
            si.submit()
        self.assertIn("Hors champ", str(ctx.exception))
        self.assertEqual(reload(si).docstatus, 0)

    # ---------- V04 ----------
    def test_V04_groups_b_ts_q_r_s_on_an_emcf_v2(self):
        si = fx.make_invoice("CLIENT PM SARL", [("DGI-TS-BIERE", 3, 2730), ("DGI-Q-BOISSON", 4, 2500),
                                                ("DGI-R-MARCHE", 1, 10000), ("DGI-S-HC", 1, 5000)], submit=True)
        si = self.assertNormalized(si)
        resp = self.response_of(si)
        self.assertEqual((resp["trq"], resp["trr"], resp["trs"]), (8, 8, 0))
        self.assertEqual(resp["ts"], 690.0)              # specific tax, not the rate of group S
        self.assertEqual(resp["vaq"], 800.0)
        self.assertEqual(resp["var"], 800.0)
        self.assertAlmostEqual(resp["total"], flt(si.base_grand_total), delta=1)
        self.assertEqual(self.sim.count("PUT", "/cancel"), 0)

    # ---------- V05 ----------
    def test_V05_reserved_groups(self):
        for code in "TUVWXYZ":
            row = frappe.db.get_value("DGI Tax Group", code, ["is_reserved", "is_active", "description"], as_dict=True)
            self.assertTrue(row, code)
            self.assertEqual((row.is_reserved, row.is_active), (1, 0), code)
        t = frappe.get_doc("DGI Tax Group", "T")
        t.is_active = 1
        t.save(ignore_permissions=True)
        self.assertEqual(reload_tg("T").is_active, 0)
        s = frappe.get_doc("DGI Compliance Settings")
        s.append("tax_group_map", {"item_tax_template": fx._itt("DGI B 16"), "edef_tax_group": "T", "priority": 5})
        with self.assertRaises(frappe.ValidationError):
            s.save(ignore_permissions=True)
        from dgi_compliance.edef.mapper import ReservedTaxGroupError, check_applicable_group, validate_invoice_request
        with self.assertRaises(ReservedTaxGroupError):
            check_applicable_group("Z")
        check_applicable_group("S")
        errors = validate_invoice_request({"nif": "A0700190Q", "isf": "ABC-DEF-01", "mode": "ht", "type": "FV",
                                           "operator": {"name": "x"},
                                           "items": [{"name": "X", "price": 1, "quantity": 1, "taxGroup": "T"}]})
        self.assertTrue(any("reserve" in e for e in errors), errors)

    # ---------- V06 ----------
    def test_V06_missing_rate_in_the_answer_uses_the_catalogue(self):
        from dgi_compliance.dgi_compliance.doctype.dgi_compliance_settings.dgi_compliance_settings import get_settings
        from dgi_compliance.edef.tasks import verify_amounts
        dto = {"mode": "ht", "items": [{"price": 1000, "quantity": 1, "taxGroup": "Q"},
                                       {"price": 1000, "quantity": 1, "taxGroup": "B"}]}
        resp = {"tb": 16, "total": 2240, "vtotal": 240, "tab": 1160, "hab": 1000, "vab": 160,
                "taq": 1080, "haq": 1000, "vaq": 80}          # trq absent
        doc = frappe._dict(base_grand_total=2240)
        self.assertEqual(verify_amounts(doc, dto, resp, get_settings()), [])

    # ---------- V07 ----------
    def test_V07_mcf_of_protocol_v1_refuses_group_q(self):
        from dgi_compliance.fiscal import mcf_payload
        route = frappe._dict(device="MCF-V1-TEST", nif=None)
        orig = mcf_payload._device_groups
        mcf_payload._device_groups = lambda r: set("ABCDEFGHIJKLMNOP")
        try:
            si = fx.make_invoice("CLIENT PM SARL", [("DGI-Q-BOISSON", 1, 2500)])
            with self.assertRaises(mcf_payload.MCFPayloadError) as ctx:
                mcf_payload.build_mcf_invoice(si, frappe.get_cached_doc("DGI Compliance Settings"), route)
            self.assertIn("protocole MCF-SFE v2", str(ctx.exception))
        finally:
            mcf_payload._device_groups = orig

    # ---------- V08 ----------
    def test_V08_patch_idempotent_and_logged(self):
        from dgi_compliance.patches.v5_5 import specifications_v2
        before = frappe.db.count("DGI Gateway Event", {"event_code": "SPECS_V2"})
        specifications_v2.execute()
        specifications_v2.execute()
        self.assertEqual(frappe.db.count("DGI Gateway Event", {"event_code": "SPECS_V2"}), before + 2)
        self.assertEqual(frappe.db.count("DGI Tax Group", {"is_reserved": 1, "is_active": 0}), 7)
        self.assertNotIn("champ", (frappe.db.get_value("DGI Tax Group", "A", "description") or "").lower())


def reload_tg(code):
    return frappe.get_doc("DGI Tax Group", code)
