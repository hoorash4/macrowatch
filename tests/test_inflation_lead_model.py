import unittest

from backend.inflation_lead_model import (
    choose_integrated_inflation,
    fisher_real_rate_pct,
    fit_ridge,
    month_to_date_average_return_pct,
    predict_ridge,
    select_ridge_alpha,
)


class InflationLeadModelTests(unittest.TestCase):
    def test_ridge_recovers_linear_signal(self):
        features = [[value, value % 3] for value in range(1, 90)]
        targets = [0.25 + 0.4 * row[0] - 0.15 * row[1] for row in features]
        alpha = select_ridge_alpha(features, targets)
        model = fit_ridge(features, targets, alpha=alpha)
        self.assertAlmostEqual(predict_ridge(model, [90, 0]), 36.25, delta=0.2)

    def test_published_pce_is_final_anchor(self):
        point = choose_integrated_inflation(
            published_pce_yoy_pct=2.1,
            published_core_pce_yoy_pct=2.4,
            bridged_pce_yoy_pct=2.2,
        )
        self.assertEqual(point.status, "final")
        self.assertEqual(point.source, "PCE")
        self.assertEqual(point.headline_yoy_pct, 2.1)

    def test_bridge_is_used_only_while_pce_is_pending(self):
        point = choose_integrated_inflation(
            published_pce_yoy_pct=None,
            published_core_pce_yoy_pct=None,
            bridged_pce_yoy_pct=2.2,
            bridged_core_pce_yoy_pct=2.5,
        )
        self.assertEqual(point.status, "provisional")
        self.assertEqual(point.headline_yoy_pct, 2.2)

    def test_exact_fisher_rate(self):
        self.assertAlmostEqual(fisher_real_rate_pct(5.0, 2.0), 2.941176, places=5)

    def test_month_to_date_average_return(self):
        self.assertAlmostEqual(month_to_date_average_return_pct([102.0, 104.0], 100.0), 3.0)


if __name__ == "__main__":
    unittest.main()
