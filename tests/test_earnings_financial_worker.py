from datetime import date
from decimal import Decimal
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.collect_financials import (  # noqa: E402
    OpenDartFinancialWorker,
    build_canonical_quarter,
    facts_for_storage,
)
from earnings.open_dart import OpenDartResponse  # noqa: E402
from earnings.open_dart_parser import parse_account_rows, select_preferred_accounts  # noqa: E402


def rows_for_period(report_code="11013", year="2026", current="100", cumulative="100"):
    accounts = (
        ("ifrs-full_Revenue", "매출액"),
        ("dart_OperatingIncomeLoss", "영업이익"),
        ("ifrs-full_ProfitLoss", "당기순이익"),
        ("ifrs-full_BasicEarningsLossPerShare", "기본주당이익"),
    )
    return [{
        "rcept_no": "20260515000001",
        "reprt_code": report_code,
        "bsns_year": year,
        "corp_code": "00126380",
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": account_id,
        "account_nm": account_name,
        "thstrm_dt": f"{year}.01.01 ~ {year}.03.31",
        "thstrm_amount": current,
        "thstrm_add_amount": cumulative,
        "currency": "KRW",
    } for account_id, account_name in accounts]


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.multi_calls = []
        self.full_calls = []

    def fetch_multi_accounts(self, corp_codes, business_year, report_code):
        self.multi_calls.append((list(corp_codes), business_year, report_code))
        return OpenDartResponse("fnlttMultiAcnt.json", {
            "corp_code": ",".join(corp_codes),
            "bsns_year": str(business_year),
            "reprt_code": report_code,
        }, self.payload)

    def fetch_single_all_accounts(self, corp_code, business_year, report_code, scope):
        self.full_calls.append((corp_code, business_year, report_code, scope))
        return OpenDartResponse("fnlttSinglAcntAll.json", {}, {"status": "013", "list": []})


class FakeStore:
    def __init__(self):
        self.completions = []
        self.failures = []
        self.payload_count = 0

    def save_source_payload(self, **kwargs):
        self.payload_count += 1
        return f"00000000-0000-0000-0000-{self.payload_count:012d}"

    def complete_open_dart_job(self, **kwargs):
        self.completions.append(kwargs)
        return {"outcome": kwargs["outcome"]}

    def fail_open_dart_job(self, **kwargs):
        self.failures.append(kwargs)
        return "retry"


class EarningsFinancialWorkerTests(unittest.TestCase):
    def test_canonical_q4_subtracts_previous_nine_month_cumulative(self):
        current = parse_account_rows({"list": rows_for_period(
            report_code="11011", current="500", cumulative="500"
        )})
        previous_rows = rows_for_period(report_code="11014", current="120", cumulative="320")
        for row in previous_rows:
            row["thstrm_dt"] = "2026.01.01 ~ 2026.09.30"
        previous = parse_account_rows({"list": previous_rows})
        quarter, missing = build_canonical_quarter(
            select_preferred_accounts(current)["00126380"],
            select_preferred_accounts(previous)["00126380"],
            filed_on="2027-03-20",
        )
        self.assertEqual(missing, [])
        self.assertEqual(quarter["revenue"], "180")
        self.assertEqual(quarter["period_start"], "2026-10-01")

    def test_source_current_and_cumulative_values_are_both_preserved(self):
        facts = parse_account_rows({"list": rows_for_period()[:1]})
        stored = facts_for_storage(facts, {facts[0].source_row_key: "payload-id"})
        self.assertEqual({row["source_field"] for row in stored}, {
            "thstrm_amount", "thstrm_add_amount"
        })
        self.assertEqual({row["source_payload_id"] for row in stored}, {"payload-id"})

    def test_q1_complete_multi_company_response_updates_canonical_without_fallback(self):
        client = FakeClient({"status": "000", "list": rows_for_period()})
        store = FakeStore()
        worker = OpenDartFinancialWorker(client, store, request_interval_seconds=0)
        result = worker.process_batch([{
            "id": 1,
            "company_id": "company-id",
            "business_year": 2026,
            "report_code": "11013",
            "corp_code": "00126380",
            "metadata": {"filed_on": "2026-05-15"},
        }])
        self.assertEqual(result["completed"], 1)
        self.assertEqual(client.full_calls, [])
        self.assertEqual(store.failures, [])
        self.assertEqual(store.completions[0]["quarter"]["eps"], "100")
        self.assertEqual(store.completions[0]["outcome"], "complete")


if __name__ == "__main__":
    unittest.main()
