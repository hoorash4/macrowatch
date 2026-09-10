"""Offline contracts for the shared persistence boundary and version policies."""
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from earnings_common.repository import EarningsRepository, _json
from earnings_v2.repository import EarningsV2Repository, StoreError


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

    def test_source_identity_and_state_success_policy(self):
        for cls, source in ((EarningsV2Repository, 'korea_v2'),):
            repository = cls('https://example.invalid', 'key', session=Mock())
            repository.rpc = Mock(return_value=[])
            for status in ('ready', 'incomplete', 'failed'):
                repository.save_state('daily', status, {'quarter': 2})
                params = repository.rpc.call_args.args[1]
                self.assertEqual(params['p_source'], source)
                self.assertEqual(params['p_last_success_at'] is not None, status != 'failed')
            self.assertIsNone(repository.pipeline_state('daily'))
            self.assertEqual(repository.rpc.call_args.args[1]['p_source'], source)

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
