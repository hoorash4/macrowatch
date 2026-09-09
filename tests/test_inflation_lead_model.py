import unittest

from backend.inflation_lead_model import (
    ProducerCalibration,
    blend_leading_forecasts,
    calibrate_producer_inflation,
    fisher_real_rate_pct,
    fit_ridge,
    integrated_inflation_yoy,
    month_to_date_average_return_pct,
    predict_ridge,
    select_ridge_alpha,
    select_nowcast_weight,
)


class InflationLeadModelTests(unittest.TestCase):
    def test_nowcast_weight_blends_two_total_forecasts(self):
        self.assertAlmostEqual(blend_leading_forecasts(0.1, 0.5, 0.8), 0.42)
        with self.assertRaises(ValueError):
            blend_leading_forecasts(0.1, 0.5, 1.1)

    def test_correction_ratio_is_selected_from_prior_direction_accuracy(self):
        actual = [0.4 if index % 2 else -0.4 for index in range(24)]
        commodity = [-value for value in actual]
        broad = [2.0 * value for value in actual]
        self.assertEqual(
            select_nowcast_weight(actual, commodity, broad),
            0.65,
        )

    def test_ridge_recovers_linear_signal(self):
        features = [[value, value % 3] for value in range(1, 90)]
        targets = [0.25 + 0.4 * row[0] - 0.15 * row[1] for row in features]
        alpha = select_ridge_alpha(features, targets)
        model = fit_ridge(features, targets, alpha=alpha)
        self.assertAlmostEqual(predict_ridge(model, [90, 0]), 36.25, delta=0.2)

    def test_integrated_measure_includes_all_three_price_families(self):
        point = integrated_inflation_yoy(
            cpi_yoy_pct=3.0,
            pce_yoy_pct=2.0,
            ppi_yoy_pct=4.0,
            producer_calibration=ProducerCalibration(2.5, 3.0, 0.5),
            status="final",
        )
        self.assertEqual(point.status, "final")
        self.assertAlmostEqual(point.aligned_ppi_yoy_pct, 3.0)
        self.assertAlmostEqual(point.yoy_pct, 2.5)

    def test_producer_calibration_matches_consumer_volatility(self):
        consumer = [float(value) for value in range(24)]
        producer = [2.0 * value + 7.0 for value in consumer]
        calibration = calibrate_producer_inflation(
            consumer_yoy_history=consumer,
            producer_yoy_history=producer,
        )
        self.assertAlmostEqual(calibration.producer_to_consumer_scale, 0.5)

    def test_exact_fisher_rate(self):
        self.assertAlmostEqual(fisher_real_rate_pct(5.0, 2.0), 2.941176, places=5)

    def test_month_to_date_average_return(self):
        self.assertAlmostEqual(month_to_date_average_return_pct([102.0, 104.0], 100.0), 3.0)


if __name__ == "__main__":
    unittest.main()
