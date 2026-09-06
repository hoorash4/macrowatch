from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from earnings_us.models import MarketSecurity, USCompany, USFinancialFact, market_period
from earnings_us.backfill import (
    USEarningsBackfillPipeline,
    _historical_ticker_directory,
    _backfill_name_matches,
    _select_backfill_fact,
    _verified_backfill_security,
)
from earnings_us.backfill_cli import chronological_period_range, period_range, resumable_periods, run_earnings_range
from earnings_us.constituents import (
    USIndexConstituentClient,
    archive_covers_filing_window,
    _decode_filing,
    _name_match_score,
    _normal_name,
    extract_oef_holdings,
    extract_oef_nport_holdings,
    extract_oef_series_accessions,
    extract_series_accessions,
    legacy_report_date,
    is_quarter_end_report_date,
    extract_nport_equity_holdings,
)
from earnings_us.pipeline import USEarningsAutomaticPipeline, in_snapshot_window
from earnings_us.providers import ProviderError, SecEdgarClient
from earnings_us.transform import extract_new_sec_facts
from earnings_us.six_k import SixKDocument, SixKFiling, extract_six_k_fact, linked_financial_documents


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
    @staticmethod
    def pending_fact(*, quarter: int = 2, top_line=None, operating_income=None, net_income=None):
        return USFinancialFact(
            company_id="us:cik:0000000001", fiscal_year=2026, fiscal_quarter=quarter,
            period_start=date(2026, (quarter - 1) * 3 + 1, 1),
            period_end=date(2026, quarter * 3, 31 if quarter == 1 else 30),
            top_line=top_line, operating_income=operating_income, net_income=net_income,
            source_filing_id=f"q{quarter}", filing_date=date(2026, quarter * 3 + 1, 20),
            is_pending=True,
        )

    def test_retry_incomplete_rechecks_only_repository_pending_rows(self):
        current = self.pending_fact(top_line=Decimal("200"))

        class Repository:
            def __init__(self):
                self.saved = []

            def us_pending_rows(self, _):
                return [{"market_id": "us_sp100", "market_year": 2026, "market_quarter": 2,
                         "company_id": current.company_id}]

            def us_active_companies(self, _):
                return [{"company_id": current.company_id, "company_name": "Example", "cik": "0000000001"}]

            def company_history(self, _):
                return [current.db_row()]

            def upsert_company_quarters(self, rows):
                self.saved.extend(rows)

            def save_us_state(self, *_args):
                pass

        class Sec:
            request_count = 0

            def company_facts(self, _):
                return payload()

            def delisting_dates(self, _):
                return []

            def company_ticker_rows(self):
                return [("EX", "Example", "0000000001")]

        repository = Repository()
        pipeline = USEarningsAutomaticPipeline(repository, Sec(), None)
        pipeline.recalculate_market_period = lambda *_: None

        result = pipeline.retry_incomplete(today=date(2026, 9, 6), write=True)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["updated_company_quarters"], 1)
        self.assertEqual(result["remaining_pending_company_quarters"], 0)
        self.assertEqual(len(repository.saved), 1)
        self.assertEqual(repository.saved[0]["top_line"], Decimal("200"))
        self.assertEqual(repository.saved[0]["operating_income"], Decimal("20"))
        self.assertFalse(repository.saved[0]["is_pending"])

    def test_retry_incomplete_carries_prior_values_only_for_confirmed_disappearance(self):
        prior = USFinancialFact(
            company_id="us:cik:0000000001", fiscal_year=2026, fiscal_quarter=1,
            period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
            top_line=Decimal("100"), operating_income=Decimal("10"), net_income=Decimal("8"),
            source_filing_id="q1", filing_date=date(2026, 5, 1), is_pending=False,
        )
        current = self.pending_fact()
        current_payload = payload()
        current_payload["facts"]["us-gaap"].pop("OperatingIncomeLoss")
        current_payload["facts"]["us-gaap"].pop("NetIncomeLoss")

        class Repository:
            def __init__(self):
                self.saved = []

            def us_pending_rows(self, _):
                return [{"market_id": "us_sp100", "market_year": 2026, "market_quarter": 2,
                         "company_id": current.company_id}]

            def us_active_companies(self, _):
                return [{"company_id": current.company_id, "company_name": "Gone", "cik": "0000000001"}]

            def company_history(self, _):
                return [prior.db_row(), current.db_row()]

            def upsert_company_quarters(self, rows):
                self.saved.extend(rows)

            def save_us_state(self, *_args):
                pass

        class Sec:
            request_count = 0

            def company_facts(self, _):
                return current_payload

            def delisting_dates(self, _):
                return [date(2026, 7, 15)]

            def company_ticker_rows(self):
                return []

        repository = Repository()
        pipeline = USEarningsAutomaticPipeline(repository, Sec(), None)
        pipeline.recalculate_market_period = lambda *_: None

        result = pipeline.retry_incomplete(today=date(2026, 9, 6), write=True)

        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(repository.saved), 1)
        self.assertEqual(repository.saved[0]["top_line"], Decimal("200"))
        self.assertEqual(repository.saved[0]["operating_income"], Decimal("10"))
        self.assertEqual(repository.saved[0]["net_income"], Decimal("8"))
        self.assertIn("carry-forward-form25-2026-07-15", repository.saved[0]["source_filing_id"])
        self.assertFalse(repository.saved[0]["is_pending"])

    def test_retry_incomplete_keeps_active_unresolved_company_pending(self):
        current = self.pending_fact()
        current_payload = payload()
        current_payload["facts"]["us-gaap"].pop("OperatingIncomeLoss")
        current_payload["facts"]["us-gaap"].pop("NetIncomeLoss")

        class Repository:
            def __init__(self):
                self.saved = []

            def us_pending_rows(self, _):
                return [{"market_id": "us_sp100", "market_year": 2026, "market_quarter": 2,
                         "company_id": current.company_id}]

            def us_active_companies(self, _):
                return [{"company_id": current.company_id, "company_name": "Active", "cik": "0000000001"}]

            def company_history(self, _):
                return [current.db_row()]

            def upsert_company_quarters(self, rows):
                self.saved.extend(rows)

            def save_us_state(self, *_args):
                pass

        class Sec:
            request_count = 0

            def company_facts(self, _):
                return current_payload

            def company_ticker_rows(self):
                return [("ACT", "Active", "0000000001")]

            def delisting_dates(self, _):
                raise AssertionError("Active listed company must not trigger a delisting request")

        repository = Repository()
        pipeline = USEarningsAutomaticPipeline(repository, Sec(), None)
        pipeline.recalculate_market_period = lambda *_: None

        result = pipeline.retry_incomplete(today=date(2026, 9, 6), write=True)

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["remaining_pending_company_quarters"], 1)
        self.assertEqual(len(repository.saved), 1)
        self.assertTrue(repository.saved[0]["is_pending"])

    def test_sec_delisting_dates_accepts_only_form_25_families(self):
        client = object.__new__(SecEdgarClient)
        client.submissions = lambda _: {"filings": {"recent": {
            "form": ["10-Q", "25-NSE", "25", "8-K"],
            "filingDate": ["2026-05-01", "2026-07-15", "2026-07-16", "2026-07-17"],
        }}}

        self.assertEqual(client.delisting_dates("1"), [date(2026, 7, 15), date(2026, 7, 16)])

    def test_six_k_mixed_currency_table_selects_current_usd_quarter(self):
        html = """
        <table>
          <tr><th>For the three months ended</th><th>For the six months ended</th></tr>
          <tr><th>June 30, 2024</th><th>June 30, 2025</th><th>June 30, 2025</th>
              <th>June 30, 2024</th><th>June 30, 2025</th><th>June 30, 2025</th></tr>
          <tr><th>RMB</th><th>RMB</th><th>US$</th><th>RMB</th><th>RMB</th><th>US$</th></tr>
          <tr><th>(In millions)</th></tr>
          <tr><td>Total net revenues</td><td>291,397</td><td>356,660</td><td>49,788</td><td>551,446</td><td>657,742</td><td>91,817</td></tr>
          <tr><td>Income/(Loss) from operations</td><td>10,501</td><td>(859</td><td>)</td><td>(120</td><td>)</td><td>18,201</td><td>9,674</td><td>1,350</td></tr>
          <tr><td>Net income</td><td>13,594</td><td>6,709</td><td>937</td><td>20,959</td><td>17,988</td><td>2,511</td></tr>
        </table>
        """
        filing = SixKFiling("mixed", date(2025, 8, 14), date(2025, 8, 14), "form.htm")

        fact = extract_six_k_fact("company", filing, [SixKDocument("ex99.htm", html)], 2025, 2)

        self.assertIsNotNone(fact)
        self.assertEqual(fact.top_line, Decimal("49788000000"))
        self.assertEqual(fact.operating_income, Decimal("-120000000"))
        self.assertEqual(fact.net_income, Decimal("937000000"))
        self.assertFalse(fact.is_pending)

    def test_six_k_week_based_period_end_maps_to_prior_calendar_quarter(self):
        html = """
        <table>
          <tr><th>Three months ended</th><th>Apr 2, 2017</th><th>Apr 1, 2018</th></tr>
          <tr><th>(in millions EUR)</th></tr>
          <tr><td>Total net sales</td><td>1,943.6</td><td>2,285.0</td></tr>
          <tr><td>Operating income</td><td>541.8</td><td>668.5</td></tr>
          <tr><td>Net income</td><td>460.9</td><td>580.5</td></tr>
        </table>
        """
        filing = SixKFiling(
            "week-end", date(2018, 4, 18), date(2018, 4, 18), "form6kq1resultsapril182018.htm",
        )

        fact = extract_six_k_fact(
            "company", filing, [SixKDocument("financialstatements.htm", html)], 2018, 1,
            lambda _currency, _date: Decimal(1),
        )

        self.assertIsNotNone(fact)
        self.assertEqual(fact.period_end, date(2018, 4, 1))
        self.assertEqual(fact.operating_income, Decimal("668500000.0"))
        self.assertFalse(fact.is_pending)

    def test_six_k_backfill_recovers_split_date_headers_and_statement_tables(self):
        html = """
        <table>
          <tr><th>For the three months ended</th></tr>
          <tr><th>March31, 2017</th><th>March31, 2018</th><th>March31, 2018</th></tr>
          <tr><th>RMB</th><th>RMB</th><th>US$</th></tr><tr><th>(in thousands)</th></tr>
          <tr><td>Total net revenues</td><td>75,218</td><td>100,127</td><td>15,962</td></tr>
          <tr><td>Income from operations</td><td>661</td><td>4,431</td><td>706</td></tr>
        </table>
        <table>
          <tr><th>For the three months ended</th></tr>
          <tr><th>March31, 2017</th><th>March31, 2018</th><th>March31, 2018</th></tr>
          <tr><th>RMB</th><th>RMB</th><th>US$</th></tr><tr><th>(in thousands)</th></tr>
          <tr><td>Net income</td><td>355</td><td>1,477</td><td>235</td></tr>
          <tr><td>Net income attributable to ordinary shareholders</td><td>239</td><td>1,524</td><td>243</td></tr>
        </table>
        """
        filing = SixKFiling("split", date(2018, 5, 9), date(2018, 5, 9), "form.htm")

        automatic = extract_six_k_fact("company", filing, [SixKDocument("results.htm", html)], 2018, 1)
        backfill = extract_six_k_fact(
            "company", filing, [SixKDocument("results.htm", html)], 2018, 1,
            backfill_mode=True,
        )

        self.assertIsNone(automatic)
        self.assertEqual(backfill.top_line, Decimal("15962000"))
        self.assertEqual(backfill.operating_income, Decimal("706000"))
        self.assertEqual(backfill.net_income, Decimal("243000"))
        self.assertFalse(backfill.is_pending)

    def test_six_k_backfill_prefers_direct_quarter_statement_over_ytd_summary(self):
        html = """
        <table><tr><th>YTD 2022</th><th>Q3 2022</th></tr>
          <tr><td>Total Revenue</td><td>33,144</td><td>25,406</td><td>30</td><td>37</td><td>10,982</td><td>9,866</td></tr>
          <tr><td>Operating profit/(loss)</td><td>2,663</td><td>1,348</td><td>98</td><td>100</td><td>1,245</td><td>(1,674)</td></tr>
          <tr><td>Profit/(Loss) for the period</td><td>2,391</td><td>461</td><td>500</td><td>500</td><td>1,642</td><td>(1,651)</td></tr>
        </table>
        <table><tr><th>For the quarter ended September 30, 2022</th><th>September 30, 2021</th></tr>
          <tr><th>(in USD millions)</th><th>$m</th><th>$m</th></tr>
          <tr><td>Total Revenue</td><td>10,982</td><td>9,866</td></tr>
          <tr><td>Operating profit/(loss)</td><td>1,245</td><td>(1,674)</td></tr>
          <tr><td>Profit/(Loss) for the period</td><td>1,642</td><td>(1,651)</td></tr>
        </table>
        """
        filing = SixKFiling("direct-quarter", date(2022, 11, 10), date(2022, 11, 10), "form.htm")

        fact = extract_six_k_fact(
            "company", filing, [SixKDocument("results.htm", html)], 2022, 3,
            backfill_mode=True,
        )

        self.assertEqual(fact.top_line, Decimal("10982000000"))
        self.assertEqual(fact.operating_income, Decimal("1245000000"))
        self.assertEqual(fact.net_income, Decimal("1642000000"))
        self.assertFalse(fact.is_pending)

    def test_six_k_rejects_cross_statement_scale_mismatch(self):
        html = """
        <div>Second quarter 2023 financial results</div>
        <table><tr><th>Three months ended</th><th>June 30, 2023</th></tr>
        <tr><th>(USD in millions)</th></tr>
        <tr><td>Revenue</td><td>1</td></tr>
        <tr><td>Operating income</td><td>2,456</td></tr>
        <tr><td>Net income</td><td>1,820</td></tr></table>
        """
        filing = SixKFiling("mismatch", date(2023, 7, 28), date(2023, 6, 30), "q2-results.htm")

        self.assertIsNone(
            extract_six_k_fact("company", filing, [SixKDocument("results.htm", html)], 2023, 2),
        )

    def test_six_k_flat_q4_statement_does_not_use_full_year_column(self):
        html = """
        <div>Financial results. Three months ended Year ended Dec 31, Dec 31, Dec 31, Dec 31,
        (Unaudited, EUR, in millions) 2016 2017 2016 2017
        Total net sales 2,000 2,560 6,700 9,052
        Income from operations 500 766 1,800 2,496
        Net income 450 714 1,600 2,119</div>
        """
        filing = SixKFiling("q4", date(2018, 1, 17), date(2018, 1, 17), "form.htm")

        fact = extract_six_k_fact(
            "company", filing, [SixKDocument("financialstatements.htm", html)], 2017, 4,
            lambda currency, _date: Decimal("1.1") if currency == "EUR" else Decimal(1),
        )

        self.assertIsNotNone(fact)
        self.assertEqual(fact.top_line, Decimal("2816000000.0"))
        self.assertEqual(fact.operating_income, Decimal("842600000.0"))
        self.assertEqual(fact.net_income, Decimal("785400000.0"))

    def test_six_k_financial_exhibit_links_support_current_and_legacy_names(self):
        html = """
        <a href="release-ex99.1.htm">Press Release - Quarterly Financial Results</a>
        <a href="https://example.com/external.htm">External</a>
        """
        self.assertEqual(linked_financial_documents(html), ["release-ex99.1.htm"])

    def test_daily_edgar_creates_row_from_new_six_k(self):
        table = """
        <table><tr><th>Three Months Ended</th></tr><tr><th>June 30, 2026</th></tr>
        <tr><th>($ in millions)</th></tr><tr><td>Revenue</td><td>200</td></tr>
        <tr><td>Operating income</td><td>20</td></tr><tr><td>Net income</td><td>16</td></tr></table>
        """
        filing = SixKFiling("six-k", date(2026, 8, 1), date(2026, 8, 1), "form.htm")

        class Repository:
            def __init__(self):
                self.saved = []

            def us_state(self, _operation):
                return None

            def us_active_companies(self, _year):
                return [{"company_id": "foreign", "company_name": "Foreign", "cik": "1"}]

            def upsert_company_quarters(self, rows):
                self.saved.extend(rows)

            def save_us_state(self, *_args):
                pass

        class Sec:
            request_count = 0

            def new_financial_accessions(self, *_args):
                return set()

            def six_k_filings(self, *_args, **_kwargs):
                return [filing]

            def six_k_documents(self, *_args):
                return [SixKDocument("ex99.htm", table)]

        repository = Repository()
        pipeline = USEarningsAutomaticPipeline(repository, Sec(), None)
        pipeline.recalculate_market_period = lambda *_args: None

        result = pipeline.daily_edgar(today=date(2026, 8, 2), write=True)

        self.assertEqual(result["updated_company_quarters"], 1)
        self.assertEqual(repository.saved[0]["top_line"], Decimal("200000000"))
        self.assertFalse(repository.saved[0]["is_pending"])

    def test_archive_selection_uses_post_quarter_filing_dates(self):
        entry = {"filingFrom": "2023-01-01", "filingTo": "2023-03-31"}

        self.assertTrue(archive_covers_filing_window(entry, date(2022, 12, 31)))
        self.assertFalse(archive_covers_filing_window(entry, date(2022, 6, 30)))

    def test_weekend_quarter_end_accepts_last_fund_reporting_day(self):
        self.assertTrue(is_quarter_end_report_date("2022-12-30", date(2022, 12, 31)))
        self.assertFalse(is_quarter_end_report_date("2022-11-30", date(2022, 12, 31)))

    def test_shared_index_members_are_persisted_once_per_database_key(self):
        class FakeRepository:
            def __init__(self):
                self.company_batches = []
                self.identifier_batches = []

            def upsert_companies(self, rows):
                self.company_batches.append(list(rows))

            def upsert_identifiers(self, rows):
                self.identifier_batches.append(list(rows))

        repository = FakeRepository()
        pipeline = USEarningsAutomaticPipeline(repository, None, None)
        reference_date = date(2026, 6, 30)
        shared = [
            MarketSecurity("SHR", "Shared Inc", "0000000001", Decimal("1"), 1, reference_date, market)
            for market in ("us_sp100", "us_nasdaq100")
        ]

        pipeline.persist_universe_securities(shared, historical=True)
        older = [
            MarketSecurity("OLD", "Old Shared Inc", "0000000001", Decimal("1"), 1, date(2026, 3, 31), market)
            for market in ("us_sp100", "us_nasdaq100")
        ]
        pipeline.persist_universe_securities(older, historical=True)

        self.assertEqual([len(batch) for batch in repository.company_batches], [1])
        self.assertEqual([len(batch) for batch in repository.identifier_batches], [1, 1, 1, 1])
        self.assertTrue(all(not row["is_primary"] for batch in repository.identifier_batches for row in batch))

    def test_cik_shaped_value_is_never_persisted_as_a_ticker(self):
        class FakeRepository:
            def __init__(self):
                self.identifier_batches = []

            def upsert_companies(self, _rows):
                pass

            def upsert_identifiers(self, rows):
                self.identifier_batches.append(list(rows))

        repository = FakeRepository()
        pipeline = USEarningsAutomaticPipeline(repository, None, None)
        pipeline.persist_universe_securities([
            MarketSecurity(
                "0001835632", "Marvell Technology, Inc.", "0001835632", Decimal("1"), 1,
                date(2026, 6, 30), "us_nasdaq100",
            )
        ], historical=True)

        identifiers = [row for batch in repository.identifier_batches for row in batch]
        self.assertEqual([row["identifier_type"] for row in identifiers], ["cik"])

    def test_universe_backfill_periods_run_newest_to_oldest(self):
        periods = period_range(2026, 2, 2016, 1)
        self.assertEqual(periods[:3], [(2026, 2), (2026, 1), (2025, 4)])
        self.assertEqual(periods[-1], (2016, 1))
        self.assertEqual(len(periods), 42)

    def test_earnings_backfill_periods_run_oldest_to_newest(self):
        periods = chronological_period_range(2016, 1, 2026, 2)
        self.assertEqual(periods[:3], [(2016, 1), (2016, 2), (2016, 3)])
        self.assertEqual(periods[-1], (2026, 2))
        self.assertEqual(len(periods), 42)

    def test_failed_range_resumes_at_failed_period_without_prior_cleanup(self):
        periods = chronological_period_range(2016, 1, 2016, 4)
        state = {"status": "failed", "cursor": {
            "range_start": "2016Q1", "range_end": "2016Q4", "failed_period": "2016Q3",
        }}

        pending, interrupted = resumable_periods(periods, state, "2016Q1", "2016Q4")

        self.assertEqual(pending, [(2016, 3), (2016, 4)])
        self.assertIsNone(interrupted)

    def test_interrupted_running_period_is_cleaned_then_retried(self):
        class Repository:
            def __init__(self):
                self.cleaned = []
                self.states = []

            def us_state(self, _):
                return {"status": "running", "cursor": {
                    "range_start": "2016Q1", "range_end": "2016Q2", "current_period": "2016Q2",
                }}

            def clear_us_backfill_period(self, year, quarter):
                self.cleaned.append((year, quarter))
                return {"company_rows_deleted": 1}

            def save_us_state(self, *args):
                self.states.append(args)

        class Pipeline:
            def __init__(self):
                self.repository = Repository()
                self.called = []

            def backfill_period(self, year, quarter, **_kwargs):
                self.called.append((year, quarter))
                return {"period": f"{year}Q{quarter}", "status": "ready"}

        pipeline = Pipeline()
        result = run_earnings_range(pipeline, [(2016, 1), (2016, 2)], write=True)

        self.assertEqual(pipeline.repository.cleaned, [(2016, 2)])
        self.assertEqual(pipeline.called, [(2016, 2)])
        self.assertEqual(result["processed_periods"], 1)

    def test_force_rerun_ignores_completed_range_checkpoint(self):
        class Repository:
            def __init__(self):
                self.states = []

            def us_state(self, _):
                raise AssertionError("force rerun must not read the completed checkpoint")

            def save_us_state(self, *args):
                self.states.append(args)

        class Pipeline:
            def __init__(self):
                self.repository = Repository()
                self.called = []

            def backfill_period(self, year, quarter, **_kwargs):
                self.called.append((year, quarter))
                return {"period": f"{year}Q{quarter}", "status": "ready"}

        pipeline = Pipeline()
        result = run_earnings_range(
            pipeline, [(2016, 1), (2016, 2)], write=True, force_rerun=True,
        )

        self.assertEqual(pipeline.called, [(2016, 1), (2016, 2)])
        self.assertEqual(result["processed_periods"], 2)
        self.assertFalse(result["already_complete"])

    def test_backfill_clears_stale_period_rows_even_when_source_is_now_missing(self):
        member = USCompany(
            company_id="us:cik:0000000001", company_name="Example", ticker="EX",
            cik="0000000001", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2023, 6, 30),
        )

        class Repository:
            def __init__(self):
                self.cleared = []
                self.saved = []

            def us_universe(self, market, _year, _quarter):
                return [member] if market == "us_sp100" else []

            def clear_us_backfill_period(self, year, quarter):
                self.cleared.append((year, quarter))

            def upsert_company_quarters(self, rows):
                self.saved.extend(rows)

            def save_us_state(self, *_args):
                pass

        class Pipeline(USEarningsBackfillPipeline):
            def _company_facts(self, _member):
                return []

            def _historical_ticker_candidates(self, _member, _year, _quarter):
                return []

            def _six_k_candidates(self, _company_id, _cik, _year, _quarter):
                return []

            def _delisted_carry_forward(self, _member, _year, _quarter):
                return None

            def recalculate_market_period(self, year, quarter):
                self.recalculated = (year, quarter)

        repository = Repository()
        pipeline = Pipeline(repository, type("Sec", (), {"request_count": 0})(), None)

        result = pipeline.backfill_period(2023, 2, write=True)

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(repository.cleared, [(2023, 2)])
        self.assertEqual(repository.saved, [])
        self.assertEqual(pipeline.recalculated, (2023, 2))

    def test_backfill_derives_ifrs_annual_quarter_from_three_stored_quarters(self):
        member = USCompany(
            company_id="us:cik:0000000001", company_name="Foreign", ticker="FOR",
            cik="0000000001", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2022, 6, 30),
        )
        prior = []
        for year, quarter, end, values in (
            (2021, 3, date(2021, 9, 30), (Decimal("100"), Decimal("10"), Decimal("8"))),
            (2021, 4, date(2021, 12, 31), (Decimal("110"), Decimal("11"), Decimal("9"))),
            (2022, 1, date(2022, 3, 31), (Decimal("120"), Decimal("12"), Decimal("10"))),
        ):
            prior.append(USFinancialFact(
                company_id=member.company_id, fiscal_year=year, fiscal_quarter=quarter,
                period_start=None, period_end=end, top_line=values[0], operating_income=values[1],
                net_income=values[2], source_filing_id="prior", filing_date=end,
                is_pending=False,
            ).db_row())
        annual = {
            "RevenueFromContractsWithCustomers": "500",
            "ProfitLossFromOperatingActivities": "50",
            "ProfitLossAttributableToOwnersOfParent": "40",
        }
        facts = {tag: {"units": {"USD": [{
            "start": "2021-07-01", "end": "2022-06-30", "val": value,
            "accn": "annual", "fy": 2021, "fp": "FY", "form": "20-F",
            "filed": "2022-08-19",
        }]}} for tag, value in annual.items()}

        class Repository:
            def company_history(self, _company_ids):
                return prior

        class Sec:
            def company_facts(self, _cik):
                return {"facts": {"ifrs-full": facts}}

        pipeline = USEarningsBackfillPipeline(Repository(), Sec(), None)
        pipeline._fx_to_usd = lambda *_args: Decimal(1)

        fact = pipeline._ifrs_annual_candidate(member, 2022, 2)

        self.assertEqual(fact.top_line, Decimal("170"))
        self.assertEqual(fact.operating_income, Decimal("17"))
        self.assertEqual(fact.net_income, Decimal("13"))
        self.assertFalse(fact.is_pending)

    def test_range_failure_cleans_only_current_period_and_stops(self):
        class Repository:
            def __init__(self):
                self.cleaned = []
                self.states = []

            def us_state(self, _):
                return None

            def clear_us_backfill_period(self, year, quarter):
                self.cleaned.append((year, quarter))
                return {"company_rows_deleted": 2}

            def save_us_state(self, *args):
                self.states.append(args)

        class Pipeline:
            def __init__(self):
                self.repository = Repository()
                self.called = []

            def backfill_period(self, year, quarter, **_kwargs):
                self.called.append((year, quarter))
                if quarter == 2:
                    raise ProviderError("SEC unavailable")
                return {"period": f"{year}Q{quarter}", "status": "ready"}

        pipeline = Pipeline()
        with self.assertRaisesRegex(RuntimeError, "stopped at 2016Q2"):
            run_earnings_range(pipeline, [(2016, 1), (2016, 2), (2016, 3)], write=True)

        self.assertEqual(pipeline.called, [(2016, 1), (2016, 2)])
        self.assertEqual(pipeline.repository.cleaned, [(2016, 2)])
        self.assertTrue(any(args[1] == "failed" for args in pipeline.repository.states))

    def test_backfill_reuses_one_companyfacts_payload_across_periods(self):
        class Sec:
            request_count = 0

            def company_facts(self, _):
                self.request_count += 1
                return payload()

        member = USCompany(
            company_id="us:cik:0000000001", company_name="Example", ticker="EX",
            cik="0000000001", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2026, 3, 31),
        )
        pipeline = USEarningsBackfillPipeline(object(), Sec(), None)

        first = pipeline._company_facts(member)
        second = pipeline._company_facts(member)

        self.assertIs(first, second)
        self.assertEqual(pipeline.sec.request_count, 1)

    def test_backfill_carries_prior_complete_values_for_confirmed_form25_exit(self):
        prior = USFinancialFact(
            company_id="us:cik:0000000001", fiscal_year=2026, fiscal_quarter=1,
            period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
            top_line=Decimal("100"), operating_income=Decimal("10"), net_income=Decimal("8"),
            source_filing_id="q1", filing_date=date(2026, 5, 1), is_pending=False,
        )

        class Repository:
            def company_history(self, _company_ids):
                return [prior.db_row()]

        class Sec:
            def company_ticker_rows(self):
                return []

            def delisting_dates(self, _cik):
                return [date(2026, 7, 15)]

        member = USCompany(
            company_id=prior.company_id, company_name="Gone", ticker="GONE",
            cik="0000000001", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2026, 6, 30),
        )
        pipeline = USEarningsBackfillPipeline(Repository(), Sec(), None)

        result = pipeline._delisted_carry_forward(member, 2026, 2)

        self.assertIsNotNone(result)
        self.assertEqual(result.top_line, Decimal("100"))
        self.assertEqual(result.period_end, date(2026, 6, 30))
        self.assertEqual(result.source_filing_id, "carry-forward-form25-2026-07-15")

    def test_backfill_delisting_carry_uses_next_fiscal_key_not_market_quarter(self):
        prior = USFinancialFact(
            company_id="us:cik:0000000001", fiscal_year=2026, fiscal_quarter=2,
            period_start=date(2025, 12, 1), period_end=date(2026, 2, 28),
            top_line=Decimal("100"), operating_income=Decimal("10"), net_income=Decimal("8"),
            source_filing_id="fiscal-q2", filing_date=date(2026, 4, 1), is_pending=False,
        )

        class Repository:
            def company_history(self, _company_ids):
                return [prior.db_row()]

        class Sec:
            def company_ticker_rows(self):
                return []

            def delisting_dates(self, _cik):
                return [date(2026, 7, 15)]

        member = USCompany(
            company_id=prior.company_id, company_name="Gone", ticker="GONE",
            cik="0000000001", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2026, 6, 30),
        )
        result = USEarningsBackfillPipeline(Repository(), Sec(), None)._delisted_carry_forward(
            member, 2026, 2,
        )

        self.assertEqual((result.fiscal_year, result.fiscal_quarter), (2026, 3))

    def test_backfill_uses_same_ticker_predecessor_cik_for_exact_market_period(self):
        class Repository:
            def us_active_companies(self, _since_year):
                return [
                    {"ticker": "BLK", "cik": "0001364742"},
                    {"ticker": "BLK", "cik": "0002012383"},
                ]

        class Sec:
            def company_facts(self, cik):
                source = payload()
                if cik.zfill(10) == "0001364742":
                    for fact in source["facts"]["us-gaap"].values():
                        fact["units"]["USD"] = [
                            entry(fy=2024, fp="Q1", accn="old-q1", start="2024-01-01",
                                  end="2024-03-31", filed="2024-05-01", value="10")
                        ]
                return source

        member = USCompany(
            company_id="us:cik:0002012383", company_name="BlackRock", ticker="BLK",
            cik="0002012383", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2024, 3, 31),
        )
        pipeline = USEarningsBackfillPipeline(Repository(), Sec(), None)

        candidates = pipeline._historical_ticker_candidates(member, 2024, 1)

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].company_id, member.company_id)
        self.assertTrue(candidates[0].fully_complete)

    def test_backfill_rejects_same_ticker_fact_from_different_named_issuer(self):
        class Repository:
            def us_active_companies(self, _since_year):
                return [{"company_name": "ATLASSIAN CLS A CS", "ticker": "TEAM", "cik": "0000027419"}]

        class Sec:
            def company_facts(self, _cik):
                source = payload()
                source["entityName"] = "TARGET CORP"
                return source

        member = USCompany(
            company_id="us:cik:0001650372", company_name="Atlassian Corp.", ticker="TEAM",
            cik="0001650372", market_id="us_nasdaq100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2022, 6, 30),
        )

        candidates = USEarningsBackfillPipeline(Repository(), Sec(), None)._historical_ticker_candidates(
            member, 2022, 2,
        )

        self.assertEqual(candidates, [])

    def test_historical_directory_rejects_current_issuer_name_on_wrong_cik(self):
        rows = [
            {"company_name": "ATLASSIAN CLS A CS", "ticker": "TEAM", "cik": "0000027419"},
            {"company_name": "Target Corp", "ticker": "TGT", "cik": "0000027419"},
        ]
        directory = {"TEAM": "0001650372", "TGT": "0000027419"}
        issuer_rows = [("TEAM", "Atlassian Corp", "0001650372"), ("TGT", "Target Corp", "0000027419")]

        result = _historical_ticker_directory(rows, directory, issuer_rows)

        self.assertNotIn("TEAM", result)
        self.assertEqual(result["TGT"], "0000027419")

    def test_backfill_security_repairs_current_named_issuer_on_wrong_cik(self):
        security = MarketSecurity(
            "TEAM", "ATLASSIAN CLS A CS", "0000027419", Decimal("1"), 1,
            date(2022, 6, 30), "us_nasdaq100",
        )

        result = _verified_backfill_security(
            security, date(2022, 6, 30), {"TEAM": "0001650372"},
            {"0001650372": ["Atlassian Corp"]}, lambda cik, reference: True,
        )

        self.assertEqual(result.cik, "0001650372")

    def test_backfill_name_match_ignores_historical_common_stock_marker(self):
        self.assertTrue(_backfill_name_matches("MARVELL TECH INC CMN", "Marvell Technology, Inc."))

    def test_backfill_security_preserves_historical_ticker_owner_with_different_name(self):
        security = MarketSecurity(
            "OLD", "Historical Holdings", "0000000001", Decimal("1"), 1,
            date(2018, 6, 30), "us_nasdaq100",
        )

        result = _verified_backfill_security(
            security, date(2018, 6, 30), {"OLD": "0000000002"},
            {"0000000002": ["Modern Software"]}, lambda cik, reference: True,
        )

        self.assertEqual(result.cik, "0000000001")

    def test_backfill_complete_predecessor_fact_can_replace_partial_successor_fact(self):
        complete = USFinancialFact(
            company_id="us:cik:new", fiscal_year=2018, fiscal_quarter=1,
            period_start=date(2017, 11, 1), period_end=date(2018, 1, 31),
            top_line=Decimal("100"), operating_income=Decimal("10"), net_income=Decimal("8"),
            source_filing_id="old-cik", filing_date=date(2018, 3, 1), is_pending=False,
        )
        partial = complete.with_changes(top_line=None, source_filing_id="new-cik", is_pending=True)

        self.assertEqual(_select_backfill_fact([partial, complete]), complete)

    def test_backfill_resolves_predecessor_cik_by_strict_name_when_old_ticker_is_missing(self):
        class Repository:
            def us_active_companies(self, _since_year):
                return [
                    {"company_name": "Walt Disney Co. (The)", "ticker": None, "cik": "0001001039"},
                    {"company_name": "WALT DISNEY COMPANY (THE)", "ticker": "DIS", "cik": "0001744489"},
                ]

        class Sec:
            def company_facts(self, cik):
                source = payload()
                if cik.zfill(10) == "0001001039":
                    for fact in source["facts"]["us-gaap"].values():
                        fact["units"]["USD"] = [
                            entry(fy=2018, fp="Q3", accn="old-q3", start="2018-04-01",
                                  end="2018-06-30", filed="2018-08-01", value="10")
                        ]
                return source

        member = USCompany(
            company_id="us:cik:0001744489", company_name="WALT DISNEY COMPANY (THE)", ticker="DIS",
            cik="0001744489", market_id="us_sp100", rank=1,
            market_cap=Decimal("1"), reference_date=date(2018, 6, 30),
        )
        pipeline = USEarningsBackfillPipeline(Repository(), Sec(), None)

        candidates = pipeline._historical_ticker_candidates(member, 2018, 2)

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].fully_complete)

    def test_legacy_oef_preserves_values_for_ranked_company_selection(self):
        row = "<TR><TD>Company {index}</TD><TD>1</TD><TD>1000</TD></TR>"
        document = (
            "Schedule of Investments iSHARES S&amp;P 100 ETF COMMON STOCKS"
            + "".join(row.format(index=index) for index in range(100))
            + "<TR><TD>Temporary Stub</TD><TD>1</TD><TD>1</TD></TR>"
            + "TOTAL INVESTMENTS"
        )
        result = extract_oef_holdings(document)
        self.assertEqual(len(result), 101)
        self.assertIn(("Temporary Stub", Decimal("1")), result)

    def test_initial_nport_without_tickers_keeps_issuer_and_weight(self):
        investments = "".join(
            f"<invstOrSec><name>Company {index}</name><identifiers><isin value='US{index:010}'/>"
            f"</identifiers><assetCat>EC</assetCat><pctVal>{100 - index / 100}</pctVal></invstOrSec>"
            for index in range(100)
        ) + "".join(
            f"<invstOrSec><name>Non-equity {index}</name><identifiers><cusip value='CASH{index}'/>"
            f"</identifiers><assetCat>STIV</assetCat><pctVal>5</pctVal></invstOrSec>"
            for index in range(2)
        )
        document = (
            "<edgarSubmission xmlns='http://www.sec.gov/edgar/nport'><formData><genInfo>"
            "<seriesName>iShares S&amp;P 100 ETF</seriesName></genInfo><invstOrSecs>"
            f"{investments}</invstOrSecs></formData></edgarSubmission>"
        )
        result = extract_oef_nport_holdings(document)
        self.assertEqual(len(result), 100)
        self.assertEqual(result[0], ("", "Company 0", Decimal("100.0")))

    def test_nport_keeps_issuer_when_provider_ticker_is_not_a_symbol(self):
        investments = "".join(
            f"<invstOrSec><name>Company {index}</name><identifiers><ticker value='T{index:03}'/>"
            f"</identifiers><assetCat>EC</assetCat><pctVal>1</pctVal></invstOrSec>"
            for index in range(99)
        )
        investments += (
            "<invstOrSec><name>Blackrock Inc</name><identifiers><ticker value='2481632'/>"
            "</identifiers><assetCat>EC</assetCat><pctVal>1</pctVal></invstOrSec>"
        )
        document = (
            "<edgarSubmission xmlns='http://www.sec.gov/edgar/nport'><formData><genInfo>"
            "<seriesName>iShares S&amp;P 100 ETF</seriesName></genInfo><invstOrSecs>"
            f"{investments}</invstOrSecs></formData></edgarSubmission>"
        )

        result = extract_oef_nport_holdings(document)

        self.assertIn(("", "Blackrock Inc", Decimal("1")), result)

    def test_oef_series_feed_selects_only_normal_report_window(self):
        atom = """<feed xmlns='http://www.w3.org/2005/Atom'>
          <entry><content><accession-number>right</accession-number><filing-date>2020-02-27</filing-date></content></entry>
          <entry><content><accession-number>early</accession-number><filing-date>2020-01-05</filing-date></content></entry>
          <entry><content><accession-number>late</accession-number><filing-date>2020-05-15</filing-date></content></entry>
        </feed>"""
        self.assertEqual(extract_oef_series_accessions(atom, date(2019, 12, 31)), ["right"])

    def test_legacy_series_feed_and_report_date_cover_prior_quarter_end(self):
        atom = """<feed xmlns='http://www.w3.org/2005/Atom'><entry><content>
            <accession-number>legacy-oef</accession-number>
        </content></entry></feed>"""
        self.assertEqual(extract_series_accessions(atom), {"legacy-oef"})
        self.assertEqual(legacy_report_date("2019-03-31", date(2019, 6, 30)), date(2019, 3, 31))
        self.assertIsNone(legacy_report_date("2019-07-31", date(2019, 6, 30)))


    def test_generic_nport_parser_accepts_qqq_series(self):
        investments = "".join(
            f"<invstOrSec><name>Company {index}</name><assetCat>EC</assetCat>"
            f"<pctVal>{100 - index / 100}</pctVal></invstOrSec>"
            for index in range(100)
        )
        document = (
            "<edgarSubmission xmlns='http://www.sec.gov/edgar/nport'><formData><genInfo>"
            "<seriesName>Invesco QQQ Trust, Series 1</seriesName></genInfo><invstOrSecs>"
            f"{investments}</invstOrSecs></formData></edgarSubmission>"
        )
        self.assertEqual(len(extract_nport_equity_holdings(document, r"Invesco\s+QQQ\s+Trust")), 100)

    def test_nasdaq_uses_qqq_filing_when_historical_api_is_empty(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        rows = [
            (f"T{index:03}", f"Company {index}", Decimal(100 - index))
            for index in range(100)
        ]
        directory = {
            ticker: str(index + 1).zfill(10)
            for index, (ticker, _, _) in enumerate(rows)
        }
        client = USIndexConstituentClient(FakeSec())
        client._json = lambda *args, **kwargs: {"aaData": []}
        client._qqq_nport_rows = lambda reference_date: rows

        result = client.nasdaq100(date(2024, 6, 30), directory)

        self.assertEqual(len(result), 100)

    def test_nasdaq_uses_nearest_trading_day_before_calendar_quarter_end(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        source = [
            {"Symbol": f"T{index:03}", "Name": f"Company {index}"}
            for index in range(100)
        ]
        directory = {f"T{index:03}": str(index + 1).zfill(10) for index in range(100)}
        requested: list[str] = []
        client = USIndexConstituentClient(FakeSec())

        def response(*args, **kwargs):
            requested.append(kwargs["data"]["tradeDate"])
            return {"aaData": source if kwargs["data"]["tradeDate"] == "2019-06-28" else []}

        client._json = response
        client._qqq_nport_rows = lambda reference_date: self.fail("official trading-day data should win")

        result = client.nasdaq100(date(2019, 6, 30), directory)

        self.assertEqual(len(result), 100)
        self.assertEqual(requested, ["2019-06-30", "2019-06-29", "2019-06-28"])

    def test_nasdaq_accepts_later_subset_after_temporary_quarter_end_overflow(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        prior = [
            {"Symbol": f"T{index:03}", "Name": f"Company {index}"}
            for index in range(101)
        ]
        later = prior[:100]
        directory = {f"T{index:03}": str(index + 1).zfill(10) for index in range(101)}
        requested: list[str] = []
        client = USIndexConstituentClient(FakeSec())

        def response(*args, **kwargs):
            trading_date = kwargs["data"]["tradeDate"]
            requested.append(trading_date)
            if trading_date == "2017-12-29":
                return {"aaData": prior}
            if trading_date == "2018-01-02":
                return {"aaData": later}
            return {"aaData": []}

        client._json = response
        client._qqq_nport_rows = lambda reference_date: None

        result = client.nasdaq100(date(2017, 12, 31), directory)

        self.assertEqual(len(result), 100)
        self.assertNotIn("T100", {item.ticker for item in result})
        self.assertEqual(requested, ["2017-12-31", "2017-12-30", "2017-12-29", "2018-01-01", "2018-01-02"])

    def test_nasdaq_rejects_later_snapshot_that_introduces_a_company(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        prior = [
            {"Symbol": f"T{index:03}", "Name": f"Company {index}"}
            for index in range(101)
        ]
        later = prior[:99] + [{"Symbol": "NEW", "Name": "New Company"}]
        directory = {f"T{index:03}": str(index + 1).zfill(10) for index in range(101)}
        directory["NEW"] = "0000009999"
        client = USIndexConstituentClient(FakeSec())

        def response(*args, **kwargs):
            trading_date = kwargs["data"]["tradeDate"]
            if trading_date == "2017-12-29":
                return {"aaData": prior}
            if trading_date == "2018-01-02":
                return {"aaData": later}
            return {"aaData": []}

        client._json = response
        client._qqq_nport_rows = lambda reference_date: None

        with self.assertRaisesRegex(ProviderError, "could not select 100 companies"):
            client.nasdaq100(date(2017, 12, 31), directory)

    def test_historical_ticker_reuse_resolves_the_period_issuer(self):
        old_cik = "0001570585"
        new_cik = "0001712184"
        regular = [
            (f"T{index:03}", f"Company {index}", str(index + 1).zfill(10))
            for index in range(99)
        ]

        class FakeSec:
            def company_ticker_rows(self):
                return regular + [("LILA", "Liberty Latin America Ltd.", new_cik)]

        client = USIndexConstituentClient(FakeSec())
        client._cik_for_name = lambda name, reference_date=None: None
        client._cik_for_ticker = lambda ticker, reference_date=None: old_cik if ticker == "LILA" else None
        directory = {ticker: cik for ticker, _, cik in regular}
        directory["LILA"] = new_cik
        rows = [(ticker, name, None) for ticker, name, _ in regular]
        rows.append(("LILA", "LIBERTY LILAC CL A", None))

        result = client._securities("us_nasdaq100", date(2017, 12, 31), rows, directory)

        self.assertEqual(len(result), 100)
        self.assertEqual(next(item.cik for item in result if item.ticker == "LILA"), old_cik)

    def test_historical_ticker_search_excludes_an_issuer_created_too_late(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        client = USIndexConstituentClient(FakeSec())
        client._json = lambda *args, **kwargs: {
            "hits": {"hits": [
                {"_source": {
                    "file_date": "2018-02-14",
                    "display_names": ["Liberty Latin America Ltd. (LILA, LILAK) (CIK 0001712184)"],
                    "ciks": ["0001712184"],
                }},
                {"_source": {
                    "file_date": "2017-02-16",
                    "display_names": ["Liberty Global plc (LBTYA, LBTYK) (CIK 0001570585)"],
                    "ciks": ["0001570585"],
                }},
            ]}
        }

        self.assertEqual(client._cik_for_ticker("LILA", date(2017, 9, 30)), "0001570585")
        self.assertEqual(client._cik_for_ticker("LILA", date(2017, 12, 31)), "0001712184")

    def test_historical_name_search_expands_ordinal_and_source_typo(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        queries: list[str] = []
        client = USIndexConstituentClient(FakeSec())

        def response(*args, **kwargs):
            query = kwargs["params"]["q"]
            queries.append(query)
            if query == "TWENTY FIRST CENTURY FOX A":
                return {"hits": {"hits": [{"_source": {
                    "display_names": ["TWENTY-FIRST CENTURY FOX, INC. (CIK 0001308161)"],
                }}]}}
            return {"hits": {"hits": []}}

        client._json = response

        self.assertEqual(client._cik_for_name("21ST CENTRY FOX A CM", date(2016, 3, 31)), "0001308161")
        self.assertIn("TWENTY FIRST CENTURY FOX A", queries)

    def test_historical_name_search_recovers_period_ticker(self):
        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return []

        client = USIndexConstituentClient(FakeSec())
        client._json = lambda *args, **kwargs: {
            "hits": {"hits": [{"_source": {"display_names": [
                "EXXON MOBIL CORP (XOM) (CIK 0000034088)",
            ]}}]}
        }

        cik = client._cik_for_name("Exxon Mobil Corp.", date(2022, 6, 30))
        rows = [("", "Exxon Mobil Corp.", Decimal("1"))] + [
            (f"T{index:03}", f"Company {index}", Decimal("1")) for index in range(99)
        ]
        directory = {f"T{index:03}": str(index + 1).zfill(10) for index in range(99)}
        client._cik_for_name = lambda name, reference_date=None: cik if name.startswith("Exxon") else directory.get(name.replace("Company ", "T"))

        result = client._securities("us_sp100", date(2022, 6, 30), rows, directory)

        self.assertEqual(cik, "0000034088")
        self.assertEqual(next(item.ticker for item in result if item.cik == cik), "XOM")

    def test_company_selection_aggregates_share_classes_and_keeps_top_100(self):
        class FakeSec:
            def company_ticker_rows(self):
                return []

        rows = [
            (f"T{index:03}", f"Company {index}", Decimal(1000 - index))
            for index in range(101)
        ]
        rows.append(("T000B", "Company 0 Class B", Decimal("10")))
        directory = {ticker: str(index + 1).zfill(10) for index, (ticker, _, _) in enumerate(rows[:-1])}
        directory["T000B"] = directory["T000"]
        client = USIndexConstituentClient(FakeSec())
        result = client._securities("us_sp100", date(2016, 3, 31), rows, directory)
        self.assertEqual(len(result), 100)
        self.assertNotIn("T100", {item.ticker for item in result})

    def test_company_selection_recovers_symbol_when_nport_ticker_is_not_usable(self):
        class FakeSec:
            def company_ticker_rows(self):
                return [("BLK", "Blackrock Inc", "0002012383")]

            def company_facts(self, _):
                return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
                    entry(fy=2025, fp="Q1", accn="q1", start="2025-01-01", end="2025-03-31",
                          filed="2025-05-01", value="1")
                ]}}}}}

        rows = [("", "Blackrock Inc", Decimal("1"))] + [
            (f"T{index:03}", f"Company {index}", Decimal("1")) for index in range(99)
        ]
        directory = {"BLK": "0002012383", **{
            f"T{index:03}": str(index + 1).zfill(10) for index in range(99)
        }}

        result = USIndexConstituentClient(FakeSec())._securities(
            "us_sp100", date(2025, 3, 31), rows, directory
        )

        self.assertIn("BLK", {item.ticker for item in result})

    def test_same_name_note_without_companyfacts_does_not_replace_operating_issuer(self):
        wrong_cik = "0001340909"
        walmart_cik = "0000104169"
        regular = [
            (f"T{index:03}", f"Company {index}", str(index + 1).zfill(10))
            for index in range(99)
        ]

        class FakeSec:
            def company_ticker_rows(self):
                return regular + [
                    ("GJO", "Wal-Mart Stores Inc.", wrong_cik),
                    ("WMT", "Walmart Inc.", walmart_cik),
                ]

            def company_facts(self, cik):
                if cik == wrong_cik:
                    raise ProviderError("HTTP 404")
                return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
                    entry(fy=2016, fp="Q1", accn="q1", start="2016-01-01", end="2016-03-31",
                          filed="2016-05-01", value="1")
                ]}}}}}

        rows = [(ticker, name, Decimal("1")) for ticker, name, _ in regular]
        rows.append(("", "Wal-Mart Stores Inc.", Decimal("1")))
        directory = {ticker: cik for ticker, _, cik in regular}
        directory["GJO"] = wrong_cik
        directory["WMT"] = walmart_cik

        result = USIndexConstituentClient(FakeSec())._securities(
            "us_sp100", date(2016, 3, 31), rows, directory,
        )

        walmart = next(item for item in result if item.name == "Wal-Mart Stores Inc.")
        self.assertEqual(walmart.cik, walmart_cik)
        self.assertEqual(walmart.ticker, "WMT")

    def test_historical_issuer_name_allows_unambiguous_word_expansion(self):
        self.assertEqual(_name_match_score("ALEXION PHARM INC", "ALEXION PHARMACEUTICALS INC"), 200)
        self.assertEqual(_name_match_score("DISCOVERY COMM A", "DISCOVERY COMMUNICATIONS INC"), 200)
        self.assertEqual(_name_match_score("CTRIP.COM INTL LTD", "CTRIP.COM INTERNATIONAL LTD"), 300)
        self.assertEqual(_name_match_score("VIACOM INC CL B", "VIACOM INC"), 100)
        self.assertEqual(_name_match_score("Lowe's Cos Inc", "LOWES COMPANIES INC"), 100)
        self.assertEqual(_name_match_score("Eli Lilly and Co", "ELI LILLY & Co"), 200)
        self.assertEqual(_name_match_score("Alphabet Inc., Class C, NVS (a)", "Alphabet Inc"), 100)
        self.assertEqual(
            _name_match_score("Twenty-First Century Fox Inc., Class A, NVS", "TWENTY-FIRST CENTURY FOX, INC."),
            400,
        )
        self.assertGreater(
            _name_match_score("WHOLE FOODS MARKET", "WHOLE FOODS MARKET INC"),
            _name_match_score("WHOLE FOODS MARKET", "WHOLE FOODS MARKET CALIFORNIA INC"),
        )
        self.assertEqual(_name_match_score("EI DU PONT DE NEMOURS", "SYNGENTA AG"), 0)
        self.assertGreater(_name_match_score("DU PONT DE NEMOURS", "DUPONT E I DE NEMOURS & CO"), 0)
        self.assertEqual(_name_match_score("ABC HOLDINGS", "ABC BANK CORPORATION"), 0)
        self.assertEqual(_name_match_score("MARRIOT INT CL A", "Marriott International Inc."), 200)
        self.assertEqual(_name_match_score("VODAFONE GRP PLC ADS", "Vodafone Group plc"), 200)
        self.assertEqual(_name_match_score("Philip Morris International In", "Philip Morris International Inc."), 300)
        self.assertEqual(_name_match_score("21ST CENTRY FOX A CM", "Twenty-First Century Fox Inc."), 299)

    def test_legacy_sec_names_and_encoding_are_normalized(self):
        self.assertEqual(_decode_filing("Lowe’s".encode("windows-1252")), "Lowe’s")
        self.assertEqual(_normal_name("KINDER MORGAN INC./DE"), _normal_name("Kinder Morgan Inc."))
        self.assertEqual(_normal_name("US BANCORP\\DE\\"), _normal_name("U.S. Bancorp"))
        self.assertEqual(_normal_name("Allergan PLC a"), _normal_name("Allergan PLC"))
        self.assertEqual(_normal_name("Celgene Corp. a,b"), _normal_name("Celgene Corp."))
        self.assertEqual(_normal_name("NetEase, Inc., ADR (China)"), _normal_name("NetEase Inc"))
        self.assertEqual(
            _normal_name("ASML Holding N.V., New York Shares (Netherlands)"),
            _normal_name("ASML Holding N.V."),
        )

    def test_nport_preserves_weights_for_ranked_company_selection(self):
        row = """
            Item C.1. Identification of investment
            Name of issuer <div class='fakeBox'>Normal {index}<span></span>
            Ticker (if ISIN is not available) <div class='fakeBox'>N{index:03}<span></span>
            Percentage value compared to net assets <div class='fakeBox'>0.100<span></span>
        """
        document = "Name of Series iShares S&amp;P 100 ETF" + "".join(row.format(index=index) for index in range(100))
        document += """
            Item C.1. Identification of investment
            Name of issuer <div class='fakeBox'>Temporary Stub<span></span>
            Ticker (if ISIN is not available) <div class='fakeBox'>TMPV<span></span>
            Percentage value compared to net assets <div class='fakeBox'>0.001<span></span>
        """
        result = extract_oef_nport_holdings(document)
        self.assertEqual(len(result), 101)
        self.assertIn(("TMPV", "Temporary Stub", Decimal("0.001")), result)

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

    def test_financial_company_uses_pretax_income_when_operating_income_is_absent(self):
        source = payload()
        facts = source["facts"]["us-gaap"]
        facts["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"] = facts.pop("OperatingIncomeLoss")

        result = extract_new_sec_facts("us:cik:financial", source, {"q2"})[0]

        self.assertEqual(result.operating_income, Decimal("20"))
        self.assertFalse(result.is_pending)

    def test_reported_operating_income_has_priority_over_pretax_income(self):
        source = payload()
        source["facts"]["us-gaap"]["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"] = {
            "units": {"USD": [entry(fy=2026, fp="Q2", accn="q2", start="2025-05-01", end="2025-07-31", filed="2025-08-20", value="200")]}
        }

        result = extract_new_sec_facts("us:cik:financial", source, {"q2"})[0]

        self.assertEqual(result.operating_income, Decimal("20"))

    def test_financial_company_accepts_legacy_total_pretax_income(self):
        source = payload()
        facts = source["facts"]["us-gaap"]
        facts["IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"] = facts.pop("OperatingIncomeLoss")

        result = extract_new_sec_facts("us:cik:legacy-financial", source, {"q3"})[0]

        self.assertEqual(result.operating_income, Decimal("30"))
        self.assertFalse(result.is_pending)

    def test_q4_uses_one_operating_income_basis_for_the_whole_fiscal_year(self):
        source = payload()
        operating_rows = source["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"]
        source["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"] = [row for row in operating_rows if row["fp"] == "FY"]
        source["facts"]["us-gaap"]["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"] = {
            "units": {"USD": [
                entry(fy=2026, fp="Q1", accn="q1", start="2025-02-01", end="2025-04-30", filed="2025-05-20", value="15"),
                entry(fy=2026, fp="Q2", accn="q2", start="2025-05-01", end="2025-07-31", filed="2025-08-20", value="25"),
                entry(fy=2026, fp="Q3", accn="q3", start="2025-08-01", end="2025-10-31", filed="2025-11-20", value="35"),
                entry(fy=2026, fp="FY", accn="fy", start="2025-02-01", end="2026-01-31", filed="2026-03-20", value="150"),
            ]}
        }

        result = extract_new_sec_facts("us:cik:financial", source, {"fy"})[0]

        self.assertEqual(result.operating_income, Decimal("75"))

    def test_fiscal_year_end_maps_to_actual_calendar_chart_quarter(self):
        fact = extract_new_sec_facts("us:cik:1", payload(), {"fy"})[0]
        self.assertEqual(fact.period_end, date(2026, 1, 31))
        self.assertEqual(market_period(fact.period_end), (2026, 1))
        row = fact.db_row()
        self.assertEqual((row["market_year"], row["market_quarter"]), (2026, 1))

    def test_comparative_sec_fy_labels_do_not_collide_across_physical_years(self):
        source = payload()
        for metric in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][metric]["units"]["USD"].extend([
                entry(fy=2026, fp="Q1", accn="q1-old", start="2025-02-01", end="2025-04-30", filed="2025-06-01", value="10"),
                entry(fy=2026, fp="Q1", accn="q1-new", start="2026-02-01", end="2026-04-30", filed="2026-06-01", value="20"),
                entry(fy=2025, fp="FY", accn="fy-old", start="2024-02-01", end="2025-01-31", filed="2025-03-01", value="40"),
                entry(fy=2026, fp="FY", accn="fy-new", start="2025-02-01", end="2026-01-31", filed="2026-03-01", value="80"),
                entry(fy=2027, fp="FY", accn="fy-next", start="2026-02-01", end="2027-01-31", filed="2027-03-01", value="120"),
            ])
        facts = extract_new_sec_facts("us:cik:comparative", source, {"q1-old", "q1-new"})
        physical = {(fact.period_end, fact.fiscal_year, fact.fiscal_quarter) for fact in facts}
        self.assertIn((date(2025, 4, 30), 2026, 1), physical)
        self.assertIn((date(2026, 4, 30), 2027, 1), physical)

    def test_later_filing_comparatives_receive_their_physical_fiscal_year_before_selection(self):
        source = payload()
        for metric in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][metric]["units"]["USD"] = [
                entry(fy=2018, fp="FY", accn="fy-2018", start="2018-01-01",
                      end="2018-12-31", filed="2019-02-01", value="400"),
                entry(fy=2019, fp="FY", accn="fy-2019", start="2019-01-01",
                      end="2019-12-31", filed="2020-02-01", value="440"),
                entry(fy=2020, fp="FY", accn="fy-2020", start="2020-01-01",
                      end="2020-12-31", filed="2021-02-01", value="480"),
                entry(fy=2020, fp="Q1", accn="q1-2020", start="2019-01-01",
                      end="2019-03-31", filed="2020-05-01", value="90"),
                entry(fy=2020, fp="Q1", accn="q1-2020", start="2020-01-01",
                      end="2020-03-31", filed="2020-05-01", value="100"),
            ]

        facts = extract_new_sec_facts("us:cik:late-comparative", source, {"q1-2020"})
        physical = {(fact.period_end, fact.fiscal_year, fact.fiscal_quarter) for fact in facts}

        self.assertIn((date(2019, 3, 31), 2019, 1), physical)
        self.assertIn((date(2020, 3, 31), 2020, 1), physical)

    def test_latest_quarter_after_last_annual_end_advances_fiscal_year(self):
        source = payload()
        for metric in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][metric]["units"]["USD"] = [
                entry(fy=2026, fp="FY", accn="fy-2026", start="2025-02-01",
                      end="2026-01-31", filed="2026-03-01", value="80"),
                entry(fy=2026, fp="Q1", accn="latest-q1", start="2026-02-01",
                      end="2026-04-30", filed="2026-06-01", value="20"),
            ]

        fact = next(
            fact for fact in extract_new_sec_facts("us:cik:latest", source, {"latest-q1"})
            if fact.period_end == date(2026, 4, 30)
        )

        self.assertEqual((fact.fiscal_year, fact.fiscal_quarter), (2027, 1))

    def test_two_physical_year_ends_in_one_calendar_year_keep_distinct_fiscal_keys(self):
        source = payload()
        for metric in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][metric]["units"]["USD"].extend([
                entry(fy=2023, fp="Q1", accn="q1-old", start="2022-01-03", end="2022-04-03", filed="2022-05-01", value="10"),
                entry(fy=2023, fp="Q1", accn="q1-new", start="2023-01-02", end="2023-04-02", filed="2023-05-01", value="20"),
                entry(fy=2021, fp="FY", accn="fy-2021", start="2021-01-04", end="2022-01-02", filed="2022-02-01", value="40"),
                entry(fy=2022, fp="FY", accn="fy-2022", start="2022-01-03", end="2023-01-01", filed="2023-02-01", value="80"),
                entry(fy=2023, fp="FY", accn="fy-2023", start="2023-01-02", end="2023-12-31", filed="2024-02-01", value="120"),
            ])
        facts = extract_new_sec_facts("us:cik:week-transition", source, {
            "q1-old", "q1-new", "fy-2021", "fy-2022", "fy-2023",
        })
        physical = {(fact.period_end, fact.fiscal_year, fact.fiscal_quarter) for fact in facts}

        self.assertIn((date(2022, 4, 3), 2022, 1), physical)
        self.assertIn((date(2023, 4, 2), 2023, 1), physical)

    def test_week_calendar_close_just_after_boundary_stays_in_prior_market_quarter(self):
        self.assertEqual(market_period(date(2017, 4, 1)), (2017, 1))
        self.assertEqual(market_period(date(2021, 1, 3)), (2020, 4))
        self.assertEqual(market_period(date(2017, 4, 8)), (2017, 2))

    def test_q4_prefers_direct_three_month_fact_inside_annual_filing(self):
        source = payload()
        source["facts"]["us-gaap"]["Revenues"]["units"]["USD"].append(
            entry(fy=2026, fp="FY", accn="fy", start="2025-11-01", end="2026-01-31", filed="2026-03-20", value="410")
        )

        fact = extract_new_sec_facts("us:cik:direct-q4", source, {"fy"})[0]

        self.assertEqual(fact.top_line, Decimal("410"))

    def test_backfill_q4_rejects_comparative_short_period_inside_annual_filing(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][tag]["units"]["USD"].append(
                entry(
                    fy=2026, fp="FY", accn="fy", start="2025-05-01",
                    end="2025-07-31", filed="2026-03-20", value="999",
                )
            )

        fact = next(
            item for item in extract_new_sec_facts(
                "us:cik:strict-q4", source, {"fy"}, strict_annual_direct=True,
            )
            if item.period_end == date(2026, 1, 31)
        )

        self.assertEqual(fact.top_line, Decimal("400"))
        self.assertEqual(fact.operating_income, Decimal("40"))
        self.assertEqual(fact.net_income, Decimal("32"))

    def test_historical_ten_k_q4_label_is_treated_as_an_annual_context(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            rows = source["facts"]["us-gaap"][tag]["units"]["USD"]
            annual = next(row for row in rows if row["fp"] == "FY")
            annual["fp"] = "Q4"
            rows.append({
                **annual, "start": "2025-11-01", "val": "410",
            })

        fact = next(
            fact for fact in extract_new_sec_facts("us:cik:q4-label", source, {"fy"})
            if fact.fiscal_quarter == 4
        )

        self.assertEqual(fact.period_end, date(2026, 1, 31))
        self.assertEqual(fact.top_line, Decimal("410"))

    def test_fy_label_inside_quarterly_filing_is_inferred_from_physical_period(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            row = entry(
                fy=2026, fp="FY", accn="q1-comparative", start="2026-02-01",
                end="2026-04-30", filed="2026-05-20", value="100",
            )
            row["form"] = "10-Q"
            source["facts"]["us-gaap"][tag]["units"]["USD"].append(row)

        facts = extract_new_sec_facts("us:cik:comparative-fy", source, {"q1-comparative"})

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].fiscal_quarter, 1)
        self.assertEqual(facts[0].period_end, date(2026, 4, 30))

    def test_incorrect_q1_label_is_normalized_to_second_physical_quarter(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][tag]["units"]["USD"] = [
                entry(fy=2025, fp="FY", accn="annual", start="2024-10-01", end="2025-09-30", filed="2025-11-20", value="400"),
                entry(fy=2026, fp="Q1", accn="actual-q1", start="2025-10-01", end="2025-12-31", filed="2026-02-01", value="90"),
                entry(fy=2026, fp="Q1", accn="mislabelled-q2", start="2026-01-01", end="2026-03-31", filed="2026-05-01", value="100"),
            ]

        facts = extract_new_sec_facts("us:cik:mislabelled-q1", source, {"mislabelled-q2"})

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].fiscal_quarter, 2)
        self.assertEqual(facts[0].period_end, date(2026, 3, 31))

    def test_backfill_prefers_complete_fact_over_later_malformed_comparative(self):
        complete = USFinancialFact(
            company_id="us:cik:1", fiscal_year=2026, fiscal_quarter=1,
            period_start=date(2025, 10, 1), period_end=date(2025, 12, 31),
            top_line=Decimal("100"), operating_income=Decimal("10"), net_income=Decimal("8"),
            source_filing_id="complete", filing_date=date(2026, 2, 1), is_pending=False,
        )
        later_incomplete = complete.with_changes(
            fiscal_quarter=3, top_line=None, source_filing_id="comparative",
            filing_date=date(2026, 8, 1), is_pending=True,
        )

        self.assertEqual(_select_backfill_fact([complete, later_incomplete]), complete)

    def test_ten_q_comparison_ending_on_fiscal_year_end_does_not_create_a_quarter(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][tag]["units"]["USD"] = [
                entry(fy=2025, fp="Q3", accn="actual-q3", start="2025-07-01", end="2025-09-30", filed="2025-11-01", value="90"),
                entry(fy=2025, fp="FY", accn="annual", start="2025-01-01", end="2025-12-31", filed="2026-02-01", value="400"),
                entry(fy=2026, fp="Q1", accn="q1-with-comparison", start="2025-10-01", end="2025-12-31", filed="2026-05-01", value="100"),
                entry(fy=2026, fp="Q1", accn="q1-with-comparison", start="2026-01-01", end="2026-03-31", filed="2026-05-01", value="110"),
            ]

        facts = extract_new_sec_facts(
            "us:cik:year-end-comparison", source,
            {"actual-q3", "annual", "q1-with-comparison"},
        )

        self.assertFalse(any(
            fact.period_end == date(2025, 12, 31) and fact.fiscal_quarter in {1, 2, 3}
            for fact in facts
        ))
        self.assertTrue(any(
            fact.period_end == date(2026, 3, 31) and fact.fiscal_quarter == 1
            for fact in facts
        ))

    def test_year_end_comparison_cannot_replace_real_prior_quarter_value(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss", "NetIncomeLoss"):
            source["facts"]["us-gaap"][tag]["units"]["USD"] = [
                entry(fy=2017, fp="FY", accn="fy-2017", start="2016-09-26",
                      end="2017-09-24", filed="2017-11-01", value="300"),
                entry(fy=2018, fp="FY", accn="fy-2018", start="2017-09-25",
                      end="2018-09-30", filed="2018-11-01", value="400"),
                entry(fy=2019, fp="Q1", accn="next-q1", start="2018-10-01",
                      end="2018-12-30", filed="2019-01-30", value="110"),
                entry(fy=2019, fp="Q1", accn="next-q1", start="2018-03-26",
                      end="2018-06-24", filed="2019-01-30", value="90"),
                entry(fy=2019, fp="Q1", accn="next-q1", start="2018-06-25",
                      end="2018-09-30", filed="2019-01-30", value="95"),
            ]

        facts = extract_new_sec_facts("us:cik:year-end-collision", source, {"next-q1"})

        self.assertTrue(any(
            fact.period_end == date(2018, 6, 24) and fact.fiscal_quarter == 3
            for fact in facts
        ))
        self.assertFalse(any(
            fact.period_end == date(2018, 9, 30) and fact.fiscal_quarter == 3
            for fact in facts
        ))

    def test_q4_can_subtract_compatible_metric_aliases_across_filings(self):
        source = payload()
        net_rows = source["facts"]["us-gaap"].pop("NetIncomeLoss")["units"]["USD"]
        source["facts"]["us-gaap"]["NetIncomeLoss"] = {
            "units": {"USD": [row for row in net_rows if row["fp"] != "FY"]}
        }
        source["facts"]["us-gaap"]["ProfitLoss"] = {
            "units": {"USD": [row for row in net_rows if row["fp"] == "FY"]}
        }

        fact = extract_new_sec_facts("us:cik:cross-basis-q4", source, {"fy"})[0]

        self.assertEqual(fact.net_income, Decimal("32"))

    def test_quarters_are_derived_from_fiscal_ytd_when_standalone_facts_are_absent(self):
        source = payload()
        cumulative_values = {
            "Revenues": ("300", "600"),
            "OperatingIncomeLoss": ("30", "60"),
            "NetIncomeLoss": ("24", "48"),
        }
        for tag, (q2_ytd, q3_ytd) in cumulative_values.items():
            rows = source["facts"]["us-gaap"][tag]["units"]["USD"]
            source["facts"]["us-gaap"][tag]["units"]["USD"] = [
                row for row in rows if row["fp"] not in {"Q2", "Q3"}
            ] + [
                # Comparative SEC facts can carry a different ``fy`` label
                # from the annual filing despite representing the same year.
                entry(fy=2025, fp="Q2", accn="q2", start="2025-02-01", end="2025-07-31", filed="2025-08-20", value=q2_ytd),
                entry(fy=2025, fp="Q3", accn="q3", start="2025-02-01", end="2025-10-31", filed="2025-11-20", value=q3_ytd),
            ]

        facts = {
            fact.fiscal_quarter: fact
            for fact in extract_new_sec_facts("us:cik:ytd", source, {"q2", "q3", "fy"})
        }

        self.assertEqual(facts[2].top_line, Decimal("200"))
        self.assertEqual(facts[3].top_line, Decimal("300"))
        self.assertEqual(facts[4].top_line, Decimal("400"))
        self.assertEqual(facts[4].operating_income, Decimal("40"))
        self.assertEqual(facts[4].net_income, Decimal("32"))
        self.assertTrue(all(fact.fully_complete for fact in facts.values()))

    def test_q1_is_derived_from_q2_ytd_when_direct_q1_metric_is_omitted(self):
        source = payload()
        for tag in ("Revenues", "OperatingIncomeLoss"):
            source["facts"]["us-gaap"][tag]["units"]["USD"] = [
                entry(fy=2026, fp="Q2", accn="q2", start="2026-01-01",
                      end="2026-06-30", filed="2026-07-30", value="250"),
                entry(fy=2026, fp="Q2", accn="q2", start="2026-04-01",
                      end="2026-06-30", filed="2026-07-30", value="130"),
            ]
        source["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
            entry(fy=2025, fp="FY", accn="fy-2025", start="2025-01-01",
                  end="2025-12-31", filed="2026-02-15", value="40"),
            entry(fy=2026, fp="Q2", accn="q2", start="2026-01-01",
                  end="2026-03-31", filed="2026-07-30", value="20"),
            entry(fy=2026, fp="Q2", accn="q2", start="2026-01-01",
                  end="2026-06-30", filed="2026-07-30", value="35"),
            entry(fy=2026, fp="Q2", accn="q2", start="2026-04-01",
                  end="2026-06-30", filed="2026-07-30", value="15"),
        ]

        q1 = next(
            fact for fact in extract_new_sec_facts("us:cik:late-q1", source, {"q2"})
            if fact.period_end == date(2026, 3, 31)
        )

        self.assertEqual(q1.top_line, Decimal("120"))
        self.assertEqual(q1.operating_income, Decimal("120"))
        self.assertEqual(q1.net_income, Decimal("20"))
        self.assertTrue(q1.fully_complete)

    def test_q4_stays_pending_when_any_prior_quarter_is_missing(self):
        source = payload()
        source["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] = [
            item for item in source["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] if item["fp"] != "Q3"
        ]
        fact = extract_new_sec_facts("us:cik:1", source, {"fy"})[0]
        self.assertIsNone(fact.net_income)
        self.assertTrue(fact.is_pending)

    def test_alternate_standard_tags_fill_revenue_and_common_stockholder_income(self):
        source = payload()
        facts = source["facts"]["us-gaap"]
        facts["RevenueFromContractWithCustomerIncludingAssessedTax"] = facts.pop("Revenues")
        facts["NetIncomeLossAvailableToCommonStockholdersBasic"] = facts.pop("NetIncomeLoss")

        fact = extract_new_sec_facts("us:cik:alternate", source, {"q2"})[0]

        self.assertEqual(fact.top_line, Decimal("200"))
        self.assertEqual(fact.net_income, Decimal("16"))
        self.assertFalse(fact.is_pending)

    def test_diluted_common_stockholder_income_is_valid_net_income_fallback(self):
        source = payload()
        net = source["facts"]["us-gaap"].pop("NetIncomeLoss")
        source["facts"]["us-gaap"]["NetIncomeLossAvailableToCommonStockholdersDiluted"] = net

        fact = extract_new_sec_facts("us:cik:diluted-net", source, {"fy"})[0]

        self.assertEqual(fact.net_income, Decimal("32"))
        self.assertFalse(fact.is_pending)

    def test_bank_composites_fill_revenue_and_pretax_income(self):
        source = payload()
        facts = source["facts"]["us-gaap"]
        revenues = facts.pop("Revenues")
        operating = facts.pop("OperatingIncomeLoss")
        facts["InterestIncomeExpenseNet"] = {
            "units": {"USD": [{**row, "val": Decimal(str(row["val"])) * Decimal("0.4")} for row in revenues["units"]["USD"]]}
        }
        facts["NoninterestIncome"] = {
            "units": {"USD": [{**row, "val": Decimal(str(row["val"])) * Decimal("0.6")} for row in revenues["units"]["USD"]]}
        }
        facts["IncomeTaxExpenseBenefit"] = {
            "units": {"USD": [{**row, "val": Decimal(str(row["val"])) * Decimal("0.25")} for row in operating["units"]["USD"]]}
        }
        facts["ProfitLoss"] = {
            "units": {"USD": [{**row, "val": Decimal(str(row["val"])) * Decimal("0.75")} for row in operating["units"]["USD"]]}
        }

        fact = extract_new_sec_facts("us:cik:bank", source, {"q3"})[0]

        self.assertEqual(fact.top_line, Decimal("300"))
        self.assertEqual(fact.operating_income, Decimal("30"))
        self.assertFalse(fact.is_pending)

    def test_income_statement_identity_fills_missing_top_line(self):
        source = payload()
        facts = source["facts"]["us-gaap"]
        revenues = facts.pop("Revenues")
        operating = facts["OperatingIncomeLoss"]
        facts["OperatingExpenses"] = {
            "units": {"USD": [
                {**row, "val": Decimal(str(row["val"])) - Decimal(str(op["val"]))}
                for row, op in zip(revenues["units"]["USD"], operating["units"]["USD"])
            ]}
        }

        fact = extract_new_sec_facts("us:cik:identity", source, {"q2"})[0]

        self.assertEqual(fact.top_line, Decimal("200"))
        self.assertFalse(fact.is_pending)

    def test_historical_constituent_rejects_successor_cik_without_period_coverage(self):
        successor_cik = "0002115436"
        historical_cik = "0000034088"

        class FakeSec:
            user_agent = "test"

            def company_ticker_rows(self):
                return [("XOM", "ExxonMobil Holdings Corp", successor_cik)]

            def company_facts(self, cik):
                end = "2026-06-30" if cik == successor_cik else "2016-03-31"
                return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
                    entry(fy=int(end[:4]), fp="Q1", accn="q1", start=end[:4] + "-01-01", end=end, filed=end, value="1")
                ]}}}}}

        client = USIndexConstituentClient(FakeSec())
        client._cik_for_name = lambda *_args, **_kwargs: historical_cik
        client._cik_for_ticker = lambda *_args, **_kwargs: successor_cik
        rows = [("XOM", "Exxon Mobil Corp", Decimal("100"))] + [
            (f"T{index:03}", f"Company {index}", Decimal(99 - index)) for index in range(99)
        ]
        directory = {"XOM": successor_cik, **{
            f"T{index:03}": str(index + 1).zfill(10) for index in range(99)
        }}
        original_coverage = client._has_company_facts_for_reference
        client._has_company_facts_for_reference = lambda cik, reference: (
            original_coverage(cik, reference) if cik in {successor_cik, historical_cik} else True
        )

        result = client._securities("us_sp100", date(2016, 3, 31), rows, directory)

        self.assertEqual(next(item.cik for item in result if item.ticker == "XOM"), historical_cik)

    def test_successor_comparatives_do_not_count_as_contemporaneous_cik_coverage(self):
        class FakeSec:
            def company_ticker_rows(self):
                return []

            def company_facts(self, _cik):
                return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
                    entry(fy=2016, fp="FY", accn="late", start="2016-01-01", end="2016-12-31",
                          filed="2020-02-01", value="1")
                ]}}}}}

        client = USIndexConstituentClient(FakeSec())

        self.assertFalse(client._has_company_facts_for_reference("0001744489", date(2016, 6, 30)))

    def test_name_search_uses_period_coverage_to_choose_historical_cik(self):
        successor_cik = "0001744489"
        historical_cik = "0001001039"

        class FakeSec:
            user_agent = "test"

        client = USIndexConstituentClient(FakeSec())
        client._json = lambda *_args, **_kwargs: {"hits": {"hits": [{"_source": {"display_names": [
            f"Walt Disney Co (DIS) (CIK {successor_cik})",
            f"WALT DISNEY CO/ (CIK {historical_cik})",
        ]}}]}}
        client._has_company_facts_for_reference = lambda cik, _reference_date: cik == historical_cik

        self.assertEqual(client._cik_for_name("Walt Disney Co. (The)", date(2018, 6, 30)), historical_cik)

    def test_date_bounded_sec_search_rejects_successor_without_historical_coverage(self):
        successor_cik = "0001744489"
        historical_cik = "0001001039"

        class FakeSec:
            def company_ticker_rows(self):
                return []

        client = USIndexConstituentClient(FakeSec())
        client._cik_for_name = lambda *_args, **_kwargs: successor_cik
        client._cik_for_ticker = lambda *_args, **_kwargs: historical_cik
        client._has_company_facts_for_reference = lambda cik, _reference_date: cik != successor_cik
        rows = [("DIS", "Walt Disney Co", Decimal("100"))] + [
            (f"T{index:03}", f"Company {index}", Decimal(99 - index)) for index in range(99)
        ]
        directory = {f"T{index:03}": str(index + 1).zfill(10) for index in range(99)}

        result = client._securities("us_nasdaq100", date(2018, 9, 30), rows, directory)

        self.assertEqual(next(item.cik for item in result if item.ticker == "DIS"), historical_cik)

    def test_unambiguous_stored_historical_ticker_resolves_delisted_issuer(self):
        historical_cik = "0001355096"

        class FakeSec:
            def company_ticker_rows(self):
                return []

        client = USIndexConstituentClient(FakeSec())
        client._cik_for_name = lambda *_args, **_kwargs: None
        client._cik_for_ticker = lambda *_args, **_kwargs: None
        client._has_company_facts_for_reference = lambda *_args, **_kwargs: True
        rows = [("QRTEA", "Qurate Retail Inc", Decimal("100"))] + [
            (f"T{index:03}", f"Company {index}", Decimal(99 - index)) for index in range(99)
        ]
        directory = {f"T{index:03}": str(index + 1).zfill(10) for index in range(99)}

        result = client._securities(
            "us_nasdaq100", date(2018, 9, 30), rows, directory,
            historical_directory={"QRTEA": historical_cik},
        )

        self.assertEqual(next(item.cik for item in result if item.ticker == "QRTEA"), historical_cik)


if __name__ == "__main__":
    unittest.main()

