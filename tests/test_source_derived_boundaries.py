import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signals.canonical_series import rows as source_rows, store as store_source
from signals.derived_series import rows as derived_rows, store as store_derived


class FakeDatabase:
    def __init__(self):
        self.writes = []

    def upsert(self, table, rows, *, conflict):
        self.writes.append((table, rows, conflict))


class SourceDerivedBoundaryTests(unittest.TestCase):
    STAGED_WORKFLOWS = (
        "economic-chart-data.yml", "em-capital-capacity.yml", "em-stress.yml",
        "equity-bond-attractiveness.yml", "equity-bond-relative-value.yml",
        "financial-stress.yml", "korea-small-business-risk.yml", "korea-stress.yml",
        "liquidity.yml", "policy-expectation.yml", "small-business-risk.yml",
    )

    def test_source_store_rejects_calculated_rows(self):
        database = FakeDatabase()
        payload = source_rows("TEST", {date(2026, 9, 1): 1.0}, frequency="M",
                              source="DERIVED:A-B")
        with self.assertRaisesRegex(ValueError, "derived_series.store"):
            store_source(database, payload, owner="test")
        self.assertEqual([], database.writes)

    def test_source_store_rejects_a_second_writer_for_registered_series(self):
        database = FakeDatabase()
        payload = source_rows("US10Y", {date(2026, 9, 1): 4.0}, frequency="D",
                              source="USTREASURY:daily_treasury_yield_curve")
        with self.assertRaisesRegex(ValueError, "owner mismatch"):
            store_source(database, payload, owner="another_pipeline")
        self.assertEqual([], database.writes)

    def test_derived_store_rejects_external_rows(self):
        database = FakeDatabase()
        payload = [{"series_code": "TEST", "observation_date": "2026-09-01",
                    "value": 1.0, "frequency": "M", "source": "FRED:TEST"}]
        with self.assertRaisesRegex(ValueError, "non-derived source"):
            store_derived(database, payload)
        self.assertEqual([], database.writes)

    def test_derived_row_builder_requires_internal_provenance(self):
        with self.assertRaisesRegex(ValueError, "internal calculation"):
            derived_rows("TEST", {date(2026, 9, 1): 1.0}, frequency="M",
                         source="ECOS:TEST")

    def test_derived_jobs_depend_on_sources_and_have_no_provider_secrets(self):
        provider_tokens = (
            "FRED_API_KEY", "ECOS_API_KEY", "BEA_API_KEY", "BLS_API_KEY",
            "KOSIS_API_KEY", "CENSUS_API_KEY", "KRX_ID", "KRX_PW",
            "KIS_APP_KEY", "KIS_APP_SECRET",
        )
        for name in self.STAGED_WORKFLOWS:
            text = (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")
            self.assertIn("\n  derived:\n", text, name)
            derived_job = text.split("\n  derived:\n", 1)[1]
            self.assertIn("needs: sources", derived_job, name)
            self.assertIn("--stage derived", derived_job, name)
            self.assertIn("pip install", derived_job, f"{name}: derived runtime dependencies")
            for token in provider_tokens:
                self.assertNotIn(token, derived_job, f"{name}: {token}")

        foreign_flow = (ROOT / ".github/workflows/korea-foreign-flow.yml").read_text(encoding="utf-8")
        derived_job = foreign_flow.split("\n  derived:\n", 1)[1]
        self.assertIn("needs: sources", derived_job)
        self.assertIn('"finalize_only":true', derived_job)
        for token in provider_tokens:
            self.assertNotIn(token, derived_job, f"korea-foreign-flow.yml: {token}")

    def test_database_enforces_source_and_derived_contract(self):
        migration = (ROOT / "supabase" / "migrations" /
                     "20260914003000_split_source_and_derived_series.sql").read_text(encoding="utf-8")
        self.assertIn("economic_chart_points_external_source_only", migration)
        self.assertIn("source not like 'DERIVED:%'", migration)
        self.assertIn("frequency in ('D','W','M','Q','E','T')", migration)
        self.assertIn("enforce_economic_series_storage_class", migration)
        self.assertIn("with (security_invoker = true)", migration)

    def test_alerts_and_frontend_read_the_combined_view(self):
        consumers = (
            ROOT / "assets/js/charts/economic-charts.js",
            ROOT / "assets/js/dashboard/dashboard-charts.js",
            ROOT / "supabase/functions/check-one-target/index.ts",
        )
        for path in consumers:
            self.assertIn("economic_chart_series_points", path.read_text(encoding="utf-8"), str(path))

    def test_small_business_and_corporate_delinquency_codes_are_distinct(self):
        pipeline = (ROOT / "backend/signals/korea_small_business_risk_pipeline.py").read_text(encoding="utf-8")
        self.assertIn('"KR_SME_LOAN_DELINQ"', pipeline)
        self.assertNotIn('"KR_CORP_DELINQ"', pipeline)


if __name__ == "__main__":
    unittest.main()
