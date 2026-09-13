import unittest
from unittest.mock import patch

from backend.signals import small_business_risk_recalculate


class FakeDatabase:
    def __init__(self):
        self.upserts = []

    def request(self, method, table, **kwargs):
        if method == "GET" and table == "us_small_business_risk_monthly":
            return [
                {"month": "2026-05-01", "sales_expectation_net": -10, "borrowing_difficulty_pct": 8, "optimism_index": 90},
                {"month": "2026-06-01", "sales_expectation_net": -12, "borrowing_difficulty_pct": 9, "optimism_index": 89},
            ]
        return []

    def upsert(self, table, rows, *, conflict):
        self.upserts.append((table, rows, conflict))


class SmallBusinessRiskRecalculationTests(unittest.TestCase):
    @patch("backend.signals.small_business_risk_recalculate.fetch_stored_delinquency")
    def test_recalculates_every_stored_month_before_one_upsert(self, delinquency):
        delinquency.return_value = {"2026-05-01": 2.4, "2026-06-01": 2.5}
        database = FakeDatabase()
        result = small_business_risk_recalculate.recalculate(database)
        self.assertEqual(result["rows"], 2)
        self.assertEqual(database.upserts[0][0], "us_small_business_risk_monthly")
        self.assertEqual(database.upserts[0][2], "month")
        self.assertEqual(database.upserts[0][1][0]["small_business_delinquency_pct"], 2.4)

    @patch("backend.signals.small_business_risk_recalculate.fetch_stored_delinquency", return_value={})
    def test_missing_component_aborts_before_any_write(self, _delinquency):
        database = FakeDatabase()
        with self.assertRaisesRegex(RuntimeError, "저장을 중단"):
            small_business_risk_recalculate.recalculate(database)
        self.assertEqual(database.upserts, [])


if __name__ == "__main__":
    unittest.main()
