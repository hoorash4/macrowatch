from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.collect_financials import (  # noqa: E402
    OpenDartFinancialWorker,
    attach_reporting_period,
    build_canonical_quarter,
    reporting_period_bounds,
)
from earnings.open_dart import (  # noqa: E402
    OpenDartApiError,
    OpenDartBinaryResponse,
    OpenDartResponse,
)
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


def filing_archive(statement_page):
    output = BytesIO()
    with ZipFile(output, "w") as zipped:
        zipped.writestr("report.xml", statement_page)
    return output.getvalue()


class FakeClient:
    def __init__(self, payload, *, archive=None):
        self.payload = payload
        self.archive = archive
        self.api_key = "fake-dart-key"
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

    def fetch_financial_xbrl_archive(self, receipt_number, report_code):
        raise OpenDartApiError("014", "No test XBRL archive")

    def fetch_filing_archive(self, receipt_number):
        if self.archive is None:
            raise AssertionError("Unexpected OpenDART filing-archive request")
        return OpenDartBinaryResponse(
            "document.xml", {"rcept_no": receipt_number}, self.archive
        )


class FakeStore:
    def __init__(self):
        self.service_role_key = "store-secret"
        self.completions = []
        self.failures = []

    def complete_open_dart_job(self, **kwargs):
        self.completions.append(kwargs)
        return {"outcome": kwargs["outcome"]}

    def fail_open_dart_job(self, **kwargs):
        self.failures.append(kwargs)
        return "retry"


class EarningsFinancialWorkerTests(unittest.TestCase):
    def test_diagnostic_errors_force_redact_both_credentials(self):
        client = FakeClient({"status": "000", "list": []})
        client.api_key = "dart-secret"
        store = FakeStore()
        worker = OpenDartFinancialWorker(client, store, request_interval_seconds=0)
        worker._record_error(RuntimeError("dart-secret and store-secret must stay hidden"))
        diagnostic = next(iter(worker.error_counts))
        self.assertNotIn("dart-secret", diagnostic)
        self.assertNotIn("store-secret", diagnostic)
        self.assertIn("[redacted]", diagnostic)

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

    def test_reporting_period_uses_official_report_name_when_multi_rows_omit_dates(self):
        start, end = reporting_period_bounds(2026, "11012", "반기보고서 (2025.06)")
        self.assertEqual(start, date(2025, 4, 1))
        self.assertEqual(end, date(2025, 6, 30))

    def test_multi_company_rows_without_dates_complete_from_filing_metadata(self):
        rows = rows_for_period()
        for row in rows:
            row.pop("thstrm_dt")
        client = FakeClient({"status": "000", "list": rows})
        store = FakeStore()
        worker = OpenDartFinancialWorker(client, store, request_interval_seconds=0)
        result = worker.process_batch([{
            "id": 1,
            "company_id": "company-id",
            "business_year": 2026,
            "report_code": "11013",
            "corp_code": "00126380",
            "metadata": {
                "receipt_no": "20260515000001",
                "filed_on": "2026-05-15",
                "report_name": "분기보고서 (2026.03)",
            },
        }])
        self.assertEqual(result["completed"], 1)
        self.assertEqual(store.failures, [])
        self.assertEqual(store.completions[0]["filing"]["period_end"], "2026-03-31")
        self.assertEqual(store.completions[0]["quarter"]["period_start"], "2026-01-01")
        self.assertNotIn("facts", store.completions[0])

    def test_q1_complete_core_metrics_update_canonical_without_fallback(self):
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
        self.assertNotIn("eps", store.completions[0]["quarter"])
        self.assertEqual(store.completions[0]["outcome"], "complete")

    def test_eps_row_is_ignored_entirely(self):
        rows = rows_for_period()
        self.assertEqual(
            {fact.metric for fact in parse_account_rows({"list": rows})},
            {"revenue", "operating_income", "net_income"},
        )
        client = FakeClient({"status": "000", "list": rows})
        store = FakeStore()
        worker = OpenDartFinancialWorker(client, store, request_interval_seconds=0)
        result = worker.process_batch([{
            "id": 1,
            "company_id": "company-id",
            "business_year": 2026,
            "report_code": "11013",
            "corp_code": "00126380",
            "metadata": {
                "receipt_no": "20260515000001",
                "filed_on": "2026-05-15",
                "report_name": "분기보고서 (2026.03)",
            },
        }])
        self.assertEqual(result["completed"], 1)
        self.assertEqual(client.full_calls, [])
        self.assertNotIn("eps", store.completions[0]["quarter"])

    def test_missing_financial_company_revenue_keeps_other_core_metrics(self):
        rows = [row for row in rows_for_period() if row["account_nm"] != "매출액"]
        selected = select_preferred_accounts(parse_account_rows({"list": rows}))["00126380"]
        quarter, missing = build_canonical_quarter(
            selected, previous_selected=None, filed_on="2026-05-15",
        )
        self.assertEqual(missing, ["revenue"])
        self.assertIsNone(quarter["revenue"])
        self.assertEqual(quarter["operating_income"], "100")
        self.assertEqual(quarter["net_income"], "100")

    def test_missing_financial_revenue_is_derived_only_after_statement_reconciliation(self):
        rows = [
            row for row in rows_for_period(current="100", cumulative="100")
            if row["account_nm"] != "매출액"
        ]
        statement_page = """
        <html><body>(단위 : 원)<table>
          <tr><td>영업수익</td><td>300</td></tr>
          <tr><td>영업비용</td><td>200</td></tr>
          <tr><td>영업이익</td><td>100</td></tr>
        </table></body></html>
        """
        client = FakeClient(
            {"status": "000", "list": rows},
            archive=filing_archive(statement_page),
        )
        store = FakeStore()
        worker = OpenDartFinancialWorker(client, store, request_interval_seconds=0)
        result = worker.process_batch([{
            "id": 1,
            "company_id": "company-id",
            "business_year": 2026,
            "report_code": "11013",
            "corp_code": "00126380",
            "metadata": {
                "receipt_no": "20260515000001",
                "filed_on": "2026-05-15",
                "report_name": "분기보고서 (2026.03)",
            },
        }])
        self.assertEqual(result["completed"], 1)
        self.assertEqual(store.completions[0]["quarter"]["revenue"], "300")
        self.assertEqual(store.completions[0]["quarter"]["missing_metrics"], [])

    def test_unreconciled_financial_revenue_remains_a_partial_review_row(self):
        rows = [row for row in rows_for_period() if row["account_nm"] != "매출액"]
        client = FakeClient(
            {"status": "000", "list": rows},
            archive=filing_archive("""
              <html><body>(단위 : 원)<table>
                <tr><td>영업수익</td><td>300</td></tr>
                <tr><td>영업비용</td><td>100</td></tr>
                <tr><td>영업이익</td><td>100</td></tr>
              </table></body></html>
            """),
        )
        store = FakeStore()
        worker = OpenDartFinancialWorker(client, store, request_interval_seconds=0)
        result = worker.process_batch([{
            "id": 1, "company_id": "company-id", "business_year": 2026,
            "report_code": "11013", "corp_code": "00126380",
            "metadata": {"receipt_no": "20260515000001", "filed_on": "2026-05-15"},
        }])
        self.assertEqual(result["review_required"], 1)
        quarter = store.completions[0]["quarter"]
        self.assertIsNone(quarter["revenue"])
        self.assertEqual(quarter["missing_metrics"], ["revenue"])


if __name__ == "__main__":
    unittest.main()
