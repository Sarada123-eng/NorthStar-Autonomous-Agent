import unittest

from app.database import CUSTOMERS, _canonical_name, _loose_customer_matches


class LooseCustomerMatchingTests(unittest.TestCase):
    def matches(self, name: str) -> list[str]:
        return [c["legal_name"] for c in _loose_customer_matches(name, [dict(c) for c in CUSTOMERS])]

    def test_abbreviation_and_missing_suffix_match_the_legal_entity(self):
        self.assertEqual(["Acme Corporation Inc."], self.matches("Acme Corp"))
        self.assertEqual(["Acme Corporation Inc."], self.matches("acme corporation"))
        self.assertEqual(["Globex Corporation LLC"], self.matches("Globex Corp."))

    def test_a_typed_legal_form_must_match(self):
        self.assertEqual(["Acme Corporation Inc."], self.matches("Acme Corp Inc"))
        self.assertEqual([], self.matches("Acme Corporation Ltd"))
        self.assertEqual([], self.matches("Globex Corporation Inc."))

    def test_unrelated_and_short_names_do_not_loosely_match(self):
        self.assertEqual([], self.matches("Initech Corp"))
        self.assertEqual([], self.matches("Acme Industries"))

    def test_canonical_name_splits_suffix(self):
        self.assertEqual(("acme corporation", "inc"), _canonical_name("Acme Corporation Inc."))
        self.assertEqual(("acme corporation", None), _canonical_name("Acme Corp"))
        self.assertEqual(("delta health", None), _canonical_name("Delta Health"))


if __name__ == "__main__":
    unittest.main()
