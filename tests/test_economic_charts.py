from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EconomicChartFeatureTests(unittest.TestCase):
    def test_economic_chart_has_dedicated_page_and_trading_style_controls(self):
        html = (ROOT / 'economic-charts.html').read_text(encoding='utf-8')
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        config = (ROOT / 'assets/js/core/config.js').read_text(encoding='utf-8')
        self.assertIn('경제지표 차트', html)
        self.assertIn('lightweight-charts', html)
        self.assertNotIn('data-years=', html)
        self.assertIn('handleScale:{ axisPressedMouseMove:true, mouseWheel:true, pinch:true }', script)
        self.assertIn('createPriceLine', script)
        self.assertIn("D: [5, 20, '5일', '20일']", script)
        self.assertIn("W: [4, 26, '4주', '26주']", script)
        self.assertIn("T: [6, 18, '6구간', '18구간']", script)
        self.assertIn("M: [6, 24, '6개월', '24개월']", script)
        self.assertIn("target: '_blank'", config)

    def test_economic_chart_collection_separates_automatic_and_bootstrap(self):
        workflow = (ROOT / '.github/workflows/economic-chart-data.yml').read_text(encoding='utf-8')
        pipeline = (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8')
        self.assertIn("choices=(\"automatic\", \"bootstrap\")", pipeline)
        self.assertIn("timedelta(days=3660) if mode == \"bootstrap\" else timedelta(days=45)", pipeline)
        self.assertIn("github.event_name == 'schedule' && 'automatic'", workflow)
        self.assertIn('options: [automatic, bootstrap]', workflow)
        self.assertNotIn('delete_before(', pipeline)
        self.assertIn('str(row["observation_date"]) not in existing', pipeline)

    def test_core_series_include_fred_ecos_and_derived_spreads(self):
        pipeline = (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8')
        for token in ('DGS2', 'DGS10', 'BAMLH0A0HYM2', 'BAMLEMCBPIOAS', 'DCOILWTICO', 'DEXKOUS', 'WEI'):
            self.assertIn(token, pipeline)
        self.assertIn('010200000', pipeline)
        self.assertIn('010210000', pipeline)
        self.assertIn('US10Y2Y', pipeline)
        self.assertIn('KR10Y3Y', pipeline)


if __name__ == '__main__':
    unittest.main()
