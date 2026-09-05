from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from earnings_us.models import MarketSecurity, market_period
from earnings_us.backfill_cli import period_range
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

    def test_universe_backfill_periods_run_newest_to_oldest(self):
        periods = period_range(2026, 2, 2016, 1)
        self.assertEqual(periods[:3], [(2026, 2), (2026, 1), (2025, 4)])
        self.assertEqual(periods[-1], (2016, 1))
        self.assertEqual(len(periods), 42)

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

    def test_legacy_sec_names_and_encoding_are_normalized(self):
        self.assertEqual(_decode_filing("Lowe’s".encode("windows-1252")), "Lowe’s")
        self.assertEqual(_normal_name("KINDER MORGAN INC./DE"), _normal_name("Kinder Morgan Inc."))
        self.assertEqual(_normal_name("US BANCORP\\DE\\"), _normal_name("U.S. Bancorp"))
        self.assertEqual(_normal_name("Allergan PLC a"), _normal_name("Allergan PLC"))
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
