from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from earnings_us.models import market_period
from earnings_us.pipeline import in_snapshot_window
from earnings_us.transform import extract_new_sec_facts


def entry(*, fy: int, fp: str, accn: str, start: str, end: str, filed: str, value: str):
    return {"fy": fy, "fp": fp, "accn": accn, "form": "10-K" if fp == "FY" else "10-Q", "start": start, "end": end, "filed": filed, "val": value}


def payload():
    facts = {}
    for tag, values in {
        "Revenues": [
            entry(fy=2026, fp="Q1", accn="q1", start="2025-02-01", end="2025-04-30", filed="2025-05-20", value="100"),
            entry(fy=2026, fp="Q2", accn="q2", start="2025-05-01", end="2025-07-31", filed="2025-08-20", value="200"),
            entry(fy=2026, fp="Q3", accn="q3", start="2025-08-01", end="2025-10-31", filed="2025-11-20", value="300"),
            entry(fy=2026, fp="FY", accn="fy", start="2025-02-01", end="2026-01-31", filed="2026-03-20", value="1000"),
        ],
        "OperatingIncomeLoss": [
            entry(fy=2026, fp="Q1", accn="q1", start="2025-02-01", end="2025-04-30", filed="2025-05-20", value="10"),
            entry(fy=2026, fp="Q2", accn="q2", start="2025-05-01", end="2025-07-31", filed="2025-08-20", value="20"),
            entry(fy=2026, fp="Q3", accn="q3", start="2025-08-01", end="2025-10-31", filed="2025-11-20", value="30"),
            entry(fy=2026, fp="FY", accn="fy", start="2025-02-01", end="2026-01-31", filed="2026-03-20", value="100"),
        ],
        "NetIncomeLoss": [
            entry(fy=2026, fp="Q1", accn="q1", start="2025-02-01", end="2025-04-30", filed="2025-05-20", value="8"),
            entry(fy=2026, fp="Q2", accn="q2", start="2025-05-01", end="2025-07-31", filed="2025-08-20", value="16"),
            entry(fy=2026, fp="Q3", accn="q3", start="2025-08-01", end="2025-10-31", filed="2025-11-20", value="24"),
            entry(fy=2026, fp="FY", accn="fy", start="2025-02-01", end="2026-01-31", filed="2026-03-20", value="80"),
        ],
    }.items():
        facts[tag] = {"units": {"USD": values}}
    return {"facts": {"us-gaap": facts}}


class USEarningsTransformTests(unittest.TestCase):
    def test_snapshot_is_not_allowed_to_create_a_late_prior_quarter_universe(self):
        self.assertTrue(in_snapshot_window(date(2026, 10, 1)))
        self.assertFalse(in_snapshot_window(date(2026, 9, 5)))
    def test_q1_to_q3_use_reported_standalone_values(self):
        facts = extract_new_sec_facts("us:cik:1", payload(), {"q2"})
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].fiscal_quarter, 2)
        self.assertEqual(facts[0].top_line, Decimal("200"))
        self.assertEqual(facts[0].operating_income, Decimal("20"))

    def test_fy_calculates_q4_only_from_same_fiscal_year_quarters(self):
        facts = extract_new_sec_facts("us:cik:1", payload(), {"fy"})
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        self.assertEqual(fact.fiscal_quarter, 4)
        self.assertEqual((fact.top_line, fact.operating_income, fact.net_income), (Decimal("400"), Decimal("40"), Decimal("32")))
        self.assertFalse(fact.is_pending)

    def test_fiscal_year_end_maps_to_actual_calendar_chart_quarter(self):
        fact = extract_new_sec_facts("us:cik:1", payload(), {"fy"})[0]
        self.assertEqual(fact.period_end, date(2026, 1, 31))
        self.assertEqual(market_period(fact.period_end), (2026, 1))
        row = fact.db_row()
        self.assertEqual((row["market_year"], row["market_quarter"]), (2026, 1))

    def test_q4_stays_pending_when_any_prior_quarter_is_missing(self):
        source = payload()
        source["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
            item for item in source["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] if item["fp"] != "Q3"
        ]
        fact = extract_new_sec_facts("us:cik:1", source, {"fy"})[0]
        self.assertIsNone(fact.net_income)
        self.assertTrue(fact.is_pending)


if __name__ == "__main__":
    unittest.main()
