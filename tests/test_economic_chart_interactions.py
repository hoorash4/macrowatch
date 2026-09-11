from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EconomicChartInteractionTests(unittest.TestCase):
    def test_default_chart_follows_first_visible_user_ordered_series(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn('function firstVisibleSeries()', script)
        self.assertIn('const initial=firstVisibleSeries()', script)
        self.assertNotIn("visibleSeries().find(x=>x.code==='US10Y')", script)

    def test_default_range_is_exactly_latest_300_observations_and_double_click_resets_it(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn('DEFAULT_VISIBLE_BARS=300', script)
        self.assertIn('const count=Math.min(DEFAULT_VISIBLE_BARS,rows.length),last=rows.length-1', script)
        self.assertIn('from:last-count+1,to:last+rightGapBars()', script)
        self.assertIn("host.ondblclick=e=>{e.preventDefault();showInitialRange();}", script)
        self.assertIn('host.onwheel=wheel', script)
        self.assertIn('function scheduleInitialRange()', script)
        self.assertNotIn('pinLatestGap', script)
        self.assertIn('더블클릭 기본복귀', script)


if __name__ == '__main__':
    unittest.main()
