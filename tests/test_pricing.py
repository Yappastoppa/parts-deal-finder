"""Pure pricing computation tests. No supplier or network access."""
import unittest
from backend import pricing


class Pricing(unittest.TestCase):
    def test_parse_supplier_price(self):
        self.assertEqual(pricing.parse_supplier_price('$1,250.00'), 1250.0)
        self.assertIsNone(pricing.parse_supplier_price(None))
        self.assertIsNone(pricing.parse_supplier_price('Call for price'))
        self.assertIsNone(pricing.parse_supplier_price('$0'))

    def test_no_rule_uses_supplier_price(self):
        self.assertEqual(pricing.price_for(250.0, {'part': 'Spindle', 'make': 'BMW'}, [], None), 250.0)

    def test_global_percent_markup(self):
        rules = [{'scope_type': 'global', 'scope_value': '', 'markup_type': 'percent', 'markup_value': 20, 'min_margin': None}]
        self.assertEqual(pricing.price_for(250.0, {'part': 'Spindle', 'make': 'BMW'}, rules, None), 300.0)

    def test_part_rule_beats_global_rule(self):
        rules = [
            {'scope_type': 'global', 'scope_value': '', 'markup_type': 'percent', 'markup_value': 10, 'min_margin': None},
            {'scope_type': 'part', 'scope_value': 'Spindle', 'markup_type': 'fixed', 'markup_value': 75, 'min_margin': None},
        ]
        # store.pricing_rules() sorts part before global; simulate that ordering here.
        ordered = sorted(rules, key=lambda r: {'part': 0, 'make': 1, 'supplier': 2, 'global': 3}[r['scope_type']])
        self.assertEqual(pricing.price_for(250.0, {'part': 'Spindle', 'make': 'BMW'}, ordered, None), 325.0)

    def test_min_margin_floor(self):
        rules = [{'scope_type': 'global', 'scope_value': '', 'markup_type': 'fixed', 'markup_value': 5, 'min_margin': 50}]
        self.assertEqual(pricing.price_for(250.0, {'part': 'Spindle', 'make': 'BMW'}, rules, None), 300.0)

    def test_manual_override_wins_over_rules(self):
        rules = [{'scope_type': 'global', 'scope_value': '', 'markup_type': 'percent', 'markup_value': 20, 'min_margin': None}]
        self.assertEqual(pricing.price_for(250.0, {'part': 'Spindle', 'make': 'BMW'}, rules, {'manual_price': 329}), 329.0)

    def test_manual_override_without_supplier_price(self):
        self.assertEqual(pricing.price_for(None, {'part': 'Spindle', 'make': 'BMW'}, [], {'manual_price': 199.5}), 199.5)


if __name__ == '__main__':
    unittest.main()
