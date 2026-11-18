"""Pure unit tests (no database) - DGI specifications v2 (October 2026), v5.5.0.

    python -m pytest dgi_compliance/tests/test_specs_v2_calc.py
    bench --site <site> run-tests --module dgi_compliance.tests.test_specs_v2_calc

API e-MCF v2.0, InvoiceResponseDataDto: rates ta..tp (A-P) and trq..trz (Q-Z); totals taa..taz,
HT haa..haz, VAT vaa..vaz; 'ts' = specific tax amount (never the rate of group S).
FACNO groupes de taxation v2.0: 19 applicable groups A-S, T-Z reserved (Reserve 1..7).
"""
import unittest

from dgi_compliance.edef.calc import (
    APPLICABLE_GROUPS, HISTORIC_GROUPS, HORS_CHAMP_GROUP, RESERVED_GROUPS, compare, compute_totals,
    is_reserved_group, rate_field, reserved_label, response_groups, response_rates,
)


def v2_response():
    """e-MCF v2 answer for: B 16 % (2 x 2 500 HT + specific tax 230/unit), Q 8 % (1 000 HT), S (500)."""
    resp = {"uid": "ac33f8fe-9735-4ed6-a9c3-df58a908ccd3", "total": 7913.6, "vtotal": 953.6, "ts": 460.0}
    for letter in "abcdefghijklmnop":
        resp["t" + letter] = {"b": 16, "c": 5, "f": 16, "g": 5, "o": 1, "p": 1}.get(letter, 0)
    for letter in "qrstuvwxyz":
        resp["tr" + letter] = {"q": 8, "r": 8}.get(letter, 0)
    resp.update({"tab": 6333.6, "hab": 5460.0, "vab": 873.6,
                 "taq": 1080.0, "haq": 1000.0, "vaq": 80.0,
                 "tas": 500.0, "has": 500.0, "vas": 0.0})
    return resp


class TestGroupsV2(unittest.TestCase):
    def test_nomenclature(self):
        self.assertEqual(APPLICABLE_GROUPS, "ABCDEFGHIJKLMNOPQRS")
        self.assertEqual(HISTORIC_GROUPS, "ABCDEFGHIJKLMNOP")
        self.assertEqual(RESERVED_GROUPS, "TUVWXYZ")
        self.assertEqual(HORS_CHAMP_GROUP, "S")
        self.assertTrue(all(is_reserved_group(x) for x in "TUVWXYZtz"))
        self.assertFalse(any(is_reserved_group(x) for x in ["A", "Q", "R", "S", "", None]))
        self.assertEqual((reserved_label("T"), reserved_label("Z")), ("Reserve 1", "Reserve 7"))

    def test_rate_field_names_of_api_v2(self):
        self.assertEqual([rate_field(x) for x in "ABP"], ["ta", "tb", "tp"])
        self.assertEqual([rate_field(x) for x in "QRSZ"], ["trq", "trr", "trs", "trz"])


class TestResponseV2(unittest.TestCase):
    def test_rates_read_from_trq_trz_and_ts_is_not_the_rate_of_s(self):
        rates = response_rates(v2_response())
        self.assertEqual((rates["B"], rates["Q"], rates["R"], rates["S"]), (16.0, 8.0, 8.0, 0.0))
        self.assertEqual(len(rates), 26)
        groups = response_groups(v2_response())
        self.assertEqual(groups["S"], {"rate": 0.0, "total": 500.0, "ht": 500.0, "vat": 0.0})
        self.assertEqual(groups["Q"]["rate"], 8.0)

    def test_amount_check_with_group_s_and_a_specific_tax(self):
        """Before 5.5.0 the rate of S was read in 'ts' (460 here): the SFE expected a VAT on S, found a
        difference and cancelled a correct invoice."""
        resp = v2_response()
        items = [{"price": 2500, "quantity": 2, "taxGroup": "B", "taxSpecificAmount": 460},
                 {"price": 1000, "quantity": 1, "taxGroup": "Q"},
                 {"price": 500, "quantity": 1, "taxGroup": "S"}]
        expected = compute_totals(items, "ht", response_rates(resp))
        actual = {"total": resp["total"], "vtotal": resp["vtotal"],
                  "groups": {g: {"total": v["total"], "vat": v["vat"]} for g, v in response_groups(resp).items()}}
        self.assertEqual(compare(expected, actual, 1), [])
        self.assertEqual((expected["total"], expected["vtotal"]), (7913.6, 953.6))

    def test_v1_continuation_accepted_except_ts(self):
        resp = {"tb": 16, "tq": 8, "tr": 8, "ts": 230, "taq": 108, "vaq": 8, "tas": 50}
        rates = response_rates(resp)
        self.assertEqual((rates["Q"], rates["R"]), (8.0, 8.0))
        self.assertNotIn("S", rates)
        self.assertEqual(response_groups(resp)["S"]["rate"], 0.0)

    def test_v2_name_wins_over_v1_continuation(self):
        self.assertEqual(response_rates({"trq": 8, "tq": 99})["Q"], 8.0)


if __name__ == "__main__":
    unittest.main()
