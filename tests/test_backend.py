from __future__ import annotations

import sys
import types
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# 계산 테스트에는 네트워크가 필요 없다. 로컬에 운영 의존성이 설치되지 않은
# 환경에서도 모듈을 불러올 수 있도록 import 자리만 제공한다.
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.Session = object
    sys.modules["requests"] = requests_stub
if "openpyxl" not in sys.modules:
    sys.modules["openpyxl"] = types.ModuleType("openpyxl")

import check_targets  # noqa: E402
import common  # noqa: E402
import em_stress_pipeline as em  # noqa: E402
import financial_stress_pipeline as us  # noqa: E402
import korea_stress_pipeline as kr  # noqa: E402


class TargetConditionTests(unittest.TestCase):
    def test_decimal_parser_handles_commas_and_parentheses(self) -> None:
        self.assertEqual(check_targets.parse_decimal("1,234.50"), Decimal("1234.50"))
        self.assertEqual(check_targets.parse_decimal("(12.5)"), Decimal("-12.5"))

    def test_crossing_conditions_use_previous_and_current_values(self) -> None:
        target = {"condition_type": "gte", "target_value": "10"}
        self.assertTrue(check_targets.condition_met(target, Decimal("9"), Decimal("11")))
        self.assertFalse(check_targets.condition_met(target, Decimal("11"), Decimal("12")))


class SharedCalculationTests(unittest.TestCase):
    def test_carry_forward_preserves_last_observed_value(self) -> None:
        periods = ["2026-01-02", "2026-01-09", "2026-01-16"]
        expected = {"2026-01-02": 1.0, "2026-01-09": 1.0, "2026-01-16": 3.0}
        self.assertEqual(us.carry_forward_values({periods[0]: 1.0, periods[2]: 3.0}, periods), expected)
        self.assertEqual(em.carry_forward({periods[0]: 1.0, periods[2]: 3.0}, periods), expected)

    def test_fixed_scores_are_not_capped_at_one_hundred(self) -> None:
        self.assertEqual(us.fixed_stress_score(38.0, "high_yield_oas_pct"), 200.0)
        self.assertEqual(em.score(30.0, 10.0, 20.0), 200.0)
        self.assertEqual(kr.score(30.0, 10.0, 20.0), 200.0)

    def test_completed_quarter_boundary(self) -> None:
        self.assertEqual(us.latest_completed_quarter_end(date(2026, 8, 26)), date(2026, 6, 30))
        self.assertEqual(us.latest_completed_quarter_end(date(2026, 1, 2)), date(2025, 12, 31))


class CommonClientTests(unittest.TestCase):
    def test_fred_client_preserves_query_contract(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {"observations": [{"date": "2026-08-01", "value": "1.5"}]}

        class Session:
            def __init__(self) -> None:
                self.call = None

            def get(self, url, **kwargs):
                self.call = (url, kwargs)
                return Response()

        session = Session()
        rows = common.fetch_fred_observations(
            "SERIES",
            "KEY",
            start="2026-08-01",
            end="2026-08-31",
            timeout=12,
            session=session,
        )
        self.assertEqual(rows[0]["value"], "1.5")
        self.assertEqual(session.call[1]["params"]["series_id"], "SERIES")
        self.assertEqual(session.call[1]["params"]["observation_start"], "2026-08-01")
        self.assertEqual(session.call[1]["timeout"], 12)


class SourceContractTests(unittest.TestCase):
    def test_news_prompt_remains_secret_driven(self) -> None:
        adapter = (ROOT / "supabase/functions/_shared/openai-adapter.ts").read_text(encoding="utf-8")
        self.assertIn('Deno.env.get("NEWS_ANALYSIS_SYSTEM_PROMPT")', adapter)
        self.assertIn('prompt.replace(/\\{\\{news_candidates\\}\\}/g, "")', adapter)

    def test_news_sources_and_partial_failure_reporting_remain_enabled(self) -> None:
        pipeline = (ROOT / "supabase/functions/news-pipeline/index.ts").read_text(encoding="utf-8")
        for source in ('"yonhap"', '"maekyung"', '"financial_news"'):
            self.assertIn(source, pipeline)
        self.assertIn("Promise.allSettled", pipeline)
        self.assertIn("errors: results.flatMap", pipeline)


class EmergingIndexTests(unittest.TestCase):
    def test_index_uses_documented_weighted_components(self) -> None:
        week = "2026-08-21"
        raw = {
            "high_yield_oas": {week: 11.0},
            "em_dollar_index": {week: 130.0},
            "tail_risk_oas": {week: 11.5},
            "em_equity_volatility": {week: 30.0},
        }
        rows = em.build_rows(raw, date(2026, 8, 22), {week: 45.0})
        self.assertEqual(len(rows), 1)
        expected = sum(
            em.score(raw[key][week], *em.SCALES[key]) * weight
            for key, weight in em.WEIGHTS.items()
        )
        self.assertEqual(rows[0]["stress_index"], round(expected, 2))

    def test_auxiliary_volatility_does_not_block_main_index(self) -> None:
        week = "2026-08-21"
        raw = {
            "high_yield_oas": {week: 11.0},
            "em_dollar_index": {week: 130.0},
            "tail_risk_oas": {week: 11.5},
            "em_equity_volatility": {},
        }
        rows = em.build_rows(raw, date(2026, 8, 22), {week: 45.0})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["vxeem_4w_average"])
        self.assertFalse(rows[0]["is_provisional"])


if __name__ == "__main__":
    unittest.main()
