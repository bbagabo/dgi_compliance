"""Pure unit tests (no database): fiscal arithmetic and amount in words."""
import unittest

from dgi_compliance.edef.calc import (
    ceil2, compute_totals, compare, group_amounts, parse_specific_tax, response_groups, round2,
    split_specific_tax,
)
from dgi_compliance.edef.words import amount_in_words_cdf, number_to_words


class TestCalc(unittest.TestCase):
    def test_rounding(self):
        self.assertEqual(round2(2.345), 2.35)
        self.assertEqual(round2(-2.345), -2.35)
        self.assertEqual(ceil2(1.231), 1.24)

    def test_group_ttc_tax_rounded_up(self):
        total, ht, vat = group_amounts(100, 16, "ttc", "B")
        self.assertEqual((total, ht, vat), (100.0, 86.2, 13.8))
        self.assertEqual(ht + vat, total)

    def test_group_ht(self):
        self.assertEqual(group_amounts(400, 16, "ht", "B"), (464.0, 400.0, 64.0))

    def test_group_n_is_vat(self):
        self.assertEqual(group_amounts(300, 0, "ht", "N"), (300.0, 0.0, 300.0))

    def test_compute_totals_example_proto(self):
        # Protocole MCF-SFE section 2: 5 x 720 (A) + LAIT 464 TTC (B 16%) = 4 064, TVA 64
        items = [{"price": 720, "quantity": 5, "taxGroup": "A"}, {"price": 464, "quantity": 1, "taxGroup": "B"}]
        res = compute_totals(items, "ttc", {"A": 0, "B": 16})
        self.assertEqual(res["total"], 4064.0)
        self.assertEqual(res["vtotal"], 64.0)
        self.assertEqual(res["groups"]["B"]["ht"], 400.0)

    def test_specific_tax(self):
        self.assertEqual(parse_specific_tax("230"), ("unit", 230.0))
        self.assertEqual(parse_specific_tax("10%"), ("percent", 10.0))
        self.assertEqual(split_specific_tax(8190, 3, "230"), (7500.0, 690.0, "230"))
        net, ts, val = split_specific_tax(1100, 1, "10%")
        self.assertEqual((round2(net), ts, val), (1000.0, 100.0, "10%"))
        with self.assertRaises(ValueError):
            parse_specific_tax("abc")

    def test_response_groups_and_compare(self):
        resp = {"tb": 16, "tab": 116, "hab": 100, "vab": 16, "total": 116, "vtotal": 16}
        groups = response_groups(resp)
        self.assertEqual(groups["B"]["vat"], 16.0)
        exp = {"total": 116, "vtotal": 16, "groups": {"B": {"total": 116, "vat": 16}}}
        self.assertEqual(compare(exp, {"total": 116, "vtotal": 16, "groups": {"B": {"total": 116, "vat": 16}}}, 0.01), [])
        self.assertTrue(compare(exp, {"total": 120, "vtotal": 16, "groups": {}}, 1))


class TestWords(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(amount_in_words_cdf(2950000), "Deux millions neuf cent cinquante mille francs congolais")
        self.assertEqual(amount_in_words_cdf(1), "Un franc congolais")
        self.assertEqual(amount_in_words_cdf(2000000), "Deux millions de francs congolais")
        self.assertEqual(amount_in_words_cdf(34991.5),
                         "Trente-quatre mille neuf cent quatre-vingt-onze francs congolais et cinquante centimes")

    def test_french_rules(self):
        self.assertEqual(number_to_words(80), "quatre-vingts")
        self.assertEqual(number_to_words(80000), "quatre-vingt mille")
        self.assertEqual(number_to_words(200), "deux cents")
        self.assertEqual(number_to_words(201), "deux cent un")
        self.assertEqual(number_to_words(71), "soixante et onze")
        self.assertEqual(number_to_words(1000), "mille")
        self.assertEqual(number_to_words(200000000), "deux cents millions")
