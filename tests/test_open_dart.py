from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import sys
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from earnings.open_dart import (  # noqa: E402
    OpenDartApiError,
    OpenDartClient,
    chunk_company_codes,
)
from earnings.open_dart_parser import (  # noqa: E402
    parse_account_rows,
    select_preferred_accounts,
    standalone_quarter_value,
)
from earnings.corp_codes import listed_corporations, parse_corp_code_archive  # noqa: E402


class FakeResponse:
    def __init__(self, payload=None, status_code=200, content=b""):
        self.payload = payload
        self.status_code = status_code
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.responses.pop(0)


def account_row(**overrides):
    row = {
        "rcept_no": "20260828000001",
        "reprt_code": "11014",
        "bsns_year": "2026",
        "corp_code": "00126380",
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": "dart_OperatingIncomeLoss",
        "account_nm": "영업이익",
        "thstrm_dt": "2026.07.01 ~ 2026.09.30",
        "thstrm_amount": "1,250",
        "thstrm_add_amount": "3,400",
        "currency": "KRW",
    }
    row.update(overrides)
    return row


class OpenDartClientTests(unittest.TestCase):
    def test_batches_deduplicate_and_never_exceed_one_hundred(self):
        codes = [f"{number:08d}" for number in range(1, 102)] + ["00000001"]
        batches = chunk_company_codes(codes)
        self.assertEqual([len(batch) for batch in batches], [100, 1])
        self.assertEqual(sum(len(batch) for batch in batches), 101)

    def test_multi_company_is_primary_request_and_secret_is_not_in_metadata(self):
        session = FakeSession([FakeResponse({"status": "000", "message": "정상", "list": []})])
        client = OpenDartClient("top-secret", session=session)
        response = client.fetch_multi_accounts(["00126380", "00164779"], 2026, "11014")
        self.assertTrue(session.calls[0]["url"].endswith("/fnlttMultiAcnt.json"))
        self.assertEqual(session.calls[0]["params"]["crtfc_key"], "top-secret")
        self.assertNotIn("crtfc_key", response.request_params)
        self.assertEqual(response.request_params["corp_code"], "00126380,00164779")

    def test_corp_code_archive_download_keeps_secret_out_of_response_metadata(self):
        session = FakeSession([FakeResponse(content=b"zip-content")])
        response = OpenDartClient("top-secret", session=session).fetch_corp_code_archive()
        self.assertTrue(session.calls[0]["url"].endswith("/corpCode.xml"))
        self.assertEqual(session.calls[0]["params"]["crtfc_key"], "top-secret")
        self.assertEqual(response.request_params, {})
        self.assertEqual(response.content, b"zip-content")

    def test_no_data_is_an_empty_success_and_other_statuses_raise(self):
        no_data = FakeSession([FakeResponse({"status": "013", "message": "조회된 데이터가 없습니다."})])
        self.assertEqual(OpenDartClient("key", session=no_data).fetch_multi_accounts(
            ["00126380"], 2026, "11014"
        ).rows, [])
        invalid = FakeSession([FakeResponse({"status": "010", "message": "등록되지 않은 키"})])
        with self.assertRaises(OpenDartApiError):
            OpenDartClient("key", session=invalid).fetch_multi_accounts(["00126380"], 2026, "11014")

    def test_periodic_search_keeps_corrections_and_paginates(self):
        session = FakeSession([
            FakeResponse({"status": "000", "total_page": 2, "list": []}),
            FakeResponse({"status": "000", "total_page": 2, "list": []}),
        ])
        pages = list(OpenDartClient("key", session=session).iter_periodic_filings(
            date(2026, 8, 1), date(2026, 8, 28)
        ))
        self.assertEqual(len(pages), 2)
        self.assertEqual(session.calls[0]["params"]["last_reprt_at"], "N")
        self.assertEqual(session.calls[1]["params"]["page_no"], "2")


class OpenDartParserTests(unittest.TestCase):
    def test_parser_preserves_current_and_cumulative_values(self):
        fact = parse_account_rows({"list": [account_row()]})[0]
        self.assertEqual(fact.metric, "operating_income")
        self.assertEqual(fact.current_amount, Decimal("1250"))
        self.assertEqual(fact.cumulative_amount, Decimal("3400"))
        self.assertEqual(fact.period_start, date(2026, 7, 1))
        self.assertEqual(fact.period_end, date(2026, 9, 30))

    def test_account_names_are_fallbacks_not_the_only_matching_method(self):
        rows = [
            account_row(account_id="ifrs-full_Revenue", account_nm="영업수익(임의 표시명)"),
            account_row(account_id="custom", account_nm="당기순이익(손실)"),
        ]
        self.assertEqual([fact.metric for fact in parse_account_rows({"list": rows})], [
            "revenue", "net_income"
        ])

    def test_selection_uses_one_cfs_scope_without_mixing_ofs(self):
        rows = [
            account_row(fs_div="CFS", account_id="ifrs-full_Revenue", account_nm="매출액"),
            account_row(fs_div="OFS", account_id="dart_OperatingIncomeLoss", account_nm="영업이익"),
        ]
        selected = select_preferred_accounts(parse_account_rows({"list": rows}))["00126380"]
        self.assertEqual(set(selected), {"revenue"})
        self.assertEqual(selected["revenue"].consolidation_scope, "CFS")

    def test_interim_prefers_documented_three_month_value(self):
        fact = parse_account_rows({"list": [account_row()]})[0]
        self.assertEqual(standalone_quarter_value(fact, previous_cumulative=Decimal("2150")), Decimal("1250"))

    def test_q4_subtracts_nine_months_for_operating_income_and_basic_eps(self):
        operating = parse_account_rows({"list": [account_row(
            reprt_code="11011", thstrm_amount="5,000", thstrm_add_amount=""
        )]})[0]
        self.assertEqual(
            standalone_quarter_value(operating, previous_cumulative=Decimal("3400")),
            Decimal("1600"),
        )
        eps = parse_account_rows({"list": [account_row(
            reprt_code="11011",
            account_id="ifrs-full_BasicEarningsLossPerShare",
            account_nm="기본주당이익",
            thstrm_amount="500",
        )]})[0]
        self.assertEqual(
            standalone_quarter_value(eps, previous_cumulative=Decimal("320")),
            Decimal("180"),
        )

    def test_corp_code_archive_maps_only_listed_six_digit_codes(self):
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <result>
          <list><corp_code>00126380</corp_code><corp_name>삼성전자</corp_name><stock_code>005930</stock_code><modify_date>20260828</modify_date></list>
          <list><corp_code>00000001</corp_code><corp_name>비상장회사</corp_name><stock_code> </stock_code><modify_date>20260827</modify_date></list>
        </result>""".encode("utf-8")
        archive_buffer = BytesIO()
        with ZipFile(archive_buffer, "w") as archive:
            archive.writestr("CORPCODE.xml", xml)
        companies = parse_corp_code_archive(archive_buffer.getvalue())
        listed = listed_corporations(companies)
        self.assertEqual(len(companies), 2)
        self.assertEqual(list(listed), ["005930"])
        self.assertEqual(listed["005930"].corp_code, "00126380")


if __name__ == "__main__":
    unittest.main()
