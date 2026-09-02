from __future__ import annotations

import unittest
import requests
from datetime import date
from decimal import Decimal

from earnings_v2.aggregation import aggregate_market
from earnings_v2.models import CompanyIdentity, FinancialFact, PeriodicFiling
from earnings_v2.cli import completed_successfully, parser
from earnings_v2.pipeline import TARGETS, KoreaEarningsV2Pipeline, _eligible_name, filing_period, latest_completed_quarter
from earnings_v2.http import RETRYABLE_STATUS_CODES, RETRY_TOTAL, resilient_session
from earnings_v2.providers import EcosFxClient, KisClient, OpenDartClient, ProviderError
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
        return list(self.universes.get((market_id, year, quarter), []))

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

    def save_state(self, operation, status, cursor, error=None):
        record = {"status": status, "cursor": dict(cursor), "last_error": error}
        self.states[operation] = record
        self.saved_states.append((operation, status, dict(cursor), error))


class SimulatedDart:
    def __init__(self, filings=()):
        self.filings = list(filings)
        self.financial_calls: list[tuple[tuple[str, ...], int, int]] = []

    @property
    def request_count(self):
        return len(self.financial_calls)

    def periodic_filings(self, _start, _end):
        return list(self.filings)

    def multi_accounts(self, corp_codes, year, quarter):
        codes = tuple(corp_codes)
        self.financial_calls.append((codes, year, quarter))
        if quarter == 1:
            return [item for corp in codes for item in complete(corp, current="100", cumulative="100")]
        return [item for corp in codes for item in complete(corp, current="20", cumulative="120")]


class UsdDart(SimulatedDart):
    def __init__(self, target_corp: str, filings=()):
        super().__init__(filings)
        self.target_corp = target_corp

    def multi_accounts(self, corp_codes, year, quarter):
        rows = super().multi_accounts(corp_codes, year, quarter)
        return [
            {**item, "currency": "USD"} if item["corp_code"] == self.target_corp else item
            for item in rows
        ]


class SimulatedKis:
    def __init__(self, values=None):
        self.values = values or {}
        self.calls: list[tuple[str, int, int]] = []

    @property
    def request_count(self):
        return len(self.calls)

    def quarter_financials(self, ticker, year, quarter):
        self.calls.append((ticker, year, quarter))
        value = self.values.get(ticker)
        if isinstance(value, dict):
            return value
        return {
            "top_line": value,
            "operating_income": Decimal("10") if value is not None else None,
            "net_income": Decimal("8") if value is not None else None,
        }


class FailingKis(SimulatedKis):
    def quarter_financials(self, ticker, year, quarter):
        self.calls.append((ticker, year, quarter))
        raise ProviderError(f"KIS top-line request failed for {ticker}")


class FailingFx:
    def __init__(self):
        self.request_count = 0

    def usd_krw(self, _reference_date):
        self.request_count += 1
        raise ProviderError("ECOS USD/KRW timed out (ConnectTimeout)")


class SimulatedKrx:
    request_count = 0


class OpenDartTransportTests(unittest.TestCase):
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
        self.assertEqual(retry.allowed_methods, frozenset({"GET", "POST"}))

    def test_ecos_reports_timeout_type_without_exposing_key(self):
        class Session:
            @staticmethod
            def get(*_args, **_kwargs):
                raise requests.ConnectTimeout("secret-key")

        client = EcosFxClient("secret-key", session=Session())
        with self.assertRaisesRegex(ProviderError, r"ECOS USD/KRW timed out \(ConnectTimeout\)") as captured:
            client.usd_krw(date(2025, 12, 31))
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
            client.usd_krw(date(2025, 12, 31))
        self.assertNotIn("sensitive detail", str(captured.exception))


class CliContractTests(unittest.TestCase):
    def test_korean_market_targets_are_one_hundred_each(self):
        self.assertEqual(TARGETS, {"kr_largecap": 100, "kr_kosdaq": 100})

    def test_year_backfill_always_runs_oldest_quarter_first(self):
        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=object(), repository=object())
        visited: list[int] = []

        def run_quarter(_year, quarter, **_kwargs):
            visited.append(quarter)
            return {"status": "ready", "quarter": quarter}

        pipeline.run_quarter = run_quarter

        pipeline.run_year(2026)

        self.assertEqual(visited, [1, 2, 3, 4])

    def test_only_ready_results_are_successful(self):
        self.assertTrue(completed_successfully({"status": "ready"}))
        self.assertTrue(completed_successfully([{"status": "ready"}, {"status": "ready"}]))
        self.assertFalse(completed_successfully({"status": "incomplete"}))
        self.assertFalse(completed_successfully([{"status": "ready"}, {"status": "incomplete"}]))
        self.assertFalse(completed_successfully([]))

    def test_recalculation_mode_is_an_explicit_cli_path(self):
        args = parser().parse_args(["--year", "2026", "--quarter", "2", "--write", "--recalculate-only"])
        self.assertTrue(args.recalculate_only)

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

        class Dart:
            @staticmethod
            def periodic_filings(start, end):
                self.assertEqual((start, end), (date(2026, 9, 1), date(2026, 9, 2)))
                return [
                    PeriodicFiling("00000001", "20260901000001", date(2026, 9, 1), "반기보고서 (2026.06)"),
                    PeriodicFiling("00000002", "20260902000001", date(2026, 9, 2), "반기보고서 (2026.06)"),
                ]

        repository = Repository()
        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=Dart(), repository=repository)
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

        class Dart:
            @staticmethod
            def periodic_filings(start, end):
                self.assertEqual((start, end), (date(2026, 9, 2), date(2026, 9, 2)))
                return []

        pipeline = KoreaEarningsV2Pipeline(krx=object(), dart=Dart(), repository=Repository())
        pipeline.run_quarter = lambda *_args, **_kwargs: {"status": "incomplete"}
        pipeline.run_daily(write=True, today=date(2026, 9, 2))


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
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        first = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(first["filing_discovery"]["new_receipts"], 1)
        self.assertEqual(first["refreshed_companies"], 1)
        self.assertEqual([call[0] for call in dart.financial_calls], [(target_corp,), (target_corp,)])
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

    def test_pending_top_line_retries_only_kis_and_completes_market(self):
        target_company = "kr:00000099"
        repository = self.populated_repository()
        repository.seed_company(target_company, top_line=None, pending=True)
        target_ticker = "000099"
        dart = SimulatedDart()
        kis = SimulatedKis({target_ticker: Decimal("250")})
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=kis,
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(dart.financial_calls, [])
        self.assertEqual(kis.calls, [(target_ticker, 2026, 2)])
        self.assertEqual(result["retried_pending_top_lines"], 1)
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("250"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(repository.market_rows[("kr_largecap", 2026, 2)]["lifecycle_status"], "complete")

    def test_profit_incomplete_pending_company_refetches_full_provider_path(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        repository = self.populated_repository()
        repository.seed_company(
            target_company, top_line=None, operating_income=None, net_income=None, pending=True,
        )
        dart = SimulatedDart()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(result["status"], "ready")
        self.assertEqual([call[0] for call in dart.financial_calls], [(target_corp,), (target_corp,)])
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["operating_income"], Decimal("20"))
        self.assertFalse(stored["is_pending"])

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

    def test_backfill_refetches_previous_cumulative_instead_of_using_db_fact(self):
        target_company = "kr:00000099"
        repository = self.populated_repository()
        stale_previous = extract_company_fact(
            "00000099", target_company, 2026, 1,
            complete("00000099", current="999", cumulative="999"),
        )
        repository.company_rows[(target_company, 2026, 1)] = stale_previous.db_row(
            calculation_version=6,
        )
        dart = SimulatedDart()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository, kis=SimulatedKis(),
        )

        result = pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            repository.company_rows[(target_company, 2026, 2)]["operating_income"],
            Decimal("20"),
        )
        self.assertEqual(dart.financial_calls, [
            (tuple(f"{rank:08d}" for rank in range(1, 101)) + tuple(f"{1000 + rank:08d}" for rank in range(1, 101)), 2026, 2),
            (tuple(f"{rank:08d}" for rank in range(1, 101)) + tuple(f"{1000 + rank:08d}" for rank in range(1, 101)), 2026, 1),
        ])

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
        fx = FailingFx()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=UsdDart(target_corp), repository=repository,
            kis=SimulatedKis(), fx=fx,
        )

        with self.assertRaisesRegex(ProviderError, r"ECOS USD/KRW timed out \(ConnectTimeout\)"):
            pipeline.run_quarter(2026, 2, write=True)

        self.assertEqual(fx.request_count, 1)
        stored = repository.company_rows[(target_company, 2026, 2)]
        self.assertEqual(stored["top_line"], Decimal("100"))
        self.assertFalse(stored["is_pending"])
        self.assertEqual(repository.states["2026Q2"]["status"], "failed")

    def test_daily_fx_failure_preserves_company_and_retries_next_run(self):
        target_company = "kr:00000099"
        target_corp = "00000099"
        receipt = PeriodicFiling(
            target_corp, "20260902000009", date(2026, 9, 2), "반기보고서 (2026.06)",
        )
        repository = self.populated_repository()
        fx = FailingFx()
        dart = UsdDart(target_corp, [receipt])
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository,
            kis=SimulatedKis(), fx=fx,
        )

        first = pipeline.run_daily(write=True, today=date(2026, 9, 2))
        stored = repository.company_rows[(target_company, 2026, 2)]

        self.assertEqual(first["status"], "incomplete")
        self.assertEqual(first["issues"], [{
            "company": "kr_largecap-99",
            "field": "currency",
            "reason": "ECOS USD/KRW timed out (ConnectTimeout)",
        }])
        self.assertEqual(stored["top_line"], Decimal("100"))
        self.assertTrue(stored["is_pending"])
        self.assertEqual(fx.request_count, 1)

        second = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(second["filing_discovery"]["new_receipts"], 0)
        self.assertEqual(second["refreshed_companies"], 1)
        self.assertEqual(fx.request_count, 2)
        self.assertTrue(repository.company_rows[(target_company, 2026, 2)]["is_pending"])

    def test_pending_kis_retry_failure_does_not_abort_daily_run(self):
        target_company = "kr:00000099"
        repository = self.populated_repository()
        repository.seed_company(target_company, top_line=None, pending=True)
        kis = FailingKis()
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=SimulatedDart(), repository=repository, kis=kis,
        )

        result = pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(kis.calls, [("000099", 2026, 2)])
        self.assertEqual(result["status"], "incomplete")
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
        pipeline = KoreaEarningsV2Pipeline(
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
        pipeline = KoreaEarningsV2Pipeline(
            krx=SimulatedKrx(), dart=dart, repository=repository,
        )
        pipeline.run_quarter = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated failure"))

        with self.assertRaisesRegex(RuntimeError, "simulated failure"):
            pipeline.run_daily(write=True, today=date(2026, 9, 2))

        self.assertEqual(repository.states["daily_filings"]["cursor"], original_cursor)
        self.assertFalse(any(
            operation == "daily_filings" and cursor.get("last_checked_date") == "2026-09-02"
            for operation, _status, cursor, _error in repository.saved_states
        ))


class QuarterlyExtractionTests(unittest.TestCase):
    def test_q1_uses_current_cumulative_value(self):
        value = extract_company_fact("00000001", "kr:1", 2026, 1, complete("00000001", current="40", cumulative="40"), [])
        self.assertEqual(value.operating_income, Decimal("40"))

    def test_q2_always_subtracts_q1_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 2,
            complete("00000001", current="60", cumulative="100"),
            complete("00000001", current="40", cumulative="40"),
        )
        self.assertEqual(value.operating_income, Decimal("60"))

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

    def test_q3_always_subtracts_q2_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 3,
            complete("00000001", current="70", cumulative="170"),
            complete("00000001", current="60", cumulative="100"),
        )
        self.assertEqual(value.net_income, Decimal("70"))

    def test_q4_uses_annual_minus_q3_cumulative(self):
        value = extract_company_fact(
            "00000001", "kr:1", 2026, 4,
            complete("00000001", current="250", cumulative=""),
            complete("00000001", current="70", cumulative="170"),
        )
        self.assertEqual(value.top_line, Decimal("80"))
        self.assertEqual(value.source_top_line_cumulative, Decimal("250"))

    def test_cfs_and_ofs_are_not_mixed(self):
        current = [
            row("1", "매출액", current="100", cumulative="100", scope="CFS"),
            row("1", "영업이익", current="20", cumulative="20", scope="CFS"),
            *complete("1", current="50", cumulative="50", scope="OFS"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertEqual(value.consolidation_scope, "OFS")
        self.assertEqual(value.net_income, Decimal("50"))

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

    def test_explicit_financial_top_line_is_allowed(self):
        current = [
            row("1", "순영업이익", current="100", cumulative="100"),
            row("1", "영업이익", current="20", cumulative="20"),
            row("1", "당기순이익", current="10", cumulative="10"),
        ]
        value = extract_company_fact("1", "kr:1", 2026, 1, current, [])
        self.assertEqual(value.top_line, Decimal("100"))


class KisFallbackTests(unittest.TestCase):
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


class GrowthAndAggregationTests(unittest.TestCase):
    def test_latest_completed_quarter_uses_previous_calendar_quarter(self):
        self.assertEqual(latest_completed_quarter(date(2026, 9, 2)), (2026, 2))
        self.assertEqual(latest_completed_quarter(date(2026, 1, 5)), (2025, 4))

    def test_yoy_requires_prior_year_and_turns_are_states(self):
        rows = calculate_financial_series([fact(2025, 1, "-10"), fact(2026, 1, "20")])
        self.assertIsNone(rows[-1].operating_income_yoy_pct)
        self.assertEqual(rows[-1].operating_income_yoy_state, "black_turn")

    def test_seasonal_qoq_waits_for_two_historical_same_quarter_transitions(self):
        rows = [
            fact(2020, 4, "100"), fact(2021, 1, "110"), fact(2021, 2, "100"), fact(2021, 3, "100"), fact(2021, 4, "100"),
            fact(2022, 1, "120"), fact(2022, 2, "100"), fact(2022, 3, "100"), fact(2022, 4, "100"), fact(2023, 1, "130"),
        ]
        calculated = calculate_financial_series(rows)
        self.assertEqual(calculated[1].operating_income_qoq_state, "insufficient_history")
        self.assertEqual(calculated[-1].operating_income_qoq_state, "normal")

    def test_incremental_point_uses_saved_window_without_recalculating_history(self):
        current = fact(2026, 2, "130")
        calculated, raw = calculate_financial_point(
            current,
            previous=fact(2026, 1, "100"),
            prior_year=fact(2025, 2, "110"),
            seasonal_samples={
                "operating_income": [Decimal("10"), Decimal("20")],
                "net_income": [Decimal("10"), Decimal("20")],
            },
        )
        self.assertEqual(calculated.operating_income_qoq_sa_pct, Decimal("15"))
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

    def test_pending_company_is_excluded_even_when_profit_values_exist(self):
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
        self.assertEqual(market.operating_income_total, Decimal("32"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_missing_baseline_uses_available_actuals_as_provisional(self):
        current_members = [member("a", 1), member("b", 2)]
        current = {"a": fact(2026, 2, "12", company="a")}
        market = aggregate_market("kr_largecap", 2026, 2, current_members, current, 2)
        self.assertEqual(market.operating_income_total, Decimal("12"))
        self.assertEqual(market.reported_company_count, 1)
        self.assertEqual(market.completion_status, "provisional")

    def test_incomplete_prior_placeholder_is_omitted_from_provisional_total(self):
        current_members = [member("a", 1), member("b", 2)]
        previous_members = [member("a", 1, year=2026, quarter=1), member("b", 2, year=2026, quarter=1)]
        current = {"a": fact(2026, 2, "12", company="a")}
        previous = {"b": fact(2026, 1, "20", company="b").with_changes(top_line=None, is_pending=True)}
        market = aggregate_market(
            "kr_largecap", 2026, 2, current_members, current, 2,
            comparison_members=previous_members, comparison_facts=previous,
        )
        self.assertEqual(market.operating_income_total, Decimal("12"))
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
        self.assertEqual(market.operating_income_total, Decimal("32"))
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
