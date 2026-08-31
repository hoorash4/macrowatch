from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.sec_edgar import SecCompanyFactsMirrorClient  # noqa: E402
from earnings.sec_parser import canonical_sec_quarters  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class MirrorSession:
    def __init__(self):
        self.queries = []

    def get(self, _url, *, params, **_kwargs):
        query = params["q"]
        self.queries.append(query)
        if "FROM xbrl_tags" in query:
            rows = [{"tag_id": index + 1, "tag": tag} for index, tag in enumerate((
                "RevenuesNetOfInterestExpense",
                "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "SalesRevenueGoodsNet", "OperatingIncomeLoss",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
                "NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
            ))]
        elif "FROM facts_enc" in query:
            rows = [{
                "id": "1", "tag_id": "1", "unit": "USD", "start": "2025-01-01",
                "end": "2025-03-31", "val": "100", "accn_id": "7", "fy": "2025",
                "fp": "Q1", "form": "10-Q", "filed": "2025-04-20", "frame": None,
            }]
        else:
            rows = [{"accn_id": "7", "accn": "0001-25-000007"}]
        return FakeResponse({"query_execution_status": "Success", "rows": rows})


def company_facts(*, include_operating_income: bool = True):
    aliases = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [100, 120, 140, 500],
        "NetIncomeLoss": [10, 12, 14, 50],
    }
    if include_operating_income:
        aliases["OperatingIncomeLoss"] = [20, 24, 28, 100]
    periods = [
        ("Q1", "2025-01-01", "2025-03-31", "10-Q", "0001-25-000001"),
        ("Q2", "2025-04-01", "2025-06-30", "10-Q", "0001-25-000002"),
        ("Q3", "2025-07-01", "2025-09-30", "10-Q/A", "0001-25-000003"),
        ("FY", "2025-01-01", "2025-12-31", "10-K", "0001-26-000004"),
    ]
    facts = {}
    for alias, values in aliases.items():
        facts[alias] = {"units": {"USD": [
            {
                "fy": 2025, "fp": fp, "start": start, "end": end,
                "form": form, "accn": accession, "filed": "2026-02-15" if fp == "FY" else end,
                "val": value,
            }
            for value, (fp, start, end, form, accession) in zip(values, periods)
        ]}}
    return {"facts": {"us-gaap": facts}}


class SecEarningsTests(unittest.TestCase):
    def test_mirror_rebuilds_the_official_company_facts_envelope(self):
        session = MirrorSession()
        client = SecCompanyFactsMirrorClient(session=session, sleeper=lambda _seconds: None)
        payload = client.fetch_company_facts("1", first_year=2025)
        fact = payload["facts"]["us-gaap"]["RevenuesNetOfInterestExpense"]["units"]["USD"][0]
        self.assertEqual(fact["accn"], "0001-25-000007")
        self.assertEqual(fact["val"], "100")
        self.assertIn("id > 0", session.queries[1])

    def test_complete_company_facts_produce_four_quarters_and_derive_q4(self):
        rows, gaps = canonical_sec_quarters(
            company_facts(), cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(gaps, [])
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[2]["filing"]["filing_kind"], "amendment")
        self.assertEqual(rows[0]["filing"]["source_filing_id"], "0000000001:0001-25-000001:2025Q1")
        self.assertEqual(rows[3]["quarter"]["revenue"], "140")
        self.assertEqual(rows[3]["quarter"]["operating_income"], "28")
        self.assertEqual(rows[3]["quarter"]["net_income"], "14")
        self.assertNotIn("eps", rows[0]["quarter"])

    def test_q4_subtraction_refuses_mixed_revenue_concepts(self):
        payload = company_facts()
        revenue_rows = payload["facts"]["us-gaap"].pop(
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        )["units"]["USD"]
        payload["facts"]["us-gaap"]["Revenues"] = {
            "units": {"USD": revenue_rows[:3]}
        }
        payload["facts"]["us-gaap"]["SalesRevenueNet"] = {
            "units": {"USD": [revenue_rows[3]]}
        }
        rows, gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertNotIn(4, [row["filing"]["fiscal_quarter"] for row in rows])
        self.assertIn((2025, 4), gaps)

    def test_zero_revenue_loss_is_kept_but_all_zero_or_negative_revenue_is_a_gap(self):
        payload = company_facts()
        facts = payload["facts"]["us-gaap"]
        revenue = facts["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"]
        operating = facts["OperatingIncomeLoss"]["units"]["USD"]
        net = facts["NetIncomeLoss"]["units"]["USD"]
        revenue[0]["val"] = 0
        operating[0]["val"] = -20
        net[0]["val"] = -10
        revenue[1]["val"] = -1
        revenue[2]["val"] = operating[2]["val"] = net[2]["val"] = 0
        rows, gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2025, years=1,
        )
        kept = {row["filing"]["fiscal_quarter"] for row in rows}
        self.assertIn(1, kept)
        self.assertNotIn(2, kept)
        self.assertNotIn(3, kept)
        self.assertIn((2025, 2), gaps)
        self.assertIn((2025, 3), gaps)

    def test_financial_company_net_interest_revenue_is_a_canonical_top_line(self):
        payload = company_facts()
        payload["facts"]["us-gaap"].pop(
            "RevenueFromContractWithCustomerExcludingAssessedTax"
        )
        payload["facts"]["us-gaap"]["RevenuesNetOfInterestExpense"] = {
            "units": {"USD": [
                {
                    "fy": 2025, "fp": fp, "start": start, "end": end,
                    "form": form, "accn": accession,
                    "filed": "2026-02-15" if fp == "FY" else end,
                    "val": value,
                }
                for value, (fp, start, end, form, accession) in zip(
                    [300, 330, 360, 1350],
                    [
                        ("Q1", "2025-01-01", "2025-03-31", "10-Q", "0001-25-000001"),
                        ("Q2", "2025-04-01", "2025-06-30", "10-Q", "0001-25-000002"),
                        ("Q3", "2025-07-01", "2025-09-30", "10-Q", "0001-25-000003"),
                        ("FY", "2025-01-01", "2025-12-31", "10-K", "0001-26-000004"),
                    ],
                )
            ]}
        }
        rows, gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(gaps, [])
        self.assertEqual([row["quarter"]["revenue"] for row in rows], [
            "300", "330", "360", "360",
        ])

    def test_missing_core_metric_is_a_gap_not_a_null_canonical_row(self):
        rows, gaps = canonical_sec_quarters(
            company_facts(include_operating_income=False),
            cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(rows, [])
        self.assertEqual(gaps, [(2025, 1), (2025, 2), (2025, 3), (2025, 4)])

    def test_invalid_period_is_ignored_before_database_constraints(self):
        payload = company_facts()
        for metric in payload["facts"]["us-gaap"].values():
            metric["units"]["USD"][0]["start"] = "2025-04-01"
        rows, gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertNotIn(1, [row["filing"]["fiscal_quarter"] for row in rows])
        self.assertIn((2025, 1), gaps)

    def test_comparative_fiscal_label_cannot_duplicate_one_period_end(self):
        payload = company_facts()
        for metric in payload["facts"]["us-gaap"].values():
            comparative = dict(metric["units"]["USD"][0])
            comparative.update({"fy": 2026, "filed": "2026-04-20"})
            metric["units"]["USD"].append(comparative)
        rows, gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2026, years=2,
        )
        period_ends = [row["filing"]["period_end"] for row in rows]
        self.assertEqual(len(period_ends), len(set(period_ends)))
        self.assertIn((2026, 1), gaps)


    def test_current_calendar_period_accepts_next_fiscal_year_label(self):
        payload = company_facts()
        for metric in payload["facts"]["us-gaap"].values():
            for fact in metric["units"]["USD"]:
                if fact["fp"] == "Q1":
                    fact["fy"] = 2026
                    fact["start"] = "2025-11-01"
                    fact["end"] = "2026-01-31"
                    fact["filed"] = "2026-03-15"
                else:
                    fact["fy"] = 1900
        rows, _gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filing"]["fiscal_year"], 2026)
        self.assertEqual(rows[0]["filing"]["market_year"], 2026)
        self.assertEqual(rows[0]["filing"]["market_quarter"], 1)


    def test_company_extension_namespace_can_supply_canonical_metrics(self):
        payload = company_facts()
        payload["facts"]["issuer"] = payload["facts"].pop("us-gaap")
        rows, gaps = canonical_sec_quarters(
            payload, cik="0000000001", as_of_year=2025, years=1,
        )
        self.assertEqual(gaps, [])
        self.assertEqual(len(rows), 4)


if __name__ == "__main__":
    unittest.main()
