import json
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from types import SimpleNamespace
import unittest

from earnings_v25.raw_dart_financials import _parse_document, _metric_for_label
from earnings_v25.legacy_dart_financials import _amount
from earnings_v25.providers import FinancialCompanyClient, FinancialCompanySnapshot
from earnings_v25.pipeline import KoreaEarningsV2Pipeline


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


if __name__ == '__main__':
    unittest.main()
