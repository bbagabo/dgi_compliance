"""Pure unit tests (no database) - homologated scope, Note de service n°01/220 VI.2 (v5.5.1).

    python -m pytest dgi_compliance/tests/test_homologation_scope.py
"""
import unittest

from dgi_compliance.edef.homologation import (
    HOMOLOGATED, NOT_HOMOLOGATED, normalize_version, parse_groups, parse_types, scope_errors,
)


def dto(kind="FV", *groups):
    return {"type": kind, "items": [{"name": f"X{g}", "taxGroup": g} for g in groups]}


class TestParsing(unittest.TestCase):
    def test_groups_normalized(self):
        self.assertEqual(parse_groups("s, b a;Q b"), "ABQS")
        self.assertEqual(parse_groups("abcdefghijklmnopqrs"), "ABCDEFGHIJKLMNOPQRS")
        self.assertEqual(parse_groups(""), "")

    def test_reserved_and_unknown_groups_refused(self):
        with self.assertRaisesRegex(ValueError, "reserves"):
            parse_groups("ABT")
        with self.assertRaisesRegex(ValueError, "reserves"):
            parse_groups("Z")

    def test_types_normalized(self):
        self.assertEqual(parse_types("fa, fv ev"), ["FV", "FA", "EV"])
        with self.assertRaisesRegex(ValueError, "inconnus"):
            parse_types("FV,XX")


class TestScope(unittest.TestCase):
    def test_no_restriction_before_the_homologation(self):
        for status in (NOT_HOMOLOGATED, None, ""):
            self.assertEqual(scope_errors(dto("ET", "Q", "S"), status, "AB", "FV"), [])

    def test_inside_the_scope(self):
        self.assertEqual(scope_errors(dto("FV", "A", "B", "S"), HOMOLOGATED, "ABS", "FV,FA"), [])

    def test_group_outside_the_scope(self):
        errors = scope_errors(dto("FV", "B", "Q", "R"), HOMOLOGATED, "ABS", "FV")
        self.assertEqual(len(errors), 1)
        self.assertIn("QR", errors[0])
        self.assertIn("re-homologation", errors[0])

    def test_invoice_type_outside_the_scope(self):
        errors = scope_errors(dto("EV", "B"), HOMOLOGATED, "ABCDEFGHIJKLMNOPQRS", "FV,FA")
        self.assertEqual(len(errors), 1)
        self.assertIn("EV", errors[0])

    def test_badly_filled_scope_blocks(self):
        errors = scope_errors(dto("FV", "B"), HOMOLOGATED, "ABT", "FV")
        self.assertEqual(len(errors), 1)
        self.assertIn("mal renseigne", errors[0])



class TestVersionHL(unittest.TestCase):
    """Numbering HL (HL v1.0.0 = former 5.5.2): the attestation may write the version in any usual form."""

    def test_forms_of_the_version(self):
        for text in ("HL v1.0.0", "HLv1.0.0", "hl V1.0.0", "v1.0.0", "1.0.0", " HL 1.0.0 "):
            self.assertEqual(normalize_version(text), "1.0.0", text)
        self.assertEqual(normalize_version("HL v1.1.0"), "1.1.0")
        self.assertEqual(normalize_version(""), "")
        self.assertEqual(normalize_version(None), "")

    def test_application_version_is_hl_1(self):
        from dgi_compliance import __version__
        self.assertEqual(__version__.split(".")[0], "1")



if __name__ == "__main__":
    unittest.main()
