from pathlib import Path
import unittest

# Economic-chart tests protect the automatic/backfill boundary as well as UI behavior.
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

    def test_chart_starts_with_latest_300_and_uses_real_chart_space_right_gap(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        html = (ROOT / 'economic-charts.html').read_text(encoding='utf-8')
        self.assertIn('DEFAULT_VISIBLE_BARS=300', script)
        self.assertIn('RIGHT_GAP_PX=18', script)
        self.assertIn('function rightGapBars()', script)
        self.assertNotIn('function updateFixedGap()', script)
        self.assertNotIn("chart.priceScale('right').width", script)
        # Protect behavior, not the obsolete spelling that implemented it before.
        self.assertIn('anchor=r.to>=last?maxTo:Math.min(r.to,maxTo)', script)
        self.assertIn('if(to>maxTo){to=maxTo;from=to-w;}', script)
        self.assertIn('showInitialRange()', script)
        self.assertIn("$('economic-fit-max').onclick=fitMax", script)
        self.assertIn('handleScale:{axisPressedMouseMove:false,mouseWheel:false,pinch:true}', script)
        self.assertIn('host.onwheel=wheel', script)
        self.assertIn('host.onwheel=null', script)
        self.assertIn('attributionLogo:false', script)
        self.assertNotIn('id="economic-plot-gap"', html)
        self.assertNotIn('.economic-plot-gap{', css)

    def test_series_list_reorders_and_persists_per_user_delete_restore(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        self.assertNotIn('localStorage', script)
        self.assertIn("from('economic_chart_preferences')", script)
        self.assertIn('series_order', script)
        self.assertIn('hidden_series', script)
        self.assertIn('dragState.category!==name', script)
        self.assertIn('saveOrder(name,codes)', script)
        self.assertIn('function hideSeries(m)', script)
        self.assertIn('function restoreSeries()', script)
        self.assertIn('economic-series-remove', script)
        self.assertIn('economic-series-restore', script)
        self.assertIn('overflow-y:auto', css)
        self.assertIn('scrollbar-gutter:stable', css)
        hide_body = script.split('function hideSeries(m)', 1)[1].split('function restoreSeries()', 1)[0]
        self.assertNotIn("supabaseClient.from('economic_chart_points').delete", hide_body)

    def test_horizontal_line_has_one_combined_axis_marker_and_flat_bell(self):
        html = (ROOT / 'economic-charts.html').read_text(encoding='utf-8')
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        self.assertIn('id="economic-delete-line"', html)
        self.assertIn('id="economic-alert-modal"', html)
        self.assertIn('economic-alert-marker-box', script)
        self.assertIn('economic-alert-marker-value', script)
        self.assertIn('economic-alert-bell', script)
        self.assertIn('axisLabelVisible:false', script)
        self.assertNotIn('economic-alert-price', script)
        self.assertNotIn('🔔', script)
        self.assertIn("source_type:'economic_chart'", script)
        self.assertIn("url:'economic-charts.html'", script)
        self.assertIn('css_selector:meta.code', script)
        self.assertIn('color:#d1d5db', css)
        self.assertIn('color:#facc15', css)
        marker_css = css.split('.economic-alert-marker-box{', 1)[1].split('}', 1)[0]
        self.assertNotIn('border-radius:50%', marker_css)
        self.assertIn("supabaseClient.from('targets').delete()", script)
        delete_line_body = script.split('function deleteLine()', 1)[1].split('function clearLines()', 1)[0]
        self.assertNotIn("from('targets')", delete_line_body)
        self.assertIn('horizontal_lines', script)
        self.assertIn('savePlainLinesFromChart()', script)
        self.assertIn('restorePlainLines()', script)

    def test_chart_footer_is_compact_and_date_axis_is_korean(self):
        css = (ROOT / 'assets/css/economic-charts.css').read_text(encoding='utf-8')
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn('height:min(64.8vw,calc(90dvh - 162px));min-height:324px', css)
        self.assertIn('.economic-chart-panel{display:flex;min-width:0;min-height:0;height:100%', css)
        self.assertIn('.economic-chart-host{position:relative;box-sizing:border-box;width:100%;min-width:0;min-height:0;flex:1 1 auto', css)
        self.assertIn('height:min(423px,58.5dvh);min-height:270px', css)
        self.assertIn('min-height:24px', css)
        self.assertIn('padding:4px 12px 6px', css)
        self.assertIn("locale:'ko-KR'", script)
        self.assertIn("dateFormat:'yyyy. MM. dd.'", script)
        self.assertIn('tickMarkFormatter:koTick', script)
        self.assertIn('return`${p.year}년`', script)
        self.assertIn('return`${p.month}월`', script)
        self.assertIn("if(!monthTickDates.has(timeKey(time)))return''", script)

    def test_moving_averages_do_not_add_current_value_axis_labels(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        self.assertIn("D:[5,20,'5일','20일']", script)
        self.assertIn("W:[4,26,'4주','26주']", script)
        self.assertIn("T:[6,18,'6구간','18구간']", script)
        self.assertIn("M:[6,24,'6개월','24개월']", script)
        self.assertGreaterEqual(script.count('lastValueVisible:false,priceLineVisible:false'), 2)
        self.assertIn('lastValueVisible:true,priceLineVisible:true', script)

    def test_chart_catalog_keeps_raw_series_and_adds_verified_requested_items(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        for removed in ('POLICY_EXPECTATION', 'EM_CAPACITY', 'US_SME_RISK', 'KR_SME_RISK', 'US_INFLATION'):
            self.assertNotIn(removed, script)
        for raw in ('US2Y', 'US10Y', 'HY_OAS', 'EM_OAS', 'KR3Y', 'KR10Y', 'WTI', 'USDKRW', 'WEI', 'RRP', 'TGA', 'EMRATIO', 'KOSPI_PER', 'KOSPI_PBR', 'CASE_SHILLER_20'):
            self.assertIn(raw, script)
        self.assertIn("fallback:['policy_expectation_spreads','observation_date,treasury_2y_rate'", script)
        self.assertIn("fallback:['us_policy_rate_daily','observed_on,treasury_10y_pct','observed_on','treasury_10y_pct']", script)
        self.assertIn('SPCS20RSA', script)
        self.assertIn('S&P 재배포 사전허가', script)

    def test_economic_chart_collection_separates_automatic_and_explicit_backfill(self):
        automatic_workflow = (ROOT / '.github/workflows/economic-chart-data.yml').read_text(encoding='utf-8')
        backfill_workflow = (ROOT / '.github/workflows/economic-chart-backfill-once.yml').read_text(encoding='utf-8')
        automatic = (ROOT / 'backend/signals/economic_chart_automatic.py').read_text(encoding='utf-8')
        backfill = (ROOT / 'backend/signals/economic_chart_backfill.py').read_text(encoding='utf-8')
        helpers = (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8')
        tracker = (ROOT / 'backend/tracking/check_targets.py').read_text(encoding='utf-8')

        self.assertIn('signals.economic_chart_automatic', automatic_workflow)
        self.assertNotIn('signals.economic_chart_backfill', automatic_workflow)
        self.assertIn('workflow_dispatch:', backfill_workflow)
        self.assertNotIn('  schedule:', backfill_workflow)
        self.assertNotIn('  push:', backfill_workflow)
        self.assertIn('signals.economic_chart_backfill', backfill_workflow)
        self.assertIn('signals.korea_export_backfill', backfill_workflow)
        self.assertNotIn('economic_chart_backfill', automatic)
        self.assertIn('timedelta(days=45)', automatic)
        self.assertIn('timedelta(days=3660)', backfill)
        self.assertNotIn('argparse', helpers)
        self.assertNotIn('choices=("automatic", "bootstrap")', helpers)
        self.assertIn('str(row["observation_date"]) not in existing', helpers)
        self.assertIn('check_collected_series_alerts(db, changed)', automatic)
        self.assertIn('!= "economic_chart"', tracker)
        self.assertIn('deliver_queued_alerts(db, targets)', tracker)

    def test_chart_reads_full_primary_history_and_merges_legacy_fallback_only_as_gap_cover(self):
        script = (ROOT / 'assets/js/charts/economic-charts.js').read_text(encoding='utf-8')
        backfill = (ROOT / 'backend/signals/economic_chart_backfill.py').read_text(encoding='utf-8')
        self.assertIn('"US10Y": ("DGS10", "D")', (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8'))
        self.assertIn('for code, (source_id, frequency) in FRED_SERIES.items()', backfill)
        self.assertIn('new Map([...fallbackRows,...primaryRows].map', script)
        self.assertIn("economic_chart_points", script)

    def test_korea_export_is_collected_incrementally_and_backfilled_explicitly(self):
        automatic = (ROOT / 'backend/signals/economic_chart_automatic.py').read_text(encoding='utf-8')
        source = (ROOT / 'backend/sources/korea_export_intramonth.py').read_text(encoding='utf-8')
        backfill = (ROOT / 'backend/signals/korea_export_backfill.py').read_text(encoding='utf-8')
        self.assertIn('fetch_snapshots(export_start_month, max_pages=6)', automatic)
        self.assertIn('KR_EXPORT_DAILY_AVG', automatic)
        self.assertIn('cumulative_export_musd - d10.cumulative_export_musd', source)
        self.assertIn('cumulative_workdays - d10.cumulative_workdays', source)
        self.assertIn('cumulative_export_musd - d20.cumulative_export_musd', source)
        self.assertIn('cumulative_workdays - d20.cumulative_workdays', source)
        self.assertIn('timedelta(days=3660)', backfill)

    def test_core_series_include_fred_ecos_krx_and_derived_spreads(self):
        pipeline = (ROOT / 'backend/signals/economic_chart_pipeline.py').read_text(encoding='utf-8')
        for token in ('DGS2', 'DGS10', 'BAMLH0A0HYM2', 'BAMLEMCBPIOAS', 'DCOILWTICO', 'DEXKOUS', 'WEI', 'RRPONTSYD', 'WTREGEN', 'EMRATIO'):
            self.assertIn(token, pipeline)
        self.assertIn('010200000', pipeline)
        self.assertIn('010210000', pipeline)
        self.assertIn('US10Y2Y', pipeline)
        self.assertIn('KR10Y3Y', pipeline)
        self.assertIn('MDCSTAT00702', pipeline)
        self.assertIn('WT_PER', pipeline)
        self.assertIn('WT_STKPRC_NETASST_RTO', pipeline)
        self.assertIn('"indTpCd": "1"', pipeline)
        self.assertIn('"indTpCd2": "001"', pipeline)
        self.assertIn('cursor + timedelta(days=729)', pipeline)
        self.assertNotIn('earnings_v2.providers', pipeline)
        self.assertNotIn('SPCS20RSA', pipeline)


if __name__ == '__main__':
    unittest.main()
