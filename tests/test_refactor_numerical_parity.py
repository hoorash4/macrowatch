"""Output snapshots from pre-refactor ecc3888, using synthetic data only.

The full-row digests protect dates, missing-data boundaries and component values,
not just the last displayed score. Intentional model changes must refresh these
snapshots; a structural refactor must not.
"""
import hashlib
import json
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from signals.equity_bond_attractiveness import QuarterlyInput, build_weekly_rows
from signals.equity_bond_model import fit_logistic_l2


class RefactorNumericalParityTests(unittest.TestCase):
    def test_full_korea_and_us_weekly_outputs_match_pre_refactor(self):
        weeks = [date(2015, 1, 2) + timedelta(weeks=i) for i in range(600)]
        prices = {day: 100 + i * .25 + (i % 17) for i, day in enumerate(weeks)}
        yields = {day: 2 + (i % 31) * .025 for i, day in enumerate(weeks)}
        quarters = [
            QuarterlyInput(date(2015 + i // 4, (i % 4 + 1) * 3, 28),
                           100 + i * 2, 10000 + i * 100)
            for i in range(50)
        ]
        expected = {
            "KR": (421, "ca9ed57147dab91384be8429f79ecc5a71e2ad6c13b864bf9d87c5e7b1e6ad2f"),
            "US": (424, "ea8ddad4e24f89b2cbf8871e96a2d3394138d2b9ba0133c24c6d51fc045c6f3b"),
        }
        for country, (count, digest) in expected.items():
            with self.subTest(country=country):
                rows = build_weekly_rows(country, weeks, prices, yields, quarters,
                                         us_anchor_earnings_yield=4.5)
                self.assertEqual(len(rows), count)
                encoded = json.dumps(rows, sort_keys=True, default=str).encode()
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), digest)

    def test_logistic_fit_preserves_pre_refactor_coefficients(self):
        features = [[((i * (j + 3)) % 19 - 9) / 10 for j in range(5)] for i in range(100)]
        labels = [int(i % 7 < 4) for i in range(100)]
        intercept, weights = fit_logistic_l2(features, labels)
        expected = [0.32439134912781026, -0.037908039149309784, 0.13828201931997353,
                    0.050368707224347394, -0.009415395404267871, 0.021965967092272345]
        for actual, baseline in zip([intercept, *weights], expected, strict=True):
            self.assertAlmostEqual(actual, baseline, places=12)


if __name__ == "__main__":
    unittest.main()
