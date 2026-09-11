from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EconomicChartFeatureTests(unittest.TestCase):
    def test_economic_chart_uses_one_large_switchable_chart(self):
        html = (ROOT / 'economic-charts.html').read_text(encoding='utf-8')
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn('경제지표 챠트', html)
        self.assertIn('id="economic-series-list"', html)
        self.assertIn('id="economic-chart-host"', html)
        self.assertNotIn('id="economic-sections"', html)
        self.assertIn('function renderList()', script)
        self.assertIn('selectSeries(m)', script)
        self.assertNotIn('function buildCard', script)

    def test_chart_starts_with_latest_300_and_keeps_right_anchor(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn('DEFAULT_VISIBLE_BARS=300', script)
        self.assertIn('return{from:to-DEFAULT_VISIBLE_BARS,to}', script)
        self.assertIn('showInitialRange()', script)
        self.assertIn("$('economic-fit-max').onclick=fitMax", script)
        self.assertIn('handleScale:{axisPressedMouseMove:false,mouseWheel:false,pinch:true}', script)
        self.assertIn('minimumWidth:92', script)
        self.assertIn("host.addEventListener('wheel',wheel,{passive:false})", script)
        self.assertIn('attributionLogo:false', script)

    def test_series_list_reorders_only_inside_category_and_scrolls(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        self.assertIn("ORDER_KEY='macrowatch-economic-chart-order-v1'", script)
        self.assertIn('dragState.category!==name', script)
        self.assertIn('saveOrder(name,codes)', script)
        self.assertIn('overflow-y:auto', css)
        self.assertIn('scrollbar-gutter:stable', css)

    def test_horizontal_lines_use_stable_axis_marker_and_alert_bells(self):
        html = (ROOT / 'economic-charts.html').read_text(encoding='utf-8')
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        self.assertIn('id="economic-delete-line"', html)
        self.assertIn('id="economic-alert-modal"', html)
        self.assertIn('economic-alert-marker', script)
        self.assertIn('economic-alert-price', script)
        self.assertIn('economic-alert-bell', script)
        self.assertIn("source_type:'economic_chart'", script)
        self.assertIn("url:'economic-charts.html'", script)
        self.assertIn('css_selector:meta.code', script)
        self.assertIn('background:#facc15', css)
        self.assertIn("supabaseClient.from('targets').delete()", script)
        delete_line_body = script.split('function deleteLine()', 1)[1].split('function clearLines()', 1)[0]
        self.assertNotIn("from('targets')", delete_line_body)

    def test_chart_is_responsive_with_aspect_ratio(self):
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        self.assertIn('aspect-ratio:16/9', css)
        self.assertIn('aspect-ratio:4/3', css)

    def test_moving_averages_do_not_add_current_value_axis_labels(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn("D:[5,20,'5일','20일']", script)
        self.assertIn("W:[4,26,'4주','26주']", script)
        self.assertIn("T:[6,18,'6구간','18구간']", script)
        self.assertIn("M:[6,24,'6개월','24개월']", script)
        self.assertGreaterEqual(script.count('lastValueVisible:false,priceLineVisible:false'), 2)
        self.assertIn('lastValueVisible:true,priceLineVisible:true', script)

    def test_only_raw_or_requested_spread_series_are_in_chart_catalog(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        for removed in ('POLICY_EXPECTATION', 'EM_CAPACITY', 'US_SME_RISK', 'KR_SME_RISK', 'US_INFLATION'):
            self.assertNotIn(removed, script)
        for raw in ('US2Y', 'US10Y', 'HY_OAS', 'EM_OAS', 'KR3Y', 'KR10Y', 'WTI', 'USDKRW', 'WEI', 'RRP', 'TGA', 'EMRATIO'):
            self.assertIn(raw, script)
        self.assertIn("fallback:['policy_expectation_spreads','observation_date,treasury_2y_rate'", script)
        self.assertIn("fallback:['us_policy_rate_daily','observed_on,treasury_10y_pct','observed_on','treasury_10y_pct']", script)

    def test_economic_chart_collection_separates_automatic_bootstrap_and_alert_checking(self):
        workflow = (ROOT / '.github/workflows/economic-chart-data.yml').read_text(encoding='utf-8')
        pipeline = (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8')
        tracker = (ROOT / 'backend/tracking/check_targets.py').read_text(encoding='utf-8')
        self.assertIn("choices=(\"automatic\", \"bootstrap\")", pipeline)
        self.assertIn("timedelta(days=3660) if mode == \"bootstrap\" else timedelta(days=45)", pipeline)
        self.assertIn("github.event_name == 'schedule' && 'automatic'", workflow)
        self.assertIn('options: [automatic, bootstrap]', workflow)
        self.assertNotIn('delete_before(', pipeline)
        self.assertIn('str(row["observation_date"]) not in existing', pipeline)
        self.assertIn('if mode == "automatic":', pipeline)
        self.assertIn('changed_codes = {code for code, inserted in counts.items() if inserted > 0}', pipeline)
        self.assertIn('check_collected_series_alerts(db, changed_codes)', pipeline)
        self.assertIn('!= "economic_chart"', tracker)
        self.assertIn('deliver_queued_alerts(db, targets)', tracker)

    def test_core_series_include_fred_ecos_and_derived_spreads(self):
        pipeline = (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8')
        for token in ('DGS2', 'DGS10', 'BAMLH0A0HYM2', 'BAMLEMCBPIOAS', 'DCOILWTICO', 'DEXKOUS', 'WEI', 'RRPONTSYD', 'WTREGEN', 'EMRATIO'):
            self.assertIn(token, pipeline)
        self.assertIn('010200000', pipeline)
        self.assertIn('010210000', pipeline)
        self.assertIn('US10Y2Y', pipeline)
        self.assertIn('KR10Y3Y', pipeline)


if __name__ == '__main__':
    unittest.main()
