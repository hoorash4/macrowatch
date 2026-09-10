"""Offline contracts for the shared persistence boundary and version policies."""
import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from earnings_common.repository import EarningsRepository, _json
from earnings_v2.repository import EarningsV2Repository, StoreError
from earnings_us.repository import USEarningsRepository


class RepositoryContractTests(unittest.TestCase):
    def test_serialization_preserves_decimal_dates_null_and_zero(self):
        self.assertEqual(_json({'rows': (Decimal('0'), None, date(2026, 6, 30))}),
                         {'rows': ['0', None, '2026-06-30']})

    def test_rpc_request_and_empty_response(self):
        for cls in (EarningsV2Repository,):
            with self.subTest(repository=cls.__module__):
                session = Mock()
                session.post.return_value.content = b''
                repository = cls('https://example.invalid/', ' key ', session=session)
                self.assertIsNone(repository.rpc('test', {'value': Decimal('1.25')}))
                session.post.assert_called_once_with(
                    'https://example.invalid/rest/v1/rpc/test',
                    headers={'apikey': 'key', 'Authorization': 'Bearer key', 'Content-Type': 'application/json'},
                    json={'value': '1.25'}, timeout=(5, 20))

    def test_automatic_and_repair_workflows_are_separate(self):
        automatic = (ROOT / '.github/workflows/earnings-us-automatic.yml').read_text(encoding='utf-8')
        repair = (ROOT / '.github/workflows/earnings-us-repair.yml').read_text(encoding='utf-8')
        self.assertIn('EARNINGS_WRITE_MODE: automatic', automatic)
        self.assertNotIn('retry_incomplete', automatic)
        self.assertNotIn('repair_cli', automatic)
        self.assertIn('workflow_dispatch:', repair)
        self.assertNotIn('schedule:', repair)
        self.assertIn('EARNINGS_WRITE_MODE: repair', repair)
        self.assertIn('earnings_us.repair_cli', repair)

    def test_source_identity_and_state_success_policy(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'automatic'}, clear=False):
            repository = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        repository.rpc = Mock(return_value=[])
        for status in ('ready', 'incomplete', 'failed'):
            repository.save_state('daily', status, {'quarter': 2})
            params = repository.rpc.call_args.args[1]
            self.assertEqual(params['p_source'], 'korea_v2')
            self.assertEqual(params['p_last_success_at'] is not None, status != 'failed')
        self.assertIsNone(repository.pipeline_state('daily'))
        self.assertEqual(repository.rpc.call_args.args[1]['p_source'], 'korea_v2')

        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'manual'}, clear=False):
            manual = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        manual.rpc = Mock(return_value=[])
        manual.save_state('2026Q2', 'ready', {})
        self.assertEqual(manual.rpc.call_args.args[1]['p_source'], 'korea_v2_manual')

    def test_period_deduplication_and_response_filtering(self):
        repository = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        repository.rpc = Mock(return_value=[None, {'id': 1}, 0])
        self.assertEqual(repository.company_periods(['b', 'a', 'b'], [(2026, 2), (2026, 2)]), [{'id': 1}])
        self.assertEqual(repository.rpc.call_args.args[1], {
            'p_company_ids': ['b', 'a'], 'p_periods': [{'fiscal_year': 2026, 'fiscal_quarter': 2}]})

    def test_policy_boundaries_and_shared_implementation(self):
        self.assertIs(EarningsV2Repository.rpc, EarningsRepository.rpc)
        self.assertTrue(hasattr(EarningsV2Repository, 'cached_kis_token'))
        for cls, error_type in ((EarningsV2Repository, StoreError),):
            session = Mock()
            session.post.side_effect = RuntimeError('transport failed')
            with self.assertRaises(error_type):
                cls('https://example.invalid', 'key', session=session).rpc('test', {})

    def test_automatic_mode_drops_historical_fact_and_market_writes(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'automatic'}, clear=False):
            repository = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        repository._automatic_period = Mock(return_value=(2026, 2))
        repository.rpc = Mock(return_value=1)

        company_rows = [
            {'company_id': 'old', 'market_year': 2026, 'market_quarter': 1},
            {'company_id': 'current', 'market_year': 2026, 'market_quarter': 2},
        ]
        self.assertEqual(repository.upsert_company_quarters(company_rows), 1)
        self.assertEqual(repository.rpc.call_args.args[0], 'earnings_v2_auto_v6_upsert_company_quarters')
        self.assertEqual(repository.rpc.call_args.args[1]['p_rows'], [company_rows[1]])

        market_rows = [
            {'market_id': 'old', 'market_year': 2025, 'market_quarter': 4},
            {'market_id': 'current', 'market_year': 2026, 'market_quarter': 2},
        ]
        self.assertEqual(repository.upsert_market_quarters(market_rows), 1)
        self.assertEqual(repository.rpc.call_args.args[0], 'earnings_v2_auto_v6_upsert_market_quarters')
        self.assertEqual(repository.rpc.call_args.args[1]['p_rows'], [market_rows[1]])

    def test_automatic_mode_blocks_historical_universe_and_period_state(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'automatic'}, clear=False):
            repository = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        repository._automatic_period = Mock(return_value=(2026, 2))
        repository.rpc = Mock(return_value=1)

        with self.assertRaises(StoreError):
            repository.replace_universe('kr_largecap', 2026, 1, [])
        with self.assertRaises(StoreError):
            repository.save_state('2025Q4', 'ready', {})
        repository.save_state('2026Q2', 'ready', {})
        self.assertEqual(repository.rpc.call_args.args[1]['p_operation'], '2026Q2')

        repository.replace_universe('kr_largecap', 2026, 2, [])
        self.assertEqual(repository.rpc.call_args.args[0], 'earnings_v2_auto_replace_universe')

    def test_automatic_mode_blocks_old_seasonal_window_updates(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'automatic'}, clear=False):
            repository = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        repository._automatic_period = Mock(return_value=(2026, 2))
        repository.rpc = Mock(return_value=1)
        rows = [
            {'entity_type': 'market', 'entity_id': 'kr_largecap', 'metric': 'operating_income',
             'fiscal_quarter': 1, 'sample_years': [2022, 2023, 2024, 2025, 2026], 'sample_values': [1, 2, 3, 4, 5]},
            {'entity_type': 'market', 'entity_id': 'kr_largecap', 'metric': 'operating_income',
             'fiscal_quarter': 2, 'sample_years': [2022, 2023, 2024, 2025, 2026], 'sample_values': [1, 2, 3, 4, 5]},
        ]
        self.assertEqual(repository.upsert_seasonal_windows(rows), 1)
        self.assertEqual(repository.rpc.call_args.args[1]['p_rows'], [rows[1]])

    def test_automatic_mode_uses_protected_fx_rpc(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'automatic'}, clear=False):
            repository = EarningsV2Repository('https://example.invalid', 'key', session=Mock())
        repository._automatic_period = Mock(return_value=(2026, 2))
        repository.rpc = Mock(return_value=1)
        row = {'fiscal_year': 2026, 'fiscal_quarter': 2, 'base_currency': 'USD', 'quote_currency': 'KRW'}
        self.assertEqual(repository.upsert_quarter_fx_rate(row), 1)
        self.assertEqual(repository.rpc.call_args.args[0], 'earnings_v2_auto_upsert_quarter_fx_rate')

    def test_us_automatic_state_cannot_impersonate_repair_or_backfill(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'automatic'}, clear=False):
            repository = USEarningsRepository('https://example.invalid', 'key', session=Mock())
        repository.rpc = Mock(return_value=[])
        for operation in ('retry_incomplete', 'backfill', 'backfill_range', 'universe_backfill'):
            with self.subTest(operation=operation):
                with self.assertRaises(StoreError):
                    repository.save_us_state(operation, 'ready', {})
        repository.save_us_state('daily_edgar', 'ready', {})
        self.assertEqual(repository.rpc.call_args.args[1]['p_source'], 'us_automatic')

    def test_us_repair_state_has_separate_source_identity(self):
        with patch.dict(os.environ, {'EARNINGS_WRITE_MODE': 'repair'}, clear=False):
            repository = USEarningsRepository('https://example.invalid', 'key', session=Mock())
        repository.rpc = Mock(return_value=[])
        repository.save_us_state('retry_incomplete', 'ready', {})
        self.assertEqual(repository.rpc.call_args.args[1]['p_source'], 'us_repair')
