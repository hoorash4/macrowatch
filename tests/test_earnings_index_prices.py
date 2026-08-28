from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260829_expand_market_index_prices.sql"
FUNCTION = ROOT / "supabase/functions/earnings-index-prices/index.ts"
WORKFLOW = ROOT / ".github/workflows/earnings-index-prices.yml"
DEPLOY = ROOT / ".github/workflows/deploy-supabase.yml"


class EarningsIndexPriceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.migration = MIGRATION.read_text(encoding="utf-8")
        cls.function = FUNCTION.read_text(encoding="utf-8")
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")
        cls.deploy = DEPLOY.read_text(encoding="utf-8")

    def test_shared_table_is_extended_without_a_duplicate_price_table(self) -> None:
        self.assertIn("alter table public.market_index_prices", self.migration)
        self.assertIn("'KOSPI200'", self.migration)
        self.assertIn("'KOSDAQ150'", self.migration)
        self.assertIn("'NASDAQ100'", self.migration)
        self.assertIn("'SP500'", self.migration)
        self.assertNotIn("create table", self.migration.lower())

    def test_backfill_uses_index_prices_only_and_marks_completed_quarters(self) -> None:
        self.assertIn('kisCode: "2001"', self.function)
        self.assertIn('kisCode: "2203"', self.function)
        self.assertIn('seriesId: "NASDAQ100"', self.function)
        self.assertIn('seriesId: "SP500"', self.function)
        self.assertIn("is_quarter_end: true", self.function)
        self.assertNotIn("earnings_quarterly_financials", self.function)

    def test_deploy_then_backfill_is_automatic_and_idempotent(self) -> None:
        self.assertIn(MIGRATION.name, self.deploy)
        self.assertIn('workflows: ["Deploy Supabase changes"]', self.workflow)
        self.assertIn('onConflict: "index_code,market_date"', self.function)
        self.assertIn('default: "2016"', self.workflow)


if __name__ == "__main__":
    unittest.main()
