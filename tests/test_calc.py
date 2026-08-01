import unittest

from app.calc import CommissionRule, calc_treatment_yen


class CalcTests(unittest.TestCase):
    def test_hpb_discount_reduces_percent_commission_base(self):
        gross, discount, net, sales, payout = calc_treatment_yen(
            unit_price_yen=10000,
            quantity=1,
            rule=CommissionRule("percent", 50),
            hpb_discount_yen=1500,
            p_points_yen=200,
            r_nomination_fee_yen=1000,
        )

        self.assertEqual(gross, 10000)
        self.assertEqual(discount, 1700)
        self.assertEqual(net, 8300)
        self.assertEqual(sales, 9300)
        self.assertEqual(payout, 5150)

    def test_hpb_discount_is_total_amount(self):
        _, discount, net, sales, payout = calc_treatment_yen(
            unit_price_yen=5000,
            quantity=2,
            rule=CommissionRule("percent", 50),
            hpb_discount_yen=1000,
            p_points_yen=0,
            r_nomination_fee_yen=0,
        )

        self.assertEqual(discount, 1000)
        self.assertEqual(net, 9000)
        self.assertEqual(sales, 9000)
        self.assertEqual(payout, 4500)


if __name__ == "__main__":
    unittest.main()
