"""Pure unit tests (no database) - v5.5.2: group N (TVA specifique) and the DGI table "Types d'article".

    python -m unittest dgi_compliance.tests.test_groupe_n_calc
    bench --site <site> run-tests --module dgi_compliance.tests.test_groupe_n_calc

SFE 1.5.8: the amount recorded in group N is entirely VAT. SFE 1.1.14: it increases the VAT total of the
invoice. FACNO v2.0: group M = regulated sale (only the amount excluding taxes is invoiced), group N = its
TVA specifique (only the VAT is invoiced). Example: 100 litres of petrol, 2 390 CDF excl. VAT + 250 CDF of
TVA specifique per litre (amounts for the test only).
"""
import unittest

from dgi_compliance.edef.calc import (
    TAX_ITEM_GROUPS, VAT_ONLY_GROUPS, commercial_tax_table, compute_totals, item_type_group_error,
    mcf_expected_totals,
)

FUEL_EMCF = [{"price": 2390, "quantity": 100, "taxGroup": "M"}, {"price": 250, "quantity": 100, "taxGroup": "N"}]
FUEL_MCF = [{"tax_group": "M", "tax_rate": 0, "amount": 239000.0}, {"tax_group": "N", "tax_rate": 0, "amount": 25000.0}]


def legacy_mcf_totals(items, mode):
    """MCF arithmetic of v5.5.1, kept to prove that nothing changes outside group N."""
    groups, ts = {}, 0.0
    for row in items:
        g = groups.setdefault(row["tax_group"], {"rate": float(row["tax_rate"]), "mon": 0.0})
        g["mon"] = round(g["mon"] + float(row["amount"]), 2)
        ts = round(ts + float(row.get("ts_total") or 0), 2)
    mv = mt = 0.0
    for g in groups.values():
        r = g["rate"]
        if mode == "TTC":
            vat = round(g["mon"] * r / (100.0 + r), 2) if r else 0.0
            mv = round(mv + g["mon"], 2)
        else:
            vat = round(g["mon"] * r / 100.0, 2) if r else 0.0
            mv = round(mv + g["mon"] + vat, 2)
        mt = round(mt + vat, 2)
    return {"mv": round(mv + ts, 2), "mt": mt, "ts": ts}


class TestGroupN(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(VAT_ONLY_GROUPS, ("N",))
        self.assertEqual(TAX_ITEM_GROUPS, ("L", "N"))

    def test_emcf_calc_counts_n_as_vat(self):
        for mode in ("ht", "ttc"):
            r = compute_totals(FUEL_EMCF, mode, {"M": 0, "N": 0})
            self.assertEqual((r["total"], r["vtotal"]), (264000.0, 25000.0), mode)
            self.assertEqual((r["groups"]["M"]["ht"], r["groups"]["M"]["vat"]), (239000.0, 0.0))
            self.assertEqual((r["groups"]["N"]["ht"], r["groups"]["N"]["vat"]), (0.0, 25000.0))

    def test_mcf_expected_totals_count_n_as_vat(self):
        for mode in ("HT", "TTC"):
            self.assertEqual(mcf_expected_totals(FUEL_MCF, mode), {"mv": 264000.0, "mt": 25000.0, "ts": 0.0}, mode)

    def test_mcf_expected_totals_with_vat_groups_and_n(self):
        items = [{"tax_group": "B", "tax_rate": 16, "amount": 10000.0},
                 {"tax_group": "M", "tax_rate": 0, "amount": 2390.0},
                 {"tax_group": "N", "tax_rate": 0, "amount": 250.0}]
        self.assertEqual(mcf_expected_totals(items, "HT"), {"mv": 14240.0, "mt": 1850.0, "ts": 0.0})
        self.assertEqual(mcf_expected_totals(items, "TTC"), {"mv": 12640.0, "mt": 1629.31, "ts": 0.0})

    def test_mcf_expected_totals_unchanged_without_n(self):
        cases = [
            [{"tax_group": "B", "tax_rate": 16, "amount": 35000.0}],
            [{"tax_group": "B", "tax_rate": 16, "amount": 7500.0, "ts_total": 690.0},
             {"tax_group": "Q", "tax_rate": 8, "amount": 10000.0}, {"tax_group": "S", "tax_rate": 0, "amount": 5000.0}],
            [{"tax_group": "C", "tax_rate": 5, "amount": 1999.99}, {"tax_group": "A", "tax_rate": 0, "amount": 380.0},
             {"tax_group": "L", "tax_rate": 0, "amount": 500.0}],
        ]
        for items in cases:
            for mode in ("HT", "TTC"):
                self.assertEqual(mcf_expected_totals(items, mode), legacy_mcf_totals(items, mode))

    def test_foreign_currency_table_prints_n_as_vat(self):
        rows = [{"group": "M", "label": "M", "rate": 0, "net": 85.36},
                {"group": "N", "label": "N", "rate": 0, "net": 8.93}]
        table = {t["group"]: t for t in commercial_tax_table(rows, 0.0)}
        self.assertEqual((table["M"]["ht"], table["M"]["vat"]), (85.36, 0.0))
        self.assertEqual((table["N"]["ht"], table["N"]["vat"], table["N"]["total"]), (0.0, 8.93, 8.93))

    def test_foreign_currency_table_allocation_unchanged(self):
        rows = [{"group": "B", "label": "B", "rate": 16, "net": 100.0},
                {"group": "C", "label": "C", "rate": 5, "net": 50.0},
                {"group": "N", "label": "N", "rate": 0, "net": 3.0},
                {"group": "A", "label": "A-EX", "rate": 0, "net": 20.0}]
        table = {t["group"]: t for t in commercial_tax_table(rows, 18.5)}
        self.assertEqual(round(table["B"]["vat"] + table["C"]["vat"] + table["A"]["vat"], 2), 18.5)
        self.assertEqual((table["B"]["vat"], table["C"]["vat"]), (16.0, 2.5))
        self.assertEqual((table["N"]["ht"], table["N"]["vat"]), (0.0, 3.0))
        self.assertEqual([t["group"] for t in commercial_tax_table(rows, 18.5)], ["A", "B", "C", "N"])


class TestItemTypes(unittest.TestCase):
    def test_dgi_table(self):
        for group in "ABCDEFGHIJKMOPQRS":
            self.assertIsNone(item_type_group_error("BIE", group), group)
            self.assertIsNone(item_type_group_error("SER", group), group)
            self.assertIn("reserve aux groupes L", item_type_group_error("TAX", group))
        for group in "LN":
            self.assertIsNone(item_type_group_error("TAX", group))
            self.assertIn("n'acceptent que", item_type_group_error("BIE", group))
            self.assertIn("n'acceptent que", item_type_group_error("SER", group))

    def test_lenient_inputs(self):
        self.assertIsNone(item_type_group_error(None, "N"))
        self.assertIsNone(item_type_group_error("TAX", ""))
        self.assertIsNone(item_type_group_error("tax", "n"))
        self.assertIsNotNone(item_type_group_error("bie", "l"))


if __name__ == "__main__":
    unittest.main()
