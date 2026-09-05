from __future__ import annotations

import unittest
import requests
from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from earnings_v2.aggregation import aggregate_market
from earnings_v2.automatic import KoreaEarningsV2AutomaticPipeline
from earnings_v2.automatic_cli import DAILY_DEADLINE_SECONDS as AUTOMATIC_DEADLINE_SECONDS
from earnings_v2.models import CompanyIdentity, DelistingFiling, FinancialFact, PeriodicFiling
from corporate_events import parse_absorbed_merger, parse_absorbed_merger_archive
from earnings_v2.cli import QUARTER_DEADLINE_SECONDS, completed_successfully, parser
from earnings_v2.pipeline import TARGETS, KoreaEarningsV2Pipeline, _eligible_name, filing_period, latest_completed_quarter
from earnings_v2.http import (
    RETRYABLE_STATUS_CODES,
    RETRY_TOTAL,
    ExecutionDeadlineExceeded,
    InvalidJsonResponse,
    ResponseDeadlineExceeded,
    bounded_request,
    provider_session,
    resilient_session,
)
from earnings_v2.providers import (
    EcosFxClient,
    KisClient,
    OpenDartClient,
    ProviderError,
)
from earnings_v2.transform import (
    calculate_financial_point,
    calculate_financial_series,
    conventional_growth,
    extract_company_fact,
    profit_margin,
    update_seasonal_window,
)


def row(
    corp: str,
    name: str,
    *,
    current: str = "",
    cumulative: str = "",
    scope: str = "CFS",
    account_id: str = "",
) -> dict[str, str]:
    return {
        "corp_code": corp,
        "account_nm": name,
        "account_id": account_id,
        "thstrm_amount": current,
        "thstrm_add_amount": cumulative,
        "fs_div": scope,
        "sj_div": "IS",
        "rcept_no": "20260814000001",
        "currency": "KRW",
        "ord": "1",
    }


def complete(corp: str, *, current: str, cumulative: str, scope: str = "CFS") -> list[dict[str, str]]:
    return [
        row(corp, "매출액", current=current, cumulative=cumulative, scope=scope, account_id="ifrs-full_Revenue"),
        row(corp, "영업이익", current=current, cumulative=cumulative, scope=scope, account_id="dart_OperatingIncomeLoss"),
        row(corp, "당기순이익", current=current, cumulative=cumulative, scope=scope, account_id="ifrs-full_ProfitLoss"),
    ]


def fact(year: int, quarter: int, value: str, *, company: str = "kr:1") -> FinancialFact:
    amount = Decimal(value)
    return FinancialFact(
        company_id=company,
        fiscal_year=year,
        fiscal_quarter=quarter,
        period_end=date(year, quarter * 3, 1),
        top_line=amount * 10,
        operating_income=amount,
        net_income=amount,
        currency="KRW",
        consolidation_scope="CFS",
        source_filing_id="test",
        filing_date=date(year, quarter * 3, 1),
    )


def member(company: str, rank: int, *, year: int = 2026, quarter: int = 2) -> CompanyIdentity:
    return CompanyIdentity(
        company_id=company,
        company_name=company,
        stock_code=f"{rank:06d}",
        corp_code=f"{rank:08d}",
        market_id="kr_largecap",
        rank=rank,
        market_cap=Decimal(1000 - rank),
        reference_date=date(year, quarter * 3, 30),
    )


def simulated_universe(market_id: str, count: int, *, year: int = 2026, quarter: int = 2) -> list[dict]:
    """운영 DB와 같은 고정 기업군을 외부 상태 없이 재현한다."""
    offset = 0 if market_id == "kr_largecap" else 1000
    return [{
        "company_id": f"kr:{offset + rank:08d}",
        "company_name": f"{market_id}-{rank}",
        "stock_code": f"{offset + rank:06d}",
        "corp_code": f"{offset + rank:08d}",
        "market_id": market_id,
        "market_cap_rank": rank,
        "market_cap": str(100000 - rank),
        "reference_date": date(year, quarter * 3, 30),
    } for rank in range(1, count + 1)]


class SimulatedRepository:
    """증분 수집 생명주기를 검증하는 최소 인메모리 저장소."""

    def __init__(self) -> None:
        self.universes = {
            ("kr_largecap", 2026, 2): simulated_universe("kr_largecap", 100),
            ("kr_kosdaq", 2026, 2): simulated_universe("kr_kosdaq", 100),
        }
        self.company_rows: dict[tuple[str, int, int], dict] = {}
        self.market_rows: dict[tuple[str, int, int], dict] = {}
        self.seasonal_rows: dict[tuple[str, str, str, int], dict] = {}
        self.states: dict[str, dict] = {}
        self.saved_states: list[tuple[str, str, dict, str | None]] = []
        self.company_period_calls: list[tuple[set[str], tuple[tuple[int, int], ...]]] = []
        self.company_profiles: dict[str, dict] = {}
        self.delisting_rows: dict[str, dict] = {}
        self.stale_pending_rows: list[dict] = []
        self.fx_rates: dict[tuple[int, int, str, str], dict] = {
            (2026, 2, "USD", "KRW"): {
                "fiscal_year": 2026, "fiscal_quarter": 2,
                "base_currency": "USD", "quote_currency": "KRW",
                "target_date": date(2026, 6, 30), "observed_on": date(2026, 6, 30),
                "rate": Decimal("1300"), "source": "ecos",
            },
        }

    def seed_company(self, company_id: str, *, top_line: Decimal | None = Decimal("100"),
                     operating_income: Decimal = Decimal("10"), net_income: Decimal = Decimal("8"),
                     pending: bool = False, source: str = "open_dart") -> None:
        stored = FinancialFact(
            company_id, 2026, 2, date(2026, 6, 30), top_line, operating_income, net_income,
            "KRW", "CFS", f"seed:{company_id}", date(2026, 8, 14), is_pending=pending,
        ).db_row(calculation_version=6)
        stored["source"] = source
        self.company_rows[(company_id, 2026, 2)] = stored

    def universe(self, market_id, year, quarter):
        return [
            {**row, **self.company_profiles.get(row["company_id"], {})}
            for row in self.universes.get((market_id, year, quarter), [])
        ]

    def quarter_fx_rate(self, year, quarter, base_currency, quote_currency):
        row = self.fx_rates.get((year, quarter, base_currency, quote_currency))
        return dict(row) if row is not None else None

    def upsert_quarter_fx_rate(self, row):
        key = (row["fiscal_year"], row["fiscal_quarter"], row["base_currency"], row["quote_currency"])
        self.fx_rates[key] = dict(row)
        return 1

    def upsert_company_profiles(self, rows):
        materialized = list(rows)
        for row in materialized:
            self.company_profiles[row["company_id"]] = dict(row)
        return len(materialized)

    def upsert_delisting_events(self, rows):
        materialized = list(rows)
        for row in materialized:
            self.delisting_rows[row["receipt_no"]] = dict(row)
        return len(materialized)

    def delisting_events(self, corp_codes, start, end):
        wanted = set(corp_codes)
        return [
            dict(row) for row in self.delisting_rows.values()
            if row["corp_code"] in wanted
            and start <= (row.get("effective_on") or row["received_on"]) <= end
        ]

    def company_history(self, company_ids):
        wanted = set(company_ids)
        return [dict(row) for (company_id, _year, _quarter), row in self.company_rows.items() if company_id in wanted]

    def company_periods(self, company_ids, periods):
        wanted = set(company_ids)
        wanted_periods = tuple(dict.fromkeys(periods))
        self.company_period_calls.append((wanted, wanted_periods))
        return [
            dict(row)
            for (company_id, year, quarter), row in self.company_rows.items()
            if company_id in wanted and (year, quarter) in wanted_periods
        ]

    def market_history(self, market_id):
        return [dict(row) for (stored_market, _year, _quarter), row in self.market_rows.items() if stored_market == market_id]

    def market_periods(self, market_ids, periods):
        wanted = set(market_ids)
        wanted_periods = set(periods)
        return [
            dict(row)
            for (market_id, year, quarter), row in self.market_rows.items()
            if market_id in wanted and (year, quarter) in wanted_periods
        ]

    def seasonal_windows(self, entity_type, entity_ids):
        wanted = set(entity_ids)
        return [
            dict(row)
            for (stored_type, entity_id, _metric, _quarter), row in self.seasonal_rows.items()
            if stored_type == entity_type and entity_id in wanted
        ]

    def upsert_seasonal_windows(self, rows):
        materialized = list(rows)
        for row in materialized:
            key = (row["entity_type"], row["entity_id"], row["metric"], row["fiscal_quarter"])
            self.seasonal_rows[key] = dict(row)
        return len(materialized)

    def upsert_company_quarters(self, rows):
        materialized = list(rows)
        for row in materialized:
            self.company_rows[(row["company_id"], row["fiscal_year"], row["fiscal_quarter"])] = dict(row)
        return len(materialized)

    def replace_company_quarters_for_backfill(self, rows):
        return self.upsert_company_quarters(rows)

    def upsert_market_quarters(self, rows):
        materialized = list(rows)
        for row in materialized:
            self.market_rows[(row["market_id"], row["market_year"], row["market_quarter"])] = dict(row)
        return len(materialized)

    def pipeline_state(self, operation):
        return self.states.get(operation)

    def pending_rows(self):
        return [dict(row) for row in self.stale_pending_rows]

    def save_state(self, operation, status, cursor, error=None):
        record = {"status": status, "cursor": dict(cursor), "last_error": error}
        self.states[operation] = record
        self.saved_states.append((operation, status, dict(cursor), error))


class SimulatedDart:
    def __init__(self, filings=(), delistings=(), mergers=()):
        self.filings = list(filings)
        self.delistings = list(delistings)
        self.mergers = list(mergers)
        self.financial_calls: list[tuple[tuple[str, ...], int, int]] = []
        self.profile_calls: list[str] = []
        self.single_calls: list[tuple[str, int, int, str]] = []

    @property
    def request_count(self):
        return len(self.financial_calls)

    def periodic_filings(self, _start, _end):
        return list(self.filings)

    def delisting_filings(self, _start, _end, *, corp_code=None):
        return [
            row for row in self.delistings
            if corp_code is None or row.corp_code == corp_code
        ]

    def merger_decision_corp_codes(self, _start, _end):
        return {row.corp_code for row in self.mergers}

    def absorbed_merger_filings(self, _start, _end, *, corp_code):
        return [row for row in self.mergers if row.corp_code == corp_code]

    def multi_accounts(self, corp_codes, year, quarter):
        codes = tuple(corp_codes)
        self.financial_calls.append((codes, year, quarter))
        if quarter == 1:
            return [item for corp in codes for item in complete(corp, current="100", cumulative="100")]
        return [item for corp in codes for item in complete(corp, current="20", cumulative="120")]

    def company_profile(self, corp_code):
        self.profile_calls.append(corp_code)
        return {"industry_code": "62010"}

    def single_accounts(self, corp_code, year, quarter, scope):
        self.single_calls.append((corp_code, year, quarter, scope))
        if quarter == 1:
            return complete(corp_code, current="100", cumulative="100", scope=scope)
        return complete(corp_code, current="20", cumulative="120", scope=scope)


class ForeignCurrencyDart(SimulatedDart):
    def __init__(self, target_corp: str, currency: str, filings=()):
        super().__init__(filings)
        self.target_corp = target_corp
        self.currency = currency

    def multi_accounts(self, corp_codes, year, quarter):
        rows = super().multi_accounts(corp_codes, year, quarter)
        return [
            {**item, "currency": self.currency} if item["corp_code"] == self.target_corp else item
            for item in rows
        ]


class UsdDart(ForeignCurrencyDart):
    def __init__(self, target_corp: str, filings=()):
        super().__init__(target_corp, "USD", filings)


class JpyDart(ForeignCurrencyDart):
    def __init__(self, target_corp: str, filings=()):
        super().__init__(target_corp, "JPY", filings)


class SimulatedKis:
    def __init__(self, values=None):
        self.values = values or {}
        self.calls: list[tuple[str, int, int]] = []
        self.history_calls: list[str] = []

    @property
    def request_count(self):
        return len(self.calls) + len(self.history_calls)

    def financial_history(self, ticker):
        self.history_calls.append(ticker)
        value = self.values.get(ticker)
        if isinstance(value, dict) and all(
            isinstance(key, tuple) and len(key) == 2 for key in value
        ):
            return value
        return {}

    def quarter_cumulative_financials(self, ticker, year, quarter):
        self.calls.append((ticker, year, quarter))
        value = self.values.get(ticker)
        if isinstance(value, dict) and value and all(
            isinstance(item, dict) for item in value.values()
        ):
            return value
        if isinstance(value, dict):
            return {
                field: {"current": amount, "previous": Decimal(0)}
                for field, amount in value.items()
            }
        return {
            "top_line": {"current": value, "previous": Decimal(0)},
            "operating_income": {
                "current": Decimal("10") if value is not None else None,
                "previous": Decimal(0),
            },
            "net_income": {
                "current": Decimal("8") if value is not None else None,
                "previous": Decimal(0),
            },
        }


class FailingKis(SimulatedKis):
    def financial_history(self, ticker):
        self.history_calls.append(ticker)
        raise ProviderError(f"KIS top-line request failed for {ticker}")

    def quarter_cumulative_financials(self, ticker, year, quarter):
        self.calls.append((ticker, year, quarter))
        raise ProviderError(f"KIS top-line request failed for {ticker}")


class FailingFx:
    def __init__(self):
        self.request_count = 0

    def latest_usd_krw(self, _reference_date):
        self.request_count += 1
        raise ProviderError("ECOS USD/KRW timed out (ConnectTimeout)")

    def latest_krw(self, base_currency, _reference_date):
        self.request_count += 1
        raise ProviderError(f"ECOS {base_currency}/KRW timed out (ConnectTimeout)")


class FixedFx:
    def __init__(self, rate=Decimal("1300")):
        self.rate = rate
        self.request_count = 0

    def latest_usd_krw(self, reference_date):
        self.request_count += 1
        return reference_date, self.rate

    def latest_krw(self, _base_currency, reference_date):
        self.request_count += 1
        return reference_date, self.rate


class SimulatedKrx:
    request_count = 0


class OpenDartTransportTests(unittest.TestCase):
    def test_structured_merger_parser_accepts_only_absorbed_company(self):
        absorbed = parse_absorbed_merger({
            "corp_code": "00838421",
            "rcept_no": "20180117001035",
            "corp_name": "씨제이이앤엠",
            "mgptncmp_cmpnm": "(주)씨제이오쇼핑\n(CJ O SHOPPING CO., Ltd)",
            "mg_mth": "(주)씨제이오쇼핑이 씨제이이앤엠(주)를 흡수합병\n- 존속회사: (주)씨제이오쇼핑\n- 소멸회사: 씨제이이앤엠(주)",
            "mgsc_mgdt": "2018년 07월 01일",
        }, expected_corp_code="00838421")
        survivor = parse_absorbed_merger({
            "corp_code": "00123456",
            "rcept_no": "20180117001036",
            "corp_name": "주식회사 씨제이오쇼핑",
            "mgptncmp_cmpnm": "씨제이이앤엠 주식회사",
            "mg_mth": "주식회사 씨제이오쇼핑이 씨제이이앤엠 주식회사를 흡수합병",
            "mgsc_mgdt": "2018.07.01",
        }, expected_corp_code="00123456")

        self.assertIsNotNone(absorbed)
        assert absorbed is not None
        self.assertEqual(absorbed.effective_on, date(2018, 7, 1))
        self.assertIsNone(survivor)

    def test_legacy_merger_archive_requires_explicit_dissolved_company(self):
        document = """
        <TABLE><TR><TD>합병방법</TD><TD>미래에셋대우 주식회사가 미래에셋증권 주식회사를 흡수합병
        - 존속법인 : 미래에셋대우 주식회사 - 소멸법인 : 미래에셋증권 주식회사</TD></TR>
        <TR><TD>합병기일</TD><TD>2016년 11월 01일</TD></TR></TABLE>
        """
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("report.xml", document.encode("cp949"))

        event = parse_absorbed_merger_archive(
            buffer.getvalue(), expected_corp_code="00311030",
            corp_name="미래에셋증권", receipt_no="20160513004518",
        )
        survivor = parse_absorbed_merger_archive(
            buffer.getvalue(), expected_corp_code="00111722",
            corp_name="미래에셋대우", receipt_no="20160513004348",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.effective_on, date(2016, 11, 1))
        self.assertIsNone(survivor)

    def test_legacy_merger_archive_accepts_explicit_absorbed_company(self):
        document = """
        <TABLE><TR><TD>합병 당사회사</TD><TD>합병회사 : 주식회사 지에스리테일
        피합병회사 : 주식회사 지에스홈쇼핑</TD></TR>
        <TR><TD>합병기일</TD><TD>2021년 07월 01일</TD></TR></TABLE>
        """
        buffer = BytesIO()
        with ZipFile(buffer, "w") as archive:
            archive.writestr("report.xml", document.encode("cp949"))

        event = parse_absorbed_merger_archive(
            buffer.getvalue(), expected_corp_code="00207755",
            corp_name="지에스홈쇼핑", receipt_no="20201110000001",
        )
        survivor = parse_absorbed_merger_archive(
            buffer.getvalue(), expected_corp_code="00676928",
            corp_name="지에스리테일", receipt_no="20201110000002",
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.effective_on, date(2021, 7, 1))
        self.assertIsNone(survivor)

    def test_corporation_map_streams_the_archive(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(*, chunk_size):
                self.assertEqual(chunk_size, 64 * 1024)
                return iter([b"not-a-zip"])

        class Session:
            def __init__(self):
                self.kwargs = None

            def get(self, _url, **kwargs):
                self.kwargs = kwargs
                return Response()

        session = Session()
        client = OpenDartClient("secret", session=session, interval=0)
        with self.assertRaises(RuntimeError):
            client.corporation_map()
        self.assertTrue(session.kwargs["stream"])

    def test_multi_account_batches_are_capped_at_one_hundred(self):
        class Response:
            content = b""

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"status": "000", "list": []}

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return Response()

        session = Session()
        client = OpenDartClient("secret", session=session, interval=0)
        client.multi_accounts([f"{index:08d}" for index in range(150)], 2026, 2)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(session.calls[0][1]["timeout"], (5, 20))
        self.assertEqual(len(session.calls[0][1]["params"]["corp_code"].split(",")), 100)
        self.assertEqual(len(session.calls[1][1]["params"]["corp_code"].split(",")), 50)

    def test_multi_account_splits_only_retryable_failed_hundred_company_batch(self):
        class Client(OpenDartClient):
            def __init__(self):
                super().__init__("secret", interval=0)
                self.calls = []

            def _multi_account_batch(self, corp_codes, year, quarter, *, retry_total=None):
                batch = list(corp_codes)
                self.calls.append((batch, retry_total))
                if len(batch) == 100:
                    raise ProviderError("failed", retryable=True)
                return []

        client = Client()
        client.multi_accounts([f"{index:08d}" for index in range(100)], 2026, 2)
        self.assertEqual(
            [(len(batch), retry_total) for batch, retry_total in client.calls],
            [(100, 1), (50, 0), (50, 0)],
        )

    def test_multi_account_retries_hundred_twice_then_calls_each_half_once(self):
        class Response:
            status_code = 200

            def __init__(self, content, content_type):
                self.content = content
                self.headers = {"Content-Type": content_type}

            @staticmethod
            def raise_for_status():
                return None

            def iter_content(self, chunk_size):
                assert chunk_size == 64 * 1024
                yield self.content

        class Session:
            def __init__(self):
                self.batch_sizes = []

            def get(self, _url, **kwargs):
                size = len(kwargs["params"]["corp_code"].split(","))
                self.batch_sizes.append(size)
                if size == 100:
                    return Response(b"<html>temporary error</html>", "text/html")
                return Response(b'{"status":"000","list":[]}', "application/json")

        session = Session()
        client = OpenDartClient("secret", session=session, interval=0)

        client.multi_accounts([f"{index:08d}" for index in range(100)], 2026, 2)

        self.assertEqual(session.batch_sizes, [100, 100, 50, 50])

    def test_multi_account_does_not_split_non_retryable_failure(self):
        class Client(OpenDartClient):
            def __init__(self):
                super().__init__("secret", interval=0)
                self.calls = []

            def _multi_account_batch(self, corp_codes, year, quarter, *, retry_total=None):
                self.calls.append((list(corp_codes), retry_total))
                raise ProviderError("rejected")

        client = Client()
        with self.assertRaisesRegex(ProviderError, "rejected"):
            client.multi_accounts([f"{index:08d}" for index in range(100)], 2026, 2)
        self.assertEqual([(len(batch), retries) for batch, retries in client.calls], [(100, 1)])

    def test_multi_account_stops_when_a_split_batch_fails(self):
        class Client(OpenDartClient):
            def __init__(self):
                super().__init__("secret", interval=0)
                self.calls = []

            def _multi_account_batch(self, corp_codes, year, quarter, *, retry_total=None):
                batch = list(corp_codes)
                self.calls.append((batch, retry_total))
                if len(batch) == 100:
                    raise ProviderError("failed", retryable=True)
                raise ProviderError("split failed", retryable=True)

        client = Client()
        with self.assertRaisesRegex(ProviderError, "split failed"):
            client.multi_accounts([f"{index:08d}" for index in range(100)], 2026, 2)
        self.assertEqual(
            [(len(batch), retries) for batch, retries in client.calls],
            [(100, 1), (50, 0)],
        )

    def test_provider_error_does_not_expose_key(self):
        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                raise RuntimeError("secret")

        client = OpenDartClient("secret", session=Session(), interval=0)
        with self.assertRaises(RuntimeError) as captured:
            client.multi_accounts(["00000001"], 2026, 1)
        self.assertNotIn("secret", str(captured.exception))
        self.assertIn("RuntimeError", str(captured.exception))

    def test_periodic_filings_preserve_unique_receipt_numbers(self):
        class Response:
            content = b"1"

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "status": "000", "total_page": 1,
                    "list": [
                        {"corp_code": "00123456", "rcept_no": "20260814000001", "rcept_dt": "20260814", "report_nm": "분기보고서 (2026.03)"},
                        {"corp_code": "00123456", "rcept_no": "20260814000001", "rcept_dt": "20260814", "report_nm": "분기보고서 (2026.03)"},
                        {"corp_code": "00999999", "rcept_no": "20260814000002", "rcept_dt": "20260814", "report_nm": "주요사항보고서"},
                    ],
                }

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = OpenDartClient("secret", session=Session(), interval=0)
        filings = client.periodic_filings(date(2026, 8, 1), date(2026, 8, 14))
        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0].receipt_no, "20260814000001")

    def test_delisting_filings_accept_only_normalized_exact_titles(self):
        class Client(OpenDartClient):
            def __init__(self):
                super().__init__("secret", interval=0)

            def _get(self, endpoint, params, *, binary=False, retry_total=None):
                if endpoint != "list.json" or params.get("pblntf_ty") != "I":
                    raise AssertionError("unexpected OpenDART request")
                return {
                    "status": "000", "total_page": 1,
                    "list": [
                        {"corp_code": "00123456", "rcept_no": "20210630000001", "rcept_dt": "20210630", "report_nm": "[코스닥시장] 상장폐지 결정"},
                        {"corp_code": "00123456", "rcept_no": "20210701000001", "rcept_dt": "20210701", "report_nm": "[기재정정]상장폐지"},
                        {"corp_code": "00123456", "rcept_no": "20210701000002", "rcept_dt": "20210701", "report_nm": "[코스닥시장] 상장폐지(피흡수합병)"},
                        {"corp_code": "00999999", "rcept_no": "20210630000002", "rcept_dt": "20210630", "report_nm": "상장폐지 예정"},
                        {"corp_code": "00999998", "rcept_no": "20210630000003", "rcept_dt": "20210630", "report_nm": "상장폐지사유발생"},
                    ],
                }

        filings = Client().delisting_filings(
            date(2021, 4, 1), date(2021, 6, 30), corp_code="00123456",
        )
        self.assertEqual([row.event_type for row in filings], ["decision", "final", "final"])
        self.assertEqual(
            [row.receipt_no for row in filings],
            ["20210630000001", "20210701000001", "20210701000002"],
        )

    def test_company_profile_and_single_accounts_use_structured_endpoints(self):
        class Client(OpenDartClient):
            def __init__(self):
                super().__init__("secret", interval=0)
                self.requests = []

            def _get(self, endpoint, params, *, binary=False):
                self.requests.append((endpoint, params, binary))
                if endpoint == "company.json":
                    return {"status": "000", "induty_code": "64110"}
                return {"status": "000", "list": [{"corp_code": "00123456"}]}

        client = Client()
        self.assertEqual(client.company_profile("00123456"), {"industry_code": "64110"})
        self.assertEqual(client.single_accounts("00123456", 2026, 2, "CFS"), [
            {"corp_code": "00123456", "fs_div": "CFS"},
        ])
        self.assertEqual(client.requests, [
            ("company.json", {"corp_code": "00123456"}, False),
            ("fnlttSinglAcntAll.json", {
                "corp_code": "00123456", "bsns_year": "2026",
                "reprt_code": "11012", "fs_div": "CFS",
            }, False),
        ])

    def test_single_accounts_preserves_provider_scope_when_present(self):
        class Client(OpenDartClient):
            def __init__(self):
                super().__init__("secret", interval=0)

            def _get(self, endpoint, params, *, binary=False):
                return {"status": "000", "list": [{"fs_div": "OFS", "account_nm": "영업수익"}]}

        self.assertEqual(Client().single_accounts("00123456", 2026, 1, "CFS"), [
            {"fs_div": "OFS", "account_nm": "영업수익"},
        ])

    def test_periodic_filing_period_uses_report_reference_month(self):
        filing = PeriodicFiling("00123456", "20260814000001", date(2026, 8, 14), "[기재정정]반기보고서 (2026.06)")
        self.assertEqual(filing_period(filing), (2026, 2))


class ProviderReliabilityTests(unittest.TestCase):
    def test_shared_session_retries_only_bounded_transient_failures(self):
        retry = resilient_session().get_adapter("https://").max_retries

        self.assertEqual(retry.total, RETRY_TOTAL)
        self.assertEqual(retry.connect, RETRY_TOTAL)
        self.assertEqual(retry.read, RETRY_TOTAL)
        self.assertEqual(retry.status, RETRY_TOTAL)
        self.assertEqual(frozenset(retry.status_forcelist), RETRYABLE_STATUS_CODES)
        self.assertTrue(retry.respect_retry_after_header)
        self.assertEqual(retry.allowed_methods, frozenset({"GET"}))

    def test_provider_session_has_no_hidden_adapter_retries(self):
        retry = provider_session().get_adapter("https://").max_retries
        self.assertEqual(retry.total, 0)

    def test_streaming_response_is_stopped_by_total_deadline(self):
        clock = [0.0]

        class Response:
            status_code = 200
            headers = {}

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(chunk_size):
                self.assertEqual(chunk_size, 64 * 1024)
                clock[0] = 6.0
                yield b'{"status":"000"}'

        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        session = Session()
        with self.assertRaises(ResponseDeadlineExceeded):
            bounded_request(
                session, "GET", "https://provider.invalid",
                provider="test", operation="stream", total_timeout=5,
                attempt_timeout=None,
                connect_timeout=2, read_timeout=2,
                monotonic=lambda: clock[0], sleep=lambda _seconds: None,
            )
        self.assertEqual(session.calls, 1)

    def test_streaming_response_has_no_total_deadline_when_disabled(self):
        clock = [0.0]

        class Response:
            status_code = 200
            headers = {}

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(chunk_size):
                self.assertEqual(chunk_size, 64 * 1024)
                clock[0] = 45.0
                yield b'{"status":"000"}'

        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        session = Session()
        result = bounded_request(
            session, "GET", "https://provider.invalid",
            provider="test", operation="stream", total_timeout=None,
            attempt_timeout=None,
            connect_timeout=5, read_timeout=20,
            monotonic=lambda: clock[0], sleep=lambda _seconds: None,
        )

        self.assertEqual(result, {"status": "000"})
        self.assertEqual(session.calls, 1)

    def test_transport_progress_reports_headers_and_completed_body(self):
        events = []

        class Response:
            status_code = 200
            headers = {"Content-Length": "16", "Content-Type": "application/json"}

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(chunk_size):
                yield b'{"status":"000"}'

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        result = bounded_request(
            Session(), "GET", "https://provider.invalid",
            provider="test", operation="stream", total_timeout=None,
            attempt_timeout=None, connect_timeout=5, read_timeout=20,
            on_progress=lambda event, details: events.append((event, details)),
        )
        self.assertEqual(result, {"status": "000"})
        self.assertEqual([event for event, _details in events], ["headers", "body"])
        self.assertTrue(events[-1][1]["complete"])
        self.assertEqual(events[-1][1]["chunk_count"], 1)

    def test_provider_retries_invalid_json_response_only_after_body_completes(self):
        retries = []

        class Response:
            status_code = 200
            headers = {"Content-Type": "text/html"}

            @staticmethod
            def raise_for_status():
                return None

            def __init__(self, content):
                self.content = content

            def iter_content(self, chunk_size):
                self.assert_chunk_size(chunk_size)
                yield self.content

            @staticmethod
            def assert_chunk_size(chunk_size):
                self.assertEqual(chunk_size, 64 * 1024)

        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return Response(b"<html>temporary error</html>")
                return Response(b'{"status":"000"}')

        session = Session()
        result = bounded_request(
            session, "GET", "https://provider.invalid",
            provider="test", operation="json", total_timeout=None,
            attempt_timeout=None, connect_timeout=5, read_timeout=20,
            sleep=lambda _seconds: None,
            on_retry=lambda attempt, reason, remaining: retries.append(
                (attempt, reason, remaining)
            ),
        )

        self.assertEqual(result, {"status": "000"})
        self.assertEqual(session.calls, 2)
        self.assertEqual(len(retries), 1)
        self.assertIn("InvalidJsonResponse", retries[0][1])

    def test_provider_stops_after_invalid_json_retry_limit(self):
        class Response:
            status_code = 200
            headers = {"Content-Type": "text/html"}

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def iter_content(chunk_size):
                yield b"<html>temporary error</html>"

        class Session:
            calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response()

        session = Session()
        with self.assertRaises(InvalidJsonResponse):
            bounded_request(
                session, "GET", "https://provider.invalid",
                provider="test", operation="json", total_timeout=None,
                attempt_timeout=None, connect_timeout=5, read_timeout=20,
                sleep=lambda _seconds: None,
            )

        self.assertEqual(session.calls, RETRY_TOTAL + 1)

    def test_provider_retries_share_one_total_budget(self):
        clock = [0.0]

        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"ok": True}

        class Session:
            calls = []

            def get(self, *_args, **kwargs):
                self.calls.append(kwargs["timeout"])
                if len(self.calls) == 1:
                    clock[0] = 2.0
                    raise requests.ConnectTimeout()
                return Response()

        def advance(seconds):
            clock[0] += seconds

        session = Session()
        result = bounded_request(
            session, "GET", "https://provider.invalid",
            provider="test", operation="retry", total_timeout=5,
            attempt_timeout=4,
            connect_timeout=4, read_timeout=4,
            monotonic=lambda: clock[0], sleep=advance,
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(session.calls), 2)
        self.assertLess(session.calls[1][0], session.calls[0][0])

    def test_ecos_reports_timeout_type_without_exposing_key(self):
        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                raise requests.ConnectTimeout("secret-key")

        client = EcosFxClient("secret-key", session=Session())
        with self.assertRaisesRegex(ProviderError, r"ECOS USD/KRW timed out \(ConnectTimeout\)") as captured:
            client.latest_usd_krw(date(2025, 12, 31))
        self.assertNotIn("secret-key", str(captured.exception))

    def test_ecos_reports_sanitized_provider_result_code(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"RESULT": {"CODE": "ERROR-101", "MESSAGE": "sensitive detail"}}

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = EcosFxClient("secret", session=Session())
        with self.assertRaisesRegex(ProviderError, r"rejected the request \(ERROR-101\)") as captured:
            client.latest_usd_krw(date(2025, 12, 31))
        self.assertNotIn("sensitive detail", str(captured.exception))

    def test_ecos_returns_latest_observation_with_rate(self):
        class Response:
            content = b"{}"

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"StatisticSearch": {"row": [
                    {"TIME": "20250627", "DATA_VALUE": "1350.5"},
                    {"TIME": "20250630", "DATA_VALUE": "1360.25"},
                ]}}

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = EcosFxClient("secret", session=Session())

        observed_on, rate = client.latest_usd_krw(date(2025, 6, 30))

        self.assertEqual(observed_on, date(2025, 6, 30))
        self.assertEqual(rate, Decimal("1360.25"))
        self.assertEqual(client.request_count, 1)

    def test_ecos_normalizes_jpy_quote_to_one_yen(self):
        class Response:
            content = b"{}"

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"StatisticSearch": {"row": [
                    {"TIME": "20250630", "DATA_VALUE": "950"},
                ]}}

        class Session:
            calls = []

            def get(self, url, **_kwargs):
                self.calls.append(url)
                return Response()

        session = Session()
        client = EcosFxClient("secret", session=session)

        observed_on, rate = client.latest_krw("JPY", date(2025, 6, 30))

        self.assertEqual(observed_on, date(2025, 6, 30))
        self.assertEqual(rate, Decimal("9.50"))
        self.assertIn("/0000002", session.calls[0])

    def test_ecos_uses_official_eur_and_cny_items_without_unit_scaling(self):
        class Response:
            content = b"{}"

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {"StatisticSearch": {"row": [
                    {"TIME": "20250630", "DATA_VALUE": "190"},
                ]}}

        class Session:
            def __init__(self):
                self.calls = []

            def get(self, url, **_kwargs):
                self.calls.append(url)
                return Response()

        session = Session()
        client = EcosFxClient("secret", session=session)

        for currency, item_code in (("EUR", "0000003"), ("CNY", "0000053")):
            with self.subTest(currency=currency):
                _observed_on, rate = client.latest_krw(currency, date(2025, 6, 30))
                self.assertEqual(rate, Decimal("190"))
                self.assertIn(f"/{item_code}", session.calls[-1])


class CliContractTests(unittest.TestCase):
    def test_korean_market_targets_are_one_hundred_each(self):
        self.assertEqual(TARGETS, {"kr_largecap": 100, "kr_kosdaq": 100})

    def test_application_deadline_precedes_workflow_hard_stop(self):
        self.assertEqual(AUTOMATIC_DEADLINE_SECONDS, 240)
        self.assertEqual(QUARTER_DEADLINE_SECONDS, 600)
        self.assertLess(AUTOMATIC_DEADLINE_SECONDS, 300)
        self.assertLess(QUARTER_DEADLINE_SECONDS, 660)

    def test_year_backfill_always_runs_oldest_quarter_first(self):
        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=object(), repository=object())
        visited: list[int] = []
        deadlines: list[int | None] = []

        def run_quarter(_year, quarter, **kwargs):
            visited.append(quarter)
            deadlines.append(kwargs.get("deadline_seconds"))
            return {"status": "ready", "quarter": quarter}

        pipeline.run_quarter = run_quarter

        pipeline.run_year(2026, deadline_seconds=600)

        self.assertEqual(visited, [1, 2, 3, 4])
        self.assertEqual(deadlines, [600, 600, 600, 600])

    def test_year_backfill_continues_after_an_incomplete_quarter(self):
        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=object(), repository=object())
        visited: list[int] = []

        def run_quarter(_year, quarter, **_kwargs):
            visited.append(quarter)
            return {"status": "incomplete" if quarter == 1 else "ready", "quarter": quarter}

        pipeline.run_quarter = run_quarter

        results = pipeline.run_year(2026)

        self.assertEqual(visited, [1, 2, 3, 4])
        self.assertEqual([row["status"] for row in results], ["incomplete", "ready", "ready", "ready"])

    def test_ready_and_incomplete_results_are_successful(self):
        self.assertTrue(completed_successfully({"status": "ready"}))
        self.assertTrue(completed_successfully([{"status": "ready"}, {"status": "ready"}]))
        self.assertTrue(completed_successfully({"status": "incomplete"}))
        self.assertTrue(completed_successfully([{"status": "ready"}, {"status": "incomplete"}]))
        self.assertFalse(completed_successfully({"status": "failed"}))
        self.assertFalse(completed_successfully([]))

    def test_recalculation_mode_is_an_explicit_cli_path(self):
        args = parser().parse_args(["--year", "2026", "--quarter", "2", "--write", "--recalculate-only"])
        self.assertTrue(args.recalculate_only)

    def test_pending_only_mode_is_an_explicit_historical_quarter_path(self):
        args = parser().parse_args(["--year", "2017", "--quarter", "3", "--write", "--pending-only"])
        self.assertTrue(args.pending_only)

    def test_backfill_cli_has_no_automatic_mode(self):
        self.assertNotIn("--daily", parser().format_help())

    def test_automatic_pipeline_exposes_no_year_backfill(self):
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=object(), dart=object(), repository=object(),
        )

        self.assertFalse(hasattr(pipeline, "run_year"))
        self.assertEqual(AUTOMATIC_DEADLINE_SECONDS, 240)

    def test_automatic_pipeline_rejects_backfill_policies_before_provider_calls(self):
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=object(), dart=object(), repository=object(),
        )

        with self.assertRaisesRegex(ValueError, "incremental mode"):
            pipeline.run_quarter(2026, 2, incremental=False)
        with self.assertRaisesRegex(ValueError, "backfill policy"):
            pipeline.run_quarter(2026, 2, allow_backfill_zero_top_line=True)
        with self.assertRaisesRegex(ValueError, "backfill policy"):
            pipeline.run_quarter(2026, 2, trust_previous_backfill=True)

class DailyCheckpointTests(unittest.TestCase):
    def test_daily_run_deduplicates_boundary_receipts_and_advances_after_success(self):
        class Repository:
            saved = []

            @staticmethod
            def pipeline_state(_operation):
                return {"cursor": {
                    "last_checked_date": "2026-09-01",
                    "boundary_receipt_ids": ["20260901000001"],
                }}

            def save_state(self, operation, status, cursor, error=None):
                self.saved.append((operation, status, cursor, error))

            @staticmethod
            def pending_rows():
                return []

        class Dart:
            @staticmethod
            def periodic_filings(start, end):
                self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 2)))
                return [
                    PeriodicFiling("00000001", "20260901000001", date(2026, 9, 1), "반기보고서 (2026.06)"),
                    PeriodicFiling("00000002", "20260902000001", date(2026, 9, 2), "반기보고서 (2026.06)"),
                ]

            @staticmethod
            def delisting_filings(_start, _end, *, corp_code=None):
                return []

        repository = Repository()
        pipeline = KoreaEarningsV2AutomaticPipeline(krx=object(), dart=Dart(), repository=repository)
        captured = {}

        def run_quarter(year, quarter, **kwargs):
            captured.update({"year": year, "quarter": quarter, **kwargs})
            return {"status": "incomplete"}

        pipeline.run_quarter = run_quarter
        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))
        self.assertEqual(captured["refresh_corp_codes"], {"00000002"})
        self.assertEqual(result["filing_discovery"]["new_receipts"], 1)
        self.assertEqual(repository.saved[-1][0:2], ("daily_filings", "incomplete"))
        self.assertEqual(repository.saved[-1][2]["last_checked_date"], "2026-09-02")
        self.assertEqual(repository.saved[-1][2]["boundary_receipt_ids"], ["20260902000001"])

    def test_first_daily_run_starts_from_the_day_it_is_enabled(self):
        class Repository:
            @staticmethod
            def pipeline_state(_operation):
                return None

            @staticmethod
            def save_state(*_args, **_kwargs):
                return None

            @staticmethod
            def pending_rows():
                return []

        class Dart:
            @staticmethod
            def periodic_filings(start, end):
                self.assertEqual((start, end), (date(2026, 9, 2), date(2026, 9, 2)))
                return []

            @staticmethod
            def delisting_filings(_start, _end, *, corp_code=None):
                return []

        pipeline = KoreaEarningsV2AutomaticPipeline(krx=object(), dart=Dart(), repository=Repository())
        pipeline.run_quarter = lambda *_args, **_kwargs: {"status": "incomplete"}
        pipeline.run_daily(write=True, today=date(2026, 9, 2))

    def test_dart_daily_does_not_retry_stale_pending_periods(self):
        class Repository:
            saved = []

            @staticmethod
            def pipeline_state(_operation):
                return None

            @staticmethod
            def pending_rows():
                return [
                    {"market_year": 2026, "market_quarter": 1},
                    {"market_year": 2026, "market_quarter": 1},
                ]

            def save_state(self, operation, status, cursor, error=None):
                self.saved.append((operation, status, cursor, error))

        class Dart:
            @staticmethod
            def periodic_filings(_start, _end):
                return []

            @staticmethod
            def delisting_filings(_start, _end, *, corp_code=None):
                return []

        calls = []
        repository = Repository()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=object(), dart=Dart(), repository=repository,
        )

        def run_quarter(year, quarter, **kwargs):
            calls.append((year, quarter, kwargs))
            return {
                "period": f"{year}Q{quarter}",
                "status": "incomplete" if quarter == 1 else "ready",
                "retried_pending_companies": 2 if quarter == 1 else 0,
            }

        pipeline.run_quarter = run_quarter
        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual([(year, quarter) for year, quarter, _ in calls], [(2026, 2)])
        self.assertFalse(calls[0][2]["use_kis_for_fresh"])
        self.assertFalse(calls[0][2]["retry_pending"])
        self.assertTrue(calls[0][2]["refresh_only"])
        self.assertEqual(result["stale_pending_retries"], [])


class IncrementalLifecycleSimulationTests(unittest.TestCase):
    def populated_repository(self, *, missing_company: str | None = None) -> SimulatedRepository:
        repository = SimulatedRepository()
        for rows in repository.universes.values():
            for identity in rows:
                if identity["company_id"] != missing_company:
                    repository.seed_company(identity["company_id"])
        return repository

    def test_new_receipt_refreshes_only_one_company_then_duplicate_is_noop(self):
        target_company = "kr:00000100"
        target_corp = "00000100"
        receipt = PeriodicFiling(
            target_corp, "20260902000001", date(2026, 9, 2), "반기보고서 (2026.06)",
        )
        repository = self.populated_repository(missing_company=target_company)
        dart = SimulatedDart([receipt])
        kis = SimulatedKis()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        first = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(first["filing_discovery"]["new_receipts"], 1)
        self.assertEqual(first["refreshed_companies"], 1)
        self.assertEqual([call[0] for call in dart.financial_calls], [(target_corp,)])
        self.assertEqual(kis.calls, [])
        self.assertTrue(repository.company_period_calls)
        self.assertTrue(all(len(periods) == 3 for _companies, periods in repository.company_period_calls))
        self.assertEqual(repository.market_rows[("kr_largecap", 2026, 2)]["lifecycle_status"], "complete")
        self.assertEqual(repository.market_rows[("kr_largecap", 2026, 2)]["reported_company_count"], 100)
        self.assertEqual(repository.market_rows[("kr_kosdaq", 2026, 2)]["reported_company_count"], 100)

        calls_after_first_run = list(dart.financial_calls)
        second = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(second["filing_discovery"]["new_receipts"], 0)
        self.assertEqual(second["refreshed_companies"], 0)
        self.assertEqual(dart.financial_calls, calls_after_first_run)
        self.assertEqual(kis.calls, [])

    def test_dart_daily_does_not_repeat_pending_company_fallbacks(self):
        target_company = "kr:00000099"
        repository = self.populated_repository()
        repository.seed_company(target_company, top_line=None, pending=True)
        repository.company_profiles[target_company] = {
            "industry_code": "64110", "entity_kind": "financial",
        }
        target_ticker = "000099"
        dart = SimulatedDart()
        kis = SimulatedKis({target_ticker: Decimal("250")})
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(dart.financial_calls, [])
        self.assertEqual(dart.single_calls, [])
        self.assertEqual(dart.profile_calls, [])
        self.assertEqual(kis.calls, [])
        self.assertEqual(result["retried_pending_companies"], 0)
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertIsNone(stored["top_line"])
        self.assertTrue(stored["is_pending"])

    def test_delisting_decision_carries_previous_quarter_and_skips_all_providers(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.seed_company(
            target_company, top_line=None, operating_income=Decimal("10"),
            net_income=Decimal("8"), pending=True,
        )
        previous = fact(2026, 1, "7", company=target_company).with_changes(
            source_top_line_cumulative=Decimal("70"),
            source_operating_income_cumulative=Decimal("7"),
            source_net_income_cumulative=Decimal("7"),
        )
        repository.company_rows[(target_company, 2026, 1)] = previous.db_row(calculation_version=6)
        decision = DelistingFiling(
            target_corp, "20260620000001", date(2026, 6, 20),
            "상장폐지결정", "decision",
        )
        dart = SimulatedDart(delistings=[decision])
        kis = SimulatedKis()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("70"))
        self.assertEqual(stored["operating_income"], Decimal("7"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(stored["source_filing_id"], "delisting_previous_quarter:20260620000001")
        self.assertFalse(any(target_corp in call[0] for call in dart.financial_calls))
        self.assertFalse(any(call[0] == target_corp for call in dart.single_calls))
        self.assertEqual(kis.calls, [])
        self.assertEqual(result["resolved_delisting_companies"], 1)

        final = DelistingFiling(
            target_corp, "20260629000001", date(2026, 6, 29),
            "상장폐지", "final",
        )
        pipeline.run_quarter(
            2026, 2, write=True, incremental=True,
            delisting_filings=[final],
        )
        self.assertEqual(
            repository.company_rows[(target_company, 2026, 2)]["source_filing_id"],
            "delisting_previous_quarter:20260620000001",
        )

    def test_absorbed_merger_uses_effective_date_and_previous_quarter(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.seed_company(
            target_company, top_line=None,
            operating_income=None, net_income=None, pending=True,
        )
        previous = fact(2026, 1, "9", company=target_company).with_changes(
            source_top_line_cumulative=Decimal("90"),
            source_operating_income_cumulative=Decimal("9"),
            source_net_income_cumulative=Decimal("9"),
        )
        repository.company_rows[(target_company, 2026, 1)] = previous.db_row(
            calculation_version=6,
        )
        merger = DelistingFiling(
            target_corp, "20260117000001", date(2026, 1, 17),
            "회사합병 결정(피흡수합병)", "absorbed_merger",
            effective_on=date(2026, 7, 1),
        )
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(mergers=[merger]),
            repository=repository, kis=SimulatedKis(),
        )

        before_effective = pipeline.run_quarter(
            2026, 2, write=True, incremental=True,
            delisting_filings=[merger], event_effective_cutoff=date(2026, 6, 30),
            retry_pending=False,
        )
        self.assertTrue(repository.company_rows[(target_company, 2026, 2)]["is_pending"])
        self.assertEqual(before_effective["resolved_delisting_companies"], 0)

        after_effective = pipeline.run_quarter(
            2026, 2, write=True, incremental=True,
            delisting_filings=[merger], event_effective_cutoff=date(2026, 7, 1),
        )
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertFalse(stored["is_pending"])
        self.assertEqual(stored["top_line"], Decimal("90"))
        self.assertEqual(
            stored["source_filing_id"],
            "delisting_previous_quarter:20260117000001",
        )
        self.assertEqual(after_effective["resolved_delisting_companies"], 1)

    def test_final_delisting_after_quarter_end_resolves_previous_pending_quarter(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.seed_company(
            target_company, top_line=None,
            operating_income=None, net_income=None, pending=True,
        )
        previous = fact(2026, 1, "9", company=target_company).with_changes(
            source_top_line_cumulative=Decimal("90"),
            source_operating_income_cumulative=Decimal("9"),
            source_net_income_cumulative=Decimal("9"),
        )
        repository.company_rows[(target_company, 2026, 1)] = previous.db_row(
            calculation_version=6,
        )
        final = DelistingFiling(
            target_corp, "20260712000001", date(2026, 7, 12),
            "상장폐지", "final",
        )
        dart = SimulatedDart(delistings=[final])
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository,
            kis=SimulatedKis(),
        )

        result = pipeline.run_quarter(
            2026, 2, write=True, incremental=True,
            discover_delistings=True,
        )

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertFalse(stored["is_pending"])
        self.assertEqual(stored["top_line"], Decimal("90"))
        self.assertEqual(
            stored["source_filing_id"],
            "delisting_previous_quarter:20260712000001",
        )
        self.assertEqual(result["resolved_delisting_companies"], 1)

    def test_next_quarter_delisting_decision_does_not_resolve_prior_quarter(self):
        repository = self.populated_repository()
        target_company = "kr:00000099"
        repository.seed_company(
            target_company, top_line=None,
            operating_income=None, net_income=None, pending=True,
        )
        decision = DelistingFiling(
            "00000099", "20260712000002", date(2026, 7, 12),
            "상장폐지결정", "decision",
        )
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(delistings=[decision]),
            repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_quarter(
            2026, 2, write=True, incremental=True,
            discover_delistings=True,
        )

        self.assertFalse(
            str(repository.company_rows[(target_company, 2026, 2)]["source_filing_id"])
            .startswith("delisting_previous_quarter:")
        )
        self.assertEqual(result["resolved_delisting_companies"], 0)

    def test_financial_fallback_uses_single_open_dart_before_kis(self):
        identity = CompanyIdentity(
            company_id="kr:00000099", company_name="과거금융사",
            stock_code="000099", corp_code="00000099",
            market_id="kr_largecap", rank=1, market_cap=Decimal("1"),
            reference_date=date(2015, 9, 30), industry_code="64110",
            entity_kind="financial",
        )
        incomplete = fact(2015, 3, "1", company=identity.company_id).with_changes(
            top_line=None, operating_income=None, net_income=None, is_pending=True,
        )
        dart = SimulatedDart()
        kis = SimulatedKis({
            identity.stock_code: {
                "top_line": Decimal("100"),
                "operating_income": Decimal("10"),
                "net_income": Decimal("8"),
            },
        })
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart,
            repository=SimulatedRepository(), kis=kis,
        )

        resolved, issue = pipeline._resolve_missing_financials(
            identity, incomplete, 2015, 3,
        )

        self.assertIsNone(issue)
        self.assertEqual(kis.calls, [])
        self.assertTrue(dart.single_calls)
        self.assertTrue(resolved.fully_complete)
        self.assertFalse(resolved.is_pending)

    def test_dart_daily_leaves_existing_pending_company_for_kis_phase(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.seed_company(
            target_company, top_line=None, operating_income=None, net_income=None, pending=True,
        )
        dart = SimulatedDart()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(dart.financial_calls, [])
        self.assertEqual(dart.single_calls, [])
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertIsNone(stored["operating_income"])
        self.assertTrue(stored["is_pending"])

    def test_backfill_can_complete_loss_company_with_zero_top_line(self):
        class NoRevenueDart(SimulatedDart):
            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return [
                    item for item in complete(corp_code, current="-10", cumulative="-10", scope=scope)
                    if item["account_nm"] != "매출액"
                ]

        identity = member("kr:00000099", 99)
        incomplete = fact(2020, 4, "-10", company=identity.company_id).with_changes(
            top_line=None, is_pending=True,
        )
        resolved, issue = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=NoRevenueDart(),
            repository=SimulatedRepository(), kis=SimulatedKis(),
        )._resolve_missing_financials(
            identity, incomplete, 2020, 4,
            allow_backfill_zero_top_line=True,
        )

        self.assertIsNone(issue)
        self.assertEqual(resolved.top_line, Decimal("0"))
        self.assertFalse(resolved.is_pending)
        self.assertTrue(resolved.source_filing_id.startswith("zero_top_line:"))

    def test_daily_keeps_missing_top_line_null_for_admin_review(self):
        class NoRevenueDart(SimulatedDart):
            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return [
                    item for item in complete(corp_code, current="-10", cumulative="-10", scope=scope)
                    if item["account_nm"] != "매출액"
                ]

        target_company = "kr:00000099"
        repository = self.populated_repository()
        repository.seed_company(
            target_company, top_line=None,
            operating_income=Decimal("-10"), net_income=Decimal("-10"),
            pending=True,
        )
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=NoRevenueDart(),
            repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(stored["top_line"])
        self.assertTrue(stored["is_pending"])

    def test_profitable_nonfinancial_without_recognized_revenue_remains_pending(self):
        class NoRevenueDart(SimulatedDart):
            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return [
                    item for item in complete(corp_code, current="10", cumulative="10", scope=scope)
                    if item["account_nm"] != "매출액"
                ]

        identity = member("kr:00000099", 99)
        incomplete = fact(2021, 2, "10", company=identity.company_id).with_changes(
            top_line=None, is_pending=True,
        )
        resolved, issue = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=NoRevenueDart(),
            repository=SimulatedRepository(), kis=SimulatedKis(),
        )._resolve_missing_financials(identity, incomplete, 2021, 2)

        self.assertIsNone(issue)
        self.assertIsNone(resolved.top_line)
        self.assertTrue(resolved.is_pending)

    def test_backfill_replaces_existing_top_line_with_provider_result(self):
        target_company = "kr:00000099"
        target_corp = "00000099"

        class MissingTopLineDart(SimulatedDart):
            def multi_accounts(self, corp_codes, year, quarter):
                rows = super().multi_accounts(corp_codes, year, quarter)
                return [
                    item for item in rows
                    if not (item["corp_code"] == target_corp and item["account_nm"] == "매출액")
                ]

            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return []

        repository = self.populated_repository()
        dart = MissingTopLineDart()
        kis = SimulatedKis({"000099": Decimal("250")})
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        result = pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(kis.calls, [("000099", 2026, 2)])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(repository.company_rows[(target_company, 2026, 2)]["top_line"], Decimal("250"))
        self.assertFalse(repository.company_rows[(target_company, 2026, 2)]["is_pending"])

    def test_backfill_overwrites_manual_current_row(self):
        target_company = "kr:00000099"
        repository = self.populated_repository()
        repository.seed_company(target_company, source="manual")
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_quarter(2026, 2, write=True)

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(stored["source"], "open_dart")
        self.assertEqual(stored["top_line"], Decimal("20"))
        self.assertTrue(repository.company_period_calls)
        self.assertTrue(
            all((2026, 2) not in periods for _companies, periods in repository.company_period_calls)
        )

    def test_backfill_reuses_saved_previous_cumulative(self):
        repository = self.populated_repository()
        for market_rows in repository.universes.values():
            for row_data in market_rows:
                previous = extract_company_fact(
                    row_data["corp_code"], row_data["company_id"], 2026, 1,
                    complete(row_data["corp_code"], current="100", cumulative="100"),
                )
                repository.company_rows[(row_data["company_id"], 2026, 1)] = previous.db_row(
                    calculation_version=6,
                )
        dart = SimulatedDart()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_quarter(2026, 2, write=True, trust_previous_backfill=True)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            repository.company_rows[("kr:00000099", 2026, 2)]["operating_income"],
            Decimal("20"),
        )
        self.assertEqual(dart.financial_calls, [
            (tuple(f"{rank:08d}" for rank in range(1, 101)) + tuple(f"{1000 + rank:08d}" for rank in range(1, 101)), 2026, 2),
        ])

    def test_non_q4_backfill_does_not_fetch_previous_dart_rows(self):
        repository = self.populated_repository()
        dart = SimulatedDart()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=SimulatedKis(),
        )

        pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual([call[2] for call in dart.financial_calls], [2])

    def test_backfill_provider_failure_stops_before_replacement(self):
        target_company = "kr:00000099"
        target_corp = "00000099"

        class MissingTopLineDart(SimulatedDart):
            def multi_accounts(self, corp_codes, year, quarter):
                rows = super().multi_accounts(corp_codes, year, quarter)
                return [
                    item for item in rows
                    if not (item["corp_code"] == target_corp and item["account_nm"] == "매출액")
                ]

            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return []

        repository = self.populated_repository()
        kis = FailingKis()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=MissingTopLineDart(), repository=repository, kis=kis,
        )

        with self.assertRaisesRegex(ProviderError, "KIS top-line request failed"):
            pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(kis.calls, [("000099", 2026, 2)])
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("100"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(repository.states["2026Q2"]["status"], "failed")

    def test_backfill_fx_failure_stops_before_replacement(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.fx_rates.clear()
        fx = FailingFx()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=UsdDart(target_corp), repository=repository,
            kis=SimulatedKis(), fx=fx,
        )

        with self.assertRaisesRegex(ProviderError, r"ECOS USD/KRW timed out \(ConnectTimeout\)"):
            pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(fx.request_count, 1)
        self.assertTrue(pipeline.dart.financial_calls)
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("100"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(repository.states["2026Q2"]["status"], "failed")

    def test_quarter_fx_snapshot_is_stored_once_and_reused_for_usd_company(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.fx_rates.clear()
        first_fx = FixedFx()
        first_pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=UsdDart(target_corp), repository=repository,
            kis=SimulatedKis(), fx=first_fx,
        )

        first_pipeline.run_quarter(2026, 2, write=True)

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(first_fx.request_count, 1)
        self.assertEqual(stored["top_line"], Decimal("26000"))
        self.assertEqual(repository.fx_rates[(2026, 2, "USD", "KRW")]["target_date"], date(2026, 6, 30))

        second_fx = FixedFx(Decimal("9999"))
        second_pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=UsdDart(target_corp), repository=repository,
            kis=SimulatedKis(), fx=second_fx,
        )
        second_pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(second_fx.request_count, 0)
        self.assertEqual(repository.company_rows[(target_company, 2026, 2)]["top_line"], Decimal("26000"))

    def test_quarter_fx_snapshot_converts_jpy_per_one_yen(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.fx_rates.clear()
        fx = FixedFx(Decimal("10"))
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=JpyDart(target_corp), repository=repository,
            kis=SimulatedKis(), fx=fx,
        )

        pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(fx.request_count, 1)
        self.assertEqual(repository.company_rows[(target_company, 2026, 2)]["top_line"], Decimal("200"))
        self.assertIn((2026, 2, "JPY", "KRW"), repository.fx_rates)

    def test_krw_only_quarter_does_not_query_ecos(self):
        repository = self.populated_repository()
        repository.fx_rates.clear()
        fx = FixedFx()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=repository,
            kis=SimulatedKis(), fx=fx,
        )

        pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(fx.request_count, 0)

    def test_backfill_application_deadline_stops_before_replacement(self):
        target_company = "kr:00000099"

        class DeadlineDart(SimulatedDart):
            def multi_accounts(self, _corp_codes, _year, _quarter):
                raise ExecutionDeadlineExceeded(
                    "earnings process exceeded 600-second application deadline"
                )

        repository = self.populated_repository()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=DeadlineDart(), repository=repository,
            kis=SimulatedKis(),
        )

        with self.assertRaises(ExecutionDeadlineExceeded):
            pipeline.run_quarter(2026, 2, write=True)

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("100"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(repository.states["2026Q2"]["status"], "failed")

    def test_daily_fx_failure_preserves_stored_financials(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        receipt = PeriodicFiling(
            target_corp, "20260902000009", date(2026, 9, 2), "반기보고서 (2026.06)",
        )
        repository = self.populated_repository()
        repository.fx_rates.clear()
        fx = FailingFx()
        dart = UsdDart(target_corp, [receipt])
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository,
            kis=SimulatedKis(), fx=fx,
        )

        with self.assertRaisesRegex(ProviderError, r"ECOS USD/KRW timed out \(ConnectTimeout\)"):
            pipeline.run_daily(write=True, today=date(2026, 9, 2))

        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("100"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(fx.request_count, 1)
        self.assertTrue(dart.financial_calls)

    def test_automatic_converts_supported_foreign_currencies_lazily(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        receipt = PeriodicFiling(
            target_corp, "20260902000009", date(2026, 9, 2), "반기보고서 (2026.06)",
        )
        for currency in ("USD", "JPY", "EUR", "CNY"):
            with self.subTest(currency=currency):
                repository = self.populated_repository()
                repository.fx_rates.clear()
                fx = FixedFx(Decimal("10"))
                dart = ForeignCurrencyDart(target_corp, currency, [receipt])
                pipeline = KoreaEarningsV2AutomaticPipeline(
                    krx=SimulatedKrx(), dart=dart, repository=repository,
                    kis=SimulatedKis(), fx=fx,
                )

                pipeline.run_daily(write=True, today=date(2026, 9, 2))

                self.assertEqual(fx.request_count, 1)
                self.assertEqual(
                    repository.company_rows[(target_company, 2026, 2)]["top_line"],
                    Decimal("200"),
                )
                self.assertIn((2026, 2, currency, "KRW"), repository.fx_rates)

    def test_automatic_krw_only_collection_does_not_query_ecos(self):
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.fx_rates.clear()
        fx = FixedFx()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(),
            dart=SimulatedDart([
                PeriodicFiling(
                    target_corp, "20260902000009", date(2026, 9, 2),
                    "반기보고서 (2026.06)",
                ),
            ]),
            repository=repository,
            kis=SimulatedKis(),
            fx=fx,
        )

        pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(fx.request_count, 0)

    def test_pending_kis_failure_is_isolated_from_other_companies(self):
        class MissingSingleDart(SimulatedDart):
            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return []

        target_company = "kr:00000099"
        repository = self.populated_repository()
        repository.seed_company(target_company, top_line=None, pending=True)
        repository.company_profiles[target_company] = {
            "industry_code": "64110", "entity_kind": "financial",
        }
        repository.stale_pending_rows = [{
            "market_id": "kr_largecap", "market_year": 2026, "market_quarter": 2,
            "company_id": target_company, "company_name": "failed",
            "stock_code": "000099",
        }]
        kis = FailingKis()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=MissingSingleDart(), repository=repository, kis=kis,
        )

        result = pipeline.run_kis_pending(write=True, today=date(2026, 9, 2))

        self.assertEqual(pipeline.dart.single_calls, [])
        self.assertEqual(kis.history_calls, ["000099"])
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(repository.company_rows[(target_company, 2026, 2)]["is_pending"])

    def test_kis_phase_uses_one_call_to_fill_multiple_pending_periods_without_overwrite(self):
        target_company = "kr:00000099"
        ticker = "000099"
        repository = self.populated_repository()
        q1 = FinancialFact(
            target_company, 2026, 1, date(2026, 3, 31),
            None, Decimal("999"), Decimal("8"), "KRW", "CFS",
            "dart:q1", date(2026, 5, 15),
            source_top_line_cumulative=None,
            source_operating_income_cumulative=Decimal("10"),
            source_net_income_cumulative=Decimal("8"),
            is_pending=True,
        )
        q2 = FinancialFact(
            target_company, 2026, 2, date(2026, 6, 30),
            None, None, None, "KRW", "CFS",
            "dart:q2", date(2026, 8, 14), is_pending=True,
        )
        old = FinancialFact(
            target_company, 2025, 4, date(2025, 12, 31),
            None, None, None, "KRW", "CFS",
            "dart:old", date(2026, 3, 31), is_pending=True,
        )
        repository.company_rows[(target_company, 2025, 4)] = old.db_row(calculation_version=6)
        repository.company_rows[(target_company, 2026, 1)] = q1.db_row(calculation_version=6)
        repository.company_rows[(target_company, 2026, 2)] = q2.db_row(calculation_version=6)
        repository.stale_pending_rows = [
            {"market_id": "kr_largecap", "market_year": 2026, "market_quarter": quarter,
             "company_id": target_company, "company_name": "target", "stock_code": ticker}
            for quarter in (1, 2)
        ] + [{
            "market_id": "kr_largecap", "market_year": 2025, "market_quarter": 4,
            "company_id": target_company, "company_name": "target", "stock_code": ticker,
        }]
        history = {
            (2025, 4): {
                "top_line": Decimal("400"), "operating_income": Decimal("40"),
                "net_income": Decimal("32"),
            },
            (2026, 1): {
                "top_line": Decimal("100"), "operating_income": Decimal("10"),
                "net_income": Decimal("8"),
            },
            (2026, 2): {
                "top_line": Decimal("250"), "operating_income": Decimal("30"),
                "net_income": Decimal("20"),
            },
        }
        kis = SimulatedKis({ticker: history})
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=repository, kis=kis,
        )
        recalculated = []
        pipeline.recalculate_quarter = lambda year, quarter, **_kwargs: recalculated.append((year, quarter)) or {"status": "ready"}

        result = pipeline.run_kis_pending(write=True, today=date(2026, 9, 2))
        second = pipeline.run_kis_pending(write=True, today=date(2026, 9, 2))

        stored_q1 = repository.company_rows[(target_company, 2026, 1)]
        stored_q2 = repository.company_rows[(target_company, 2026, 2)]
        stored_old = repository.company_rows[(target_company, 2025, 4)]
        self.assertEqual(kis.history_calls, [ticker])
        self.assertEqual(result["changed_company_periods"], 3)
        self.assertEqual(result["filled_reference_cumulative_periods"], 1)
        self.assertEqual(result["comparison_periods"], ["2026Q1", "2026Q2"])
        self.assertEqual(result["ignored_older_pending_periods"], 1)
        self.assertEqual(second["skipped_same_day_companies"], 1)
        self.assertTrue(repository.company_period_calls)
        self.assertEqual(
            repository.company_period_calls[0][1],
            ((2025, 4), (2026, 1), (2026, 2)),
        )
        self.assertEqual(stored_q1["top_line"], Decimal("100"))
        self.assertEqual(stored_q1["operating_income"], Decimal("999"))
        self.assertEqual(stored_q2["top_line"], Decimal("150"))
        self.assertEqual(stored_q2["operating_income"], Decimal("20"))
        self.assertEqual(stored_q2["net_income"], Decimal("12"))
        self.assertIsNone(stored_old["top_line"])
        self.assertEqual(stored_old["source_top_line_cumulative"], Decimal("400"))
        self.assertEqual(recalculated, [(2026, 1), (2026, 2)])

    def test_new_filing_runs_dart_fallback_but_defers_kis(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        receipt = PeriodicFiling(
            target_corp, "20260902000011", date(2026, 9, 2), "반기보고서 (2026.06)",
        )

        class MissingDart(SimulatedDart):
            def multi_accounts(self, corp_codes, year, quarter):
                rows = super().multi_accounts(corp_codes, year, quarter)
                return [row for row in rows if row["corp_code"] != target_corp]

            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return []

        repository = self.populated_repository()
        repository.company_rows.pop((target_company, 2026, 2))
        dart = MissingDart([receipt])
        kis = SimulatedKis({"000099": {}})
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(result["refreshed_companies"], 1)
        self.assertEqual(len(dart.financial_calls), 1)
        self.assertTrue(dart.single_calls)
        self.assertEqual(kis.history_calls, [])
        self.assertEqual(kis.calls, [])
        self.assertTrue(repository.company_rows[(target_company, 2026, 2)]["is_pending"])

    def test_manual_company_is_excluded_even_when_a_new_receipt_exists(self):
        target_company = "kr:00000098"
        target_corp = "00000098"
        repository = self.populated_repository()
        repository.seed_company(target_company, source="manual")
        dart = SimulatedDart([
            PeriodicFiling(target_corp, "20260902000002", date(2026, 9, 2), "반기보고서 (2026.06)"),
        ])
        kis = SimulatedKis()
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(result["filing_discovery"]["new_receipts"], 1)
        self.assertEqual(result["refreshed_companies"], 0)
        self.assertEqual(dart.financial_calls, [])
        self.assertEqual(kis.calls, [])
        self.assertEqual(repository.company_rows[(target_company, 2026, 2)]["source"], "manual")

    def test_processing_failure_does_not_advance_daily_checkpoint(self):
        repository = SimulatedRepository()
        original_cursor = {
            "last_checked_date": "2026-09-01",
            "boundary_receipt_ids": ["20260901000001"],
        }
        repository.states["daily_filings"] = {"status": "ready", "cursor": original_cursor}
        dart = SimulatedDart([
            PeriodicFiling("00000001", "20260902000003", date(2026, 9, 2), "반기보고서 (2026.06)"),
        ])
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository,
        )
        pipeline.run_quarter = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated failure"))

        with self.assertRaisesRegex(RuntimeError, "simulated failure"):
            pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(repository.states["daily_filings"]["cursor"], original_cursor)
        self.assertEqual(repository.states["daily_filings"]["status"], "failed")
        self.assertFalse(any(
            operation == "daily_filings" and cursor.get("last_checked_date") == "2026-09-02"
            for operation, _status, cursor, _error in repository.saved_states
        ))


class QuarterlyExtractionTests(unittest.TestCase):
    def test_numeric_and_decimal_zero_are_preserved(self):
        current = [
            row("1", "매출액", current=0, cumulative=0),
            row("1", "영업이익", current=Decimal("0"), cumulative=Decimal("0")),
            row("1", "당기순이익", current="0", cumulative="0"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current)
        self.assertEqual(value.top_line, Decimal("0"))
        self.assertEqual(value.operating_income, Decimal("0"))
        self.assertEqual(value.net_income, Decimal("0"))
        self.assertTrue(value.fully_complete)

    def test_q1_uses_current_cumulative_value(self):
        value = extract_company_fact("00000001", "kr:1", 2026, 1, complete("00000001", current="40", cumulative="40"), [])
        self.assertEqual(value.operating_income, Decimal("40"))

    def test_q1_uses_current_amount_for_both_values_when_cumulative_is_blank(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2016, 1,
            complete("00000001", current="40", cumulative=""),
            [],
        )

        self.assertEqual(value.top_line, Decimal("40"))
        self.assertEqual(value.operating_income, Decimal("40"))
        self.assertEqual(value.net_income, Decimal("40"))
        self.assertEqual(value.source_top_line_cumulative, Decimal("40"))
        self.assertEqual(value.source_operating_income_cumulative, Decimal("40"))
        self.assertEqual(value.source_net_income_cumulative, Decimal("40"))

    def test_q2_preserves_reported_current_even_when_it_differs_from_cumulative_change(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 2,
            complete("00000001", current="55", cumulative="100"),
            complete("00000001", current="40", cumulative="40"),
        )
        self.assertEqual(value.operating_income, Decimal("55"))

    def test_q2_uses_saved_source_cumulative_without_previous_api_rows(self):
        previous = extract_company_fact(
            "00000001", "kr:1", 2026, 1,
            complete("00000001", current="40", cumulative="40"),
        )
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 2,
            complete("00000001", current="60", cumulative="100"),
            previous_fact=previous,
        )

        self.assertEqual(value.operating_income, Decimal("60"))
        self.assertEqual(value.source_operating_income_cumulative, Decimal("100"))

    def test_collector_skips_previous_bulk_request_when_saved_cumulative_exists(self):
        identity = member("kr:00000001", 1)
        previous = extract_company_fact(
            identity.corp_code, identity.company_id, 2026, 1,
            complete(identity.corp_code, current="100", cumulative="100"),
        )
        dart = SimulatedDart()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=SimulatedRepository(),
        )

        facts, issues = pipeline.collect_financials(
            [identity], 2026, 2, {identity.company_id: previous},
        )

        self.assertEqual(dart.financial_calls, [((identity.corp_code,), 2026, 2)])
        self.assertEqual(facts[identity.company_id].operating_income, Decimal("20"))
        self.assertEqual(issues, [])

    def test_q3_preserves_reported_current_even_when_it_differs_from_cumulative_change(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 3,
            complete("00000001", current="65", cumulative="170"),
            complete("00000001", current="60", cumulative="100"),
        )
        self.assertEqual(value.net_income, Decimal("65"))

    def test_q3_without_previous_cumulative_uses_reported_current_period(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2022, 3,
            complete("00000001", current="70", cumulative="170"),
            [],
        )
        self.assertEqual(value.top_line, Decimal("70"))
        self.assertEqual(value.operating_income, Decimal("70"))
        self.assertEqual(value.net_income, Decimal("70"))

    def test_q4_uses_annual_minus_q3_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 4,
            complete("00000001", current="250", cumulative=""),
            complete("00000001", current="70", cumulative="170"),
        )
        self.assertEqual(value.top_line, Decimal("80"))
        self.assertEqual(value.source_top_line_cumulative, Decimal("250"))

    def test_q4_without_q3_cumulative_uses_annual_quarter_average(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2021, 4,
            complete("00000001", current="240", cumulative=""),
            [],
        )
        self.assertEqual(value.top_line, Decimal("60"))
        self.assertEqual(value.operating_income, Decimal("60"))
        self.assertEqual(value.net_income, Decimal("60"))
        self.assertTrue(value.source_filing_id.startswith("annual_without_q3_average:"))

    def test_q4_does_not_subtract_fetched_q3_in_another_currency(self):
        current = [
            {**item, "currency": "USD"}
            for item in complete("00000001", current="400", cumulative="")
        ]
        previous = complete("00000001", current="100", cumulative="300")

        value = extract_company_fact(
            "00000001", "kr:1", 2023, 4, current, previous,
        )

        self.assertEqual(value.operating_income, Decimal("100"))
        self.assertEqual(value.source_currency, "USD")
        self.assertTrue(value.source_filing_id.startswith("annual_without_q3_average:"))

    def test_automatic_q4_currency_change_remains_missing(self):
        current = [
            {**item, "currency": "USD"}
            for item in complete("00000001", current="400", cumulative="")
        ]
        previous = complete("00000001", current="100", cumulative="300")

        value = extract_company_fact(
            "00000001", "kr:1", 2023, 4, current, previous,
            allow_annual_average=False,
        )

        self.assertIsNone(value.operating_income)
        self.assertEqual(value.source_currency, "USD")

    def test_automatic_q4_without_q3_cumulative_remains_missing(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2021, 4,
            complete("00000001", current="240", cumulative=""),
            [], allow_annual_average=False,
        )
        self.assertIsNone(value.top_line)
        self.assertIsNone(value.operating_income)
        self.assertIsNone(value.net_income)
        self.assertFalse(value.source_filing_id.startswith("annual_without_q3_average:"))

    def test_automatic_q4_uses_db_q3_without_fetching_previous_dart(self):
        identity = member("kr:00000001", 1, quarter=4)

        class AnnualDart(SimulatedDart):
            def multi_accounts(self, corp_codes, year, quarter):
                codes = tuple(corp_codes)
                self.financial_calls.append((codes, year, quarter))
                return [
                    item for corp in codes
                    for item in complete(corp, current="400", cumulative="")
                ]

        previous = fact(2026, 3, "10", company=identity.company_id).with_changes(
            source_top_line_cumulative=Decimal("300"),
            source_operating_income_cumulative=Decimal("300"),
            source_net_income_cumulative=Decimal("300"),
        )
        dart = AnnualDart()
        values, issues = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=SimulatedRepository(),
        ).collect_financials(
            [identity], 2026, 4, {identity.company_id: previous},
        )
        self.assertEqual(values[identity.company_id].top_line, Decimal("100"))
        self.assertEqual(issues, [])
        self.assertEqual([call[2] for call in dart.financial_calls], [4])

    def test_automatic_q4_without_db_q3_does_not_fetch_or_average(self):
        identity = member("kr:00000001", 1, quarter=4)

        class AnnualDart(SimulatedDart):
            def multi_accounts(self, corp_codes, year, quarter):
                codes = tuple(corp_codes)
                self.financial_calls.append((codes, year, quarter))
                return [
                    item for corp in codes
                    for item in complete(corp, current="400", cumulative="")
                ]

            def single_accounts(self, corp_code, year, quarter, scope):
                self.single_calls.append((corp_code, year, quarter, scope))
                return complete(corp_code, current="400", cumulative="", scope=scope)

        dart = AnnualDart()
        values, _issues = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=dart, repository=SimulatedRepository(),
        ).collect_financials([identity], 2026, 4)
        self.assertIsNone(values[identity.company_id].top_line)
        self.assertTrue(values[identity.company_id].is_pending)
        self.assertEqual([call[2] for call in dart.financial_calls], [4])
        self.assertEqual({call[2] for call in dart.single_calls}, {4})

    def test_backfill_q4_fetches_q3_when_db_cumulative_is_missing(self):
        identity = member("kr:00000001", 1, quarter=4)

        class AnnualDart(SimulatedDart):
            def multi_accounts(self, corp_codes, year, quarter):
                codes = tuple(corp_codes)
                self.financial_calls.append((codes, year, quarter))
                current, cumulative = (
                    ("400", "") if quarter == 4 else ("100", "300")
                )
                return [
                    item for corp in codes
                    for item in complete(corp, current=current, cumulative=cumulative)
                ]

        dart = AnnualDart()
        values, issues = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=SimulatedRepository(),
        ).collect_financials([identity], 2026, 4)
        self.assertEqual(values[identity.company_id].top_line, Decimal("100"))
        self.assertEqual(issues, [])
        self.assertEqual([call[2] for call in dart.financial_calls], [4, 3])

    def test_cfs_is_preferred_even_when_ofs_is_more_complete(self):
        current = [
            row("1", "매출액", current="100", cumulative="100", scope="CFS"),
            row("1", "영업이익", current="20", cumulative="20", scope="CFS"),
            *complete("1", current="50", cumulative="50", scope="OFS"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertEqual(value.consolidation_scope, "CFS")
        self.assertIsNone(value.net_income)

    def test_scope_change_does_not_block_cumulative_subtraction(self):
        previous = extract_company_fact(
            "1", "kr:1", 2026, 1,
            complete("1", current="40", cumulative="40", scope="OFS"),
        )
        current = extract_company_fact(
            "1", "kr:1", 2026, 2,
            complete("1", current="60", cumulative="100", scope="CFS"),
            previous_fact=previous,
        )
        self.assertEqual(current.consolidation_scope, "CFS")
        self.assertEqual(current.top_line, Decimal("60"))

    def test_income_leaves_are_not_summed_as_top_line(self):
        current = [
            row("1", "이자수익", current="70", cumulative="70"),
            row("1", "수수료수익", current="30", cumulative="30"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertIsNone(value.top_line)
        self.assertEqual(value.operating_income, Decimal("20"))

    def test_standard_revenue_id_does_not_override_an_unrelated_label(self):
        current = [
            row("1", "보험수익", current="100", cumulative="100", account_id="ifrs-full_Revenue"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertIsNone(value.top_line)

    def test_exact_top_line_names_and_display_prefixes_are_allowed(self):
        for account_name in ("매출액", "매출", "수익", "영업수익", " Ⅰ. 매출액 "):
            with self.subTest(account_name=account_name):
                current = [
                    row("1", account_name, current="100", cumulative="100"),
                    row("1", "영업이익", current="20", cumulative="20"),
                    row("1", "당기순이익", current="10", cumulative="10"),
                ]
                value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
                self.assertEqual(value.top_line, Decimal("100"))

    def test_financial_components_and_profit_labels_are_not_top_line(self):
        for account_name in ("금융수익", "보험수익", "이자수익", "수수료수익", "순영업수익", "순영업이익"):
            with self.subTest(account_name=account_name):
                current = [
                    row("1", account_name, current="100", cumulative="100", account_id="ifrs-full_Revenue"),
                    row("1", "영업이익", current="20", cumulative="20"),
                    row("1", "당기순이익", current="10", cumulative="10"),
                ]
                value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
                self.assertIsNone(value.top_line)

    def test_semantic_suffix_does_not_match_an_exact_top_line(self):
        current = [
            row("1", "매출및기타수익", current="100", cumulative="100"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertIsNone(value.top_line)


class KisFallbackTests(unittest.TestCase):
    @staticmethod
    def _incomplete(company_id="kr:00000001"):
        return fact(2026, 3, "10", company=company_id).with_changes(
            top_line=None, is_pending=True,
        )

    @staticmethod
    def _kis_values(current, previous):
        return {
            "top_line": {"current": current, "previous": previous},
            "operating_income": {"current": None, "previous": None},
            "net_income": {"current": None, "previous": None},
        }

    def test_kis_converts_cumulative_hundred_million_krw_to_standalone_won(self):
        class Response:
            ok = True

            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "rt_cd": "0",
                    "output": [
                        {"stac_yymm": "202603", "sale_account": "100", "bsop_prti": "10", "thtr_ntin": "8"},
                        {"stac_yymm": "202606", "sale_account": "250", "bsop_prti": "30", "thtr_ntin": "20"},
                    ],
                }

        class Session:
            def __init__(self):
                self.kwargs = None

            def get(self, *_args, **kwargs):
                self.kwargs = kwargs
                return Response()

        session = Session()
        client = KisClient("key", "secret", cached_token=lambda: "token", session=session, interval=0)
        self.assertEqual(client.quarter_financials("005930", 2026, 2), {
            "top_line": Decimal("15000000000"),
            "operating_income": Decimal("2000000000"),
            "net_income": Decimal("1200000000"),
        })
        self.assertEqual(session.kwargs["params"]["FID_DIV_CLS_CODE"], "1")

    def test_equal_numeric_cumulative_values_produce_zero_standalone(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "rt_cd": "0",
                    "output": [
                        {"stac_yymm": "202603", "sale_account": 100, "bsop_prti": 10, "thtr_ntin": 8},
                        {"stac_yymm": "202606", "sale_account": 100, "bsop_prti": 10, "thtr_ntin": 8},
                    ],
                }

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = KisClient("key", "secret", cached_token=lambda: "token", session=Session(), interval=0)
        self.assertEqual(client.quarter_financials("005930", 2026, 2), {
            "top_line": Decimal("0"),
            "operating_income": Decimal("0"),
            "net_income": Decimal("0"),
        })

    def test_kis_exposes_current_and_previous_cumulative_values(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "rt_cd": "0",
                    "output": [
                        {"stac_yymm": "202603", "sale_account": "100", "bsop_prti": "10", "thtr_ntin": "8"},
                        {"stac_yymm": "202606", "sale_account": "250", "bsop_prti": "30", "thtr_ntin": "20"},
                    ],
                }

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = KisClient("key", "secret", cached_token=lambda: "token", session=Session(), interval=0)
        values = client.quarter_cumulative_financials("005930", 2026, 2)
        self.assertEqual(values["top_line"], {
            "current": Decimal("25000000000"),
            "previous": Decimal("10000000000"),
        })

    def test_kis_history_preserves_every_returned_quarter_from_one_response(self):
        class Response:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "rt_cd": "0",
                    "output": [
                        {"stac_yymm": "201903", "sale_account": "1", "bsop_prti": "2", "thtr_ntin": "3"},
                        {"stac_yymm": "202606", "sale_account": "4", "bsop_prti": "5", "thtr_ntin": "6"},
                    ],
                }

        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                return Response()

        client = KisClient("key", "secret", cached_token=lambda: "token", session=Session(), interval=0)
        history = client.financial_history("005930")

        self.assertEqual(set(history), {(2019, 1), (2026, 2)})
        self.assertEqual(history[(2019, 1)]["top_line"], Decimal("100000000"))
        self.assertEqual(client.request_count, 1)

    def test_backfill_kis_prefers_kis_previous_cumulative(self):
        identity = member("kr:00000001", 1, quarter=3)
        previous = fact(2026, 2, "10", company=identity.company_id).with_changes(
            source_top_line_cumulative=Decimal("150"),
        )
        kis = SimulatedKis({identity.stock_code: self._kis_values(Decimal("300"), Decimal("180"))})
        resolved, issue = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=SimulatedRepository(), kis=kis,
        )._try_kis_missing_financials(
            identity, self._incomplete(identity.company_id), 2026, 3,
            stage="test", previous_fact=previous,
        )
        self.assertIsNone(issue)
        self.assertEqual(resolved.top_line, Decimal("120"))
        self.assertEqual(resolved.source_top_line_cumulative, Decimal("300"))

    def test_backfill_kis_uses_db_previous_then_quarter_average(self):
        identity = member("kr:00000001", 1, quarter=3)
        previous = fact(2026, 2, "10", company=identity.company_id).with_changes(
            source_top_line_cumulative=Decimal("150"),
        )
        values = self._kis_values(Decimal("300"), None)
        with_db, _ = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=SimulatedRepository(),
            kis=SimulatedKis({identity.stock_code: values}),
        )._try_kis_missing_financials(
            identity, self._incomplete(identity.company_id), 2026, 3,
            stage="test", previous_fact=previous,
        )
        without_db, _ = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=SimulatedRepository(),
            kis=SimulatedKis({identity.stock_code: values}),
        )._try_kis_missing_financials(
            identity, self._incomplete(identity.company_id), 2026, 3,
            stage="test",
        )
        self.assertEqual(with_db.top_line, Decimal("150"))
        self.assertEqual(without_db.top_line, Decimal("100"))

    def test_automatic_kis_uses_only_db_previous_cumulative(self):
        identity = member("kr:00000001", 1, quarter=3)
        previous = fact(2026, 2, "10", company=identity.company_id).with_changes(
            source_top_line_cumulative=Decimal("150"),
        )
        values = self._kis_values(Decimal("300"), Decimal("180"))
        pipeline = KoreaEarningsV2AutomaticPipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=SimulatedRepository(),
            kis=SimulatedKis({identity.stock_code: values}),
        )
        resolved, _ = pipeline._try_kis_missing_financials(
            identity, self._incomplete(identity.company_id), 2026, 3,
            stage="test", previous_fact=previous,
        )
        unresolved, _ = pipeline._try_kis_missing_financials(
            identity, self._incomplete(identity.company_id), 2026, 3,
            stage="test",
        )
        self.assertEqual(resolved.top_line, Decimal("150"))
        self.assertIsNone(unresolved.top_line)
        self.assertTrue(unresolved.is_pending)

    def test_kis_missing_current_cumulative_stays_pending(self):
        identity = member("kr:00000001", 1, quarter=3)
        resolved, issue = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=SimulatedRepository(),
            kis=SimulatedKis({identity.stock_code: self._kis_values(None, Decimal("180"))}),
        )._try_kis_missing_financials(
            identity, self._incomplete(identity.company_id), 2026, 3,
            stage="test",
        )
        self.assertIsNone(issue)
        self.assertIsNone(resolved.top_line)
        self.assertTrue(resolved.is_pending)


class GrowthAndAggregationTests(unittest.TestCase):
    def test_latest_completed_quarter_uses_previous_calendar_quarter(self):
        self.assertEqual(latest_completed_quarter(date(2026, 9, 2)), (2026, 2))
        self.assertEqual(latest_completed_quarter(date(2026, 1, 5)), (2025, 4))

    def test_yoy_requires_prior_year_and_turns_are_states(self):
        rows = calculate_financial_series([fact(2025, 1, "-10"), fact(2026, 1, "20")])
        self.assertIsNone(rows[-1].operating_income_yoy_pct)
        self.assertEqual(rows[-1].operating_income_yoy_state, "black_turn")

    def test_historical_seasonal_qoq_uses_full_leave_one_out_sample(self):
        rows = [
            fact(2020, 4, "100"), fact(2021, 1, "110"), fact(2021, 2, "100"), fact(2021, 3, "100"), fact(2021, 4, "100"),
            fact(2022, 1, "120"), fact(2022, 2, "100"), fact(2022, 3, "100"), fact(2022, 4, "100"),
            fact(2023, 1, "130"), fact(2023, 2, "100"), fact(2023, 3, "100"), fact(2023, 4, "100"), fact(2024, 1, "140"),
        ]
        calculated = calculate_financial_series(rows)
        self.assertEqual(calculated[1].operating_income_qoq_state, "normal")
        self.assertEqual(calculated[1].operating_income_qoq_sa_pct, Decimal("-20"))
        self.assertEqual(calculated[9].operating_income_qoq_state, "normal")
        self.assertEqual(calculated[-1].operating_income_qoq_state, "normal")

    def test_historical_seasonal_qoq_starts_at_2019(self):
        rows = [
            fact(2018, 4, "100"), fact(2019, 1, "110"),
            fact(2019, 2, "100"), fact(2019, 3, "100"), fact(2019, 4, "100"),
        ]
        calculated = calculate_financial_series(rows)
        self.assertEqual(calculated[1].operating_income_qoq_state, "missing_prior")
        self.assertIsNone(calculated[1].operating_income_qoq_sa_pct)

    def test_incremental_point_uses_saved_window_without_recalculating_history(self):
        current = fact(2026, 2, "130")
        calculated, raw = calculate_financial_point(
            current,
            previous=fact(2026, 1, "100"),
            prior_year=fact(2025, 2, "110"),
            seasonal_samples={
                "operating_income": [Decimal("10"), Decimal("20"), Decimal("30")],
                "net_income": [Decimal("10"), Decimal("20"), Decimal("30")],
            },
        )
        self.assertEqual(calculated.operating_income_qoq_sa_pct, Decimal("10"))
        self.assertEqual(raw["operating_income"], Decimal("30"))

    def test_scope_change_does_not_block_yoy_or_qoq(self):
        current = fact(2026, 2, "130").with_changes(consolidation_scope="CFS")
        calculated, raw = calculate_financial_point(
            current,
            previous=fact(2026, 1, "100").with_changes(consolidation_scope="OFS"),
            prior_year=fact(2025, 2, "100").with_changes(consolidation_scope="OFS"),
            seasonal_samples={
                "operating_income": [Decimal("10"), Decimal("20"), Decimal("30")],
                "net_income": [Decimal("10"), Decimal("20"), Decimal("30")],
            },
        )
        self.assertEqual(calculated.operating_income_yoy_pct, Decimal("30.0"))
        self.assertEqual(calculated.operating_income_qoq_sa_pct, Decimal("10"))
        self.assertEqual(raw["operating_income"], Decimal("30"))

    def test_seasonal_window_replaces_same_year_and_keeps_only_ten_samples(self):
        years, values = update_seasonal_window(
            range(2015, 2025),
            [Decimal(year) for year in range(2015, 2025)],
            year=2025,
            value=Decimal("99"),
        )
        self.assertEqual(years, list(range(2016, 2026)))
        self.assertEqual(values[-1], Decimal("99"))
        years, values = update_seasonal_window(years, values, year=2025, value=Decimal("100"))
        self.assertEqual(len(years), 10)
        self.assertEqual(values[-1], Decimal("100"))

    def test_loss_to_loss_growth_uses_absolute_prior_denominator(self):
        self.assertEqual(conventional_growth(Decimal("-70"), Decimal("-100")), (Decimal("30.0"), "normal"))
        self.assertEqual(conventional_growth(Decimal("-130"), Decimal("-100")), (Decimal("-30.0"), "normal"))

    def test_turns_and_zero_denominator_are_not_numeric_growth(self):
        self.assertEqual(conventional_growth(Decimal("10"), Decimal("-10")), (None, "black_turn"))
        self.assertEqual(conventional_growth(Decimal("-10"), Decimal("10")), (None, "red_turn"))
        self.assertEqual(conventional_growth(Decimal("10"), Decimal("0")), (None, "from_zero"))
        self.assertEqual(conventional_growth(Decimal("0"), Decimal("-10")), (Decimal("100"), "normal"))
        self.assertEqual(conventional_growth(Decimal("0"), Decimal("10")), (Decimal("-100"), "normal"))

    def test_individual_margin_is_null_for_nonpositive_top_line(self):
        self.assertIsNone(profit_margin(Decimal("10"), Decimal("0")))
        self.assertIsNone(profit_margin(Decimal("10"), Decimal("-5")))
        self.assertEqual(profit_margin(Decimal("0"), Decimal("100")), Decimal("0"))

    def test_company_db_row_contains_v6_pending_state(self):
        stored = fact(2026, 2, "10").db_row(calculation_version=6)
        self.assertFalse(stored["is_pending"])

    def test_pending_company_contributes_each_available_current_metric(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("b", 2, year=2026, quarter=1)]
        current = {
            "a": fact(2026, 2, "12", company="a"),
            "b": fact(2026, 2, "999", company="b").with_changes(top_line=None, is_pending=True),
        }
        previous = {"a": fact(2026, 1, "10", company="a"), "b": fact(2026, 1, "20", company="b")}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.top_line_total, Decimal("320"))
        self.assertEqual(market.operating_income_total, Decimal("1011"))
        self.assertEqual(market.net_income_total, Decimal("1011"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_missing_baseline_uses_available_actuals_as_provisional(self):
        current_members = [member("a", 1), member("b", 2)]
        current = {"a": fact(2026, 2, "12", company="a")}
        market = aggregate_market("kr_largecap", 2026, 2, current_members, current, 2)
        self.assertEqual(market.operating_income_total, Decimal("12"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_incomplete_prior_placeholder_contributes_its_available_metrics(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("b", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a")}
        previous = {"b": fact(2026, 1, "20", company="b").with_changes(top_line=None, is_pending=True)}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.top_line_total, Decimal("120"))
        self.assertEqual(market.operating_income_total, Decimal("32"))
        self.assertEqual(market.net_income_total, Decimal("32"))
        self.assertEqual(market.completion_status, "provisional")

    def test_unconverted_pending_currency_uses_krw_placeholder(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("b", 2, year=2026, quarter=1)]
        current = {
            "a": fact(2026, 2, "12", company="a"),
            "b": fact(2026, 2, "999", company="b").with_changes(currency="EUR", is_pending=True),
        }
        previous = {"a": fact(2026, 1, "10", company="a"), "b": fact(2026, 1, "20", company="b")}

        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )

        self.assertEqual(market.operating_income_total, Decimal("32"))
        self.assertEqual(market.completion_status, "provisional")

    def test_market_db_row_maps_domain_status_to_database_lifecycle(self):
        current_members = [member("a", 1)]
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members,
            {"a": fact(2026, 2, "12", company="a")}, 1,
        )
        stored = market.db_row(calculation_version=6)
        self.assertEqual(stored["lifecycle_status"], "complete")
        self.assertNotIn("completion_status", stored)

    def test_provisional_total_replaces_reported_firms_and_keeps_placeholders(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("x", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a"), "b": fact(2026, 2, "30", company="b").with_changes(is_pending=True)}
        previous = {"a": fact(2026, 1, "10", company="a"), "x": fact(2026, 1, "20", company="x")}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("42"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_final_total_uses_only_current_basket(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("x", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a"), "b": fact(2026, 2, "30", company="b")}
        previous = {"a": fact(2026, 1, "10", company="a"), "x": fact(2026, 1, "999", company="x")}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("42"))
        self.assertEqual(market.completion_status, "complete")

    def test_preferred_shares_are_excluded(self):
        self.assertFalse(_eligible_name("삼성전자우"))
        self.assertFalse(_eligible_name("현대차2우B"))
        self.assertTrue(_eligible_name("삼성전자"))


if __name__ == "__main__":
    unittest.main()

