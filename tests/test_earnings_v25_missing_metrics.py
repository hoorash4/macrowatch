import json
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from types import SimpleNamespace
import unittest
from io import BytesIO
from zipfile import ZipFile

from earnings_v25.raw_dart_financials import _parse_document, _metric_for_label, parse_raw_filing_archive
from earnings_v25.legacy_dart_financials import _amount
from earnings_v25.providers import FinancialCompanyClient, FinancialCompanySnapshot
from earnings_v25.pipeline import KoreaEarningsV2Pipeline
from earnings_v25.models import FinancialFact
from datetime import date


class MissingMetricsTests(unittest.TestCase):
    def test_real_raw_tables(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/v25_missing_net_tables.json').read_text(encoding='utf-8'))
        for item in rows:
            year = int(item['receipt'][:4])
            code = '11014' if year == 2016 else '11012' if item['company'] == '지씨셀' else '11013'
            parsed = _parse_document(f'<p>{year}년 연결 손익계산서 (단위: 원)</p>' + item['tables'][0], code, year)
            self.assertTrue(parsed, item['company'])
            statement = parsed[0]
            if item['company'] == '콜마비앤에이치':
                self.assertEqual(statement.cumulative['net_income'], Decimal('24636760649'))
                self.assertEqual(statement.standalone['net_income'], Decimal('8358646388'))
            elif item['company'] == '지씨셀':
                self.assertEqual(statement.cumulative['net_income'], Decimal('927044703'))
                self.assertEqual(statement.standalone['net_income'], Decimal('937830614'))
            else:
                self.assertIsNone(statement.cumulative['net_income'])  # blank NCI is not zero

    def test_labels_and_signed_amount(self):
        for label in ('반기순 손실', '분기순 손실'):
            self.assertEqual(_metric_for_label(label), 'net_income')
        self.assertEqual(_metric_for_label('매출과지분법손익(영업수익)'), 'top_line')
        self.assertIsNone(_metric_for_label('당기순이익(손실)의 귀속'))
        self.assertIsNone(_metric_for_label('법인세비용차감전순이익'))
        self.assertEqual(_amount('-211'), Decimal('-211'))
        self.assertEqual(_amount('(211)'), Decimal('-211'))

    def test_summary_only_supplements_exact_matching_statement(self):
        rows = json.loads((Path(__file__).parent / 'fixtures/v25_missing_net_tables.json').read_text(encoding='utf-8'))
        detail = next(item['tables'][0] for item in rows if item['company'] == '헬릭스미스')
        for operating, expected in [('-2492136659', Decimal('-2210337128')), ('-2367451601', None)]:
            summary = ('<p>2018년 (단위: 원)</p><table>'
                       '<tr><td>매출액</td><td>473622130</td></tr>'
                       f'<tr><td>영업이익</td><td>{operating}</td></tr>'
                       '<tr><td>당기순이익</td><td>-2210337128</td></tr></table>')
            buffer = BytesIO()
            with ZipFile(buffer, 'w') as archive:
                archive.writestr('report.xml', summary + '<p>연결 손익계산서 (단위: 원)</p>' + detail)
            parsed = parse_raw_filing_archive(buffer.getvalue(), report_code='11013', fiscal_year=2018)
            self.assertEqual(parsed['CFS'].cumulative['net_income'], expected)

    def test_partial_sector_continues_to_common_without_changing_v2_path(self):
        client = FinancialCompanyClient('https://example.test', 'service', 'token', 'key')
        partial = FinancialCompanySnapshot('1234567890123', '11013', 'CFS', 'KRW', Decimal(100), Decimal(10), None)
        client._sector_quarter_financials = lambda *args: [partial]
        calls = []
        client._source_request = lambda *args, **kwargs: calls.append('common') or {'status': 'no_report'}
        self.assertEqual(client.quarter_financials(partial.crno, 2016, 1, '64992'), [partial])
        self.assertEqual(calls, [])
        self.assertEqual(client.quarter_financial_candidates(partial.crno, 2016, 1, '64992'), [partial])
        self.assertEqual(calls, ['common'])
        common = replace(partial, top_line_cumulative=None, operating_income_cumulative=None, net_income_cumulative=Decimal(5))
        fact = SimpleNamespace(consolidation_scope='CFS')
        result = KoreaEarningsV2Pipeline._financial_company_snapshot([partial, common], fact)
        self.assertEqual((result.top_line_cumulative, result.net_income_cumulative), (100, 5))

    def test_complete_consolidated_replaces_incomplete_separate_as_a_set(self):
        snapshot = FinancialCompanySnapshot('1234567890123', '11013', 'CFS', 'KRW', Decimal(100), Decimal(10), Decimal(5))
        pipeline = object.__new__(KoreaEarningsV2Pipeline)
        pipeline.financial_company = SimpleNamespace(quarter_financial_candidates=lambda *args, **kwargs: [snapshot])
        fact = FinancialFact(company_id='kr:test', fiscal_year=2016, fiscal_quarter=1,
            period_end=date(2016,3,31), top_line=None, operating_income=Decimal(2), net_income=Decimal(1),
            currency='KRW', consolidation_scope='OFS', source_filing_id='test', filing_date=date(2016,5,1), is_pending=True)
        result = pipeline._financial_company_missing_financials(SimpleNamespace(company_name='test'), fact, 2016, 1, None, snapshot.crno, '64992')
        self.assertEqual((result.top_line,result.operating_income,result.net_income), (100,10,5))
        self.assertEqual(result.consolidation_scope,'CFS')

    def test_opposite_scope_fills_only_missing_metric(self):
        snapshot = FinancialCompanySnapshot(
            '1234567890123', '11013', 'OFS', 'KRW', Decimal(100), Decimal(99), Decimal(88),
        )
        pipeline = object.__new__(KoreaEarningsV2Pipeline)
        pipeline.financial_company = SimpleNamespace(
            quarter_financial_candidates=lambda *args, **kwargs: [snapshot],
        )
        pipeline._progress = lambda *args, **kwargs: None
        fact = FinancialFact(
            company_id='kr:test', fiscal_year=2016, fiscal_quarter=1,
            period_end=date(2016, 3, 31), top_line=None,
            operating_income=Decimal(10), net_income=Decimal(5),
            currency='KRW', consolidation_scope='CFS', source_filing_id='open_dart:test',
            filing_date=date(2016, 5, 1), source='open_dart', is_pending=True,
        )

        result = pipeline._financial_company_missing_financials(
            SimpleNamespace(company_name='test'), fact, 2016, 1, None,
            snapshot.crno, '64992',
        )

        self.assertEqual((result.top_line, result.operating_income, result.net_income), (100, 10, 5))
        self.assertEqual(result.consolidation_scope, 'CFS')
        self.assertEqual(result.source, 'mixed')
        self.assertFalse(result.is_pending)


if __name__ == '__main__':
    unittest.main()
