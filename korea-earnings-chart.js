(() => {
  'use strict';

  const METRICS = ['revenue', 'operating_income', 'net_income'];
  const LABELS = { revenue: '매출', operating_income: '영업이익', net_income: '순이익' };
  const HEIGHT = 320, Y_AXIS_WIDTH = 52, MIN_WIDTH = 640;
  const PADDING = { top: 24, right: 24, bottom: 42, left: 14 };
  const state = { series: [], metric: 'revenue', years: 5, snapshotDate: null, universeCount: 100 };

  function ordinal(row) { return Number(row.fiscal_year) * 4 + Number(row.fiscal_quarter) - 1; }
  function finite(value) { const number = Number(value); return Number.isFinite(number) ? number : null; }
  function periodLabel(row) { return `${row.fiscalYear} Q${row.fiscalQuarter}`; }
  function formatSigned(value, unit) { return Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}${unit}` : '—'; }

  function normalizeRows(rows) {
    return rows.map((row) => {
      return {
        ...row,
        _ordinal: ordinal(row),
        // 서로 다른 표시통화를 임의의 현물환율로 섞지 않는다. 한국 카드의
        // 금액 합산은 OpenDART에서 원화로 공시된 행만 비교군에 포함한다.
        _values: Object.fromEntries(METRICS.map((metric) => [metric, row.currency === 'KRW' ? finite(row[metric]) : null])),
      };
    });
  }

  // NA는 원천 제공자가 연결/별도 구분을 주지 않은 경우다. 명시적인 CFS와 OFS가
  // 서로 충돌할 때만 제외해, 불필요한 결측 확대 없이 비교 기준을 지킨다.
  function compatibleScopes(rows) {
    return new Set(rows.map((row) => row.consolidation_scope).filter((scope) => scope && scope !== 'NA')).size <= 1;
  }

  function aggregateMetric(cohort, metric) {
    if (!cohort.length) return null;
    const sum = (key) => cohort.reduce((total, item) => total + item[key]._values[metric], 0);
    const current = sum('current'), prior = sum('prior'), previous = sum('previous'), previousPrior = sum('previousPrior');
    if (prior === 0 || previousPrior === 0) return null;
    const yoyPct = (current - prior) / Math.abs(prior) * 100;
    const previousYoyPct = (previous - previousPrior) / Math.abs(previousPrior) * 100;
    return { yoyPct, yoyDeltaPp: yoyPct - previousYoyPct, coverage: cohort.length, currentTotal: current };
  }

  function calculateSeries(rows, universeCount = 100) {
    const normalized = normalizeRows(rows);
    const byCompanyPeriod = new Map(normalized.map((row) => [`${row.company_id}:${row._ordinal}`, row]));
    const periods = [...new Map(normalized.map((row) => [row._ordinal, { ordinal: row._ordinal, fiscalYear: Number(row.fiscal_year), fiscalQuarter: Number(row.fiscal_quarter) }])).values()]
      .sort((a, b) => a.ordinal - b.ordinal);
    const companyIds = [...new Set(normalized.map((row) => row.company_id))];

    return periods.flatMap((period) => {
      const metrics = {};
      for (const metric of METRICS) {
        const cohort = companyIds.flatMap((companyId) => {
          const current = byCompanyPeriod.get(`${companyId}:${period.ordinal}`);
          const previous = byCompanyPeriod.get(`${companyId}:${period.ordinal - 1}`);
          const prior = byCompanyPeriod.get(`${companyId}:${period.ordinal - 4}`);
          const previousPrior = byCompanyPeriod.get(`${companyId}:${period.ordinal - 5}`);
          const comparison = [current, previous, prior, previousPrior];
          if (comparison.some((row) => !row || row._values[metric] === null) || !compatibleScopes(comparison)) return [];
          return [{ current, previous, prior, previousPrior }];
        });
        metrics[metric] = aggregateMetric(cohort, metric);
      }
      return Object.values(metrics).some(Boolean) ? [{ ...period, universeCount, metrics }] : [];
    });
  }

  function scale(value, sourceMin, sourceMax, targetMin, targetMax) {
    return sourceMax === sourceMin ? (targetMin + targetMax) / 2 : targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  }

  function path(points, key, yMin, yMax, width) {
    return points.map((point, index) => {
      const x = scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, width - PADDING.right);
      const y = scale(point[key], yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top);
      return `${index ? 'L' : 'M'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    }).join(' ');
  }

  function updateSummary(points) {
    const element = document.getElementById('korea-earnings-summary');
    const latest = points.at(-1);
    if (!element || !latest) return;
    const metric = latest.metrics[state.metric];
    element.innerHTML = `<strong>${LABELS[state.metric]} ${periodLabel(latest)}</strong><span>증가율 ${formatSigned(metric.yoyPct, '%')}</span><span>델타 ${formatSigned(metric.yoyDeltaPp, '%p')}</span><span>동일기업 ${metric.coverage}/${latest.universeCount}사</span><span>구성 기준 ${state.snapshotDate || '—'}</span>`;
  }

  function render() {
    const container = document.getElementById('korea-earnings-chart');
    if (!container) return;
    const usable = state.series.filter((row) => row.metrics[state.metric]);
    const points = state.years === 'max' ? usable : usable.slice(-Number(state.years) * 4);
    if (!points.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">비교 가능한 KOSPI 100 합산 실적이 아직 없습니다.</div>';
      return;
    }
    updateSummary(points);
    const values = points.flatMap((row) => [row.metrics[state.metric].yoyPct, row.metrics[state.metric].yoyDeltaPp]).filter(Number.isFinite);
    const maximum = Math.max(1, ...values.map(Math.abs));
    const tickStep = window.MacroWatchAnalysisChart.niceStep(maximum / 2);
    const yMax = tickStep * 2, yMin = -yMax;
    const frameWidth = Math.max(MIN_WIDTH, (container.clientWidth || MIN_WIDTH) - Y_AXIS_WIDTH);
    const chartWidth = Math.max(frameWidth, points.length * 48);
    const y = (value) => scale(value, yMin, yMax, HEIGHT - PADDING.bottom, PADDING.top);
    const x = (index) => scale(index, 0, Math.max(points.length - 1, 1), PADDING.left, chartWidth - PADDING.right);
    const ticks = [-2, -1, 0, 1, 2];
    const yAxis = ticks.map((multiple) => `<text x="45" y="${y(multiple * tickStep) + 3}" text-anchor="end" class="korea-earnings-axis-label">${multiple * tickStep}%</text>`).join('');
    const grids = ticks.map((multiple) => `<line x1="${PADDING.left}" y1="${y(multiple * tickStep)}" x2="${chartWidth - PADDING.right}" y2="${y(multiple * tickStep)}" class="korea-earnings-grid${multiple === 0 ? ' korea-earnings-grid--zero' : ''}"/>`).join('');
    const periodLabels = points.map((point, index) => point.fiscalQuarter === 1 || index === points.length - 1 ? `<text x="${x(index)}" y="${HEIGHT - 12}" text-anchor="middle" class="korea-earnings-period-label">${point.fiscalQuarter === 1 ? point.fiscalYear : `Q${point.fiscalQuarter}`}</text>` : '').join('');
    const metricPoints = points.map((point) => ({ ...point, yoyPct: point.metrics[state.metric].yoyPct, yoyDeltaPp: point.metrics[state.metric].yoyDeltaPp }));
    const dots = metricPoints.map((point, index) => `<circle cx="${x(index)}" cy="${y(point.yoyPct)}" r="2.8" class="korea-earnings-point korea-earnings-point--growth"/><circle cx="${x(index)}" cy="${y(point.yoyDeltaPp)}" r="2.5" class="korea-earnings-point korea-earnings-point--delta"/>`).join('');
    container.innerHTML = `<div class="korea-earnings-chart-layout"><svg class="korea-earnings-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${yAxis}</svg><div class="korea-earnings-chart-frame"><svg class="korea-earnings-chart-svg" width="${chartWidth}" height="${HEIGHT}" viewBox="0 0 ${chartWidth} ${HEIGHT}" role="img" aria-label="${LABELS[state.metric]} 합산 전년동기 증가율과 증가율 델타">${grids}${periodLabels}<path d="${path(metricPoints, 'yoyPct', yMin, yMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--growth"/><path d="${path(metricPoints, 'yoyDeltaPp', yMin, yMax, chartWidth)}" class="korea-earnings-line korea-earnings-line--delta"/>${dots}<line data-korea-earnings-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="korea-earnings-cursor"/><text data-korea-earnings-cursor-label x="0" y="15" text-anchor="middle" class="korea-earnings-cursor-label"></text><rect x="0" y="0" width="${chartWidth}" height="${HEIGHT}" fill="transparent" data-korea-earnings-hit/></svg></div></div>`;
    const frame = container.querySelector('.korea-earnings-chart-frame'), hit = container.querySelector('[data-korea-earnings-hit]');
    const cursor = container.querySelector('[data-korea-earnings-cursor]'), cursorLabel = container.querySelector('[data-korea-earnings-cursor-label]');
    hit.addEventListener('pointermove', (event) => {
      const rect = hit.getBoundingClientRect(), localX = (event.clientX - rect.left) * (chartWidth / rect.width);
      const index = Math.max(0, Math.min(metricPoints.length - 1, Math.round(scale(localX, PADDING.left, chartWidth - PADDING.right, 0, Math.max(metricPoints.length - 1, 1)))));
      const point = metricPoints[index], cursorX = x(index), metric = point.metrics[state.metric];
      cursor.setAttribute('x1', cursorX); cursor.setAttribute('x2', cursorX); cursorLabel.setAttribute('x', cursorX);
      cursorLabel.textContent = `${periodLabel(point)} · ${formatSigned(metric.yoyPct, '%')} · Δ ${formatSigned(metric.yoyDeltaPp, '%p')} · ${metric.coverage}/${point.universeCount}사`;
      cursor.classList.add('is-visible'); cursorLabel.classList.add('is-visible');
    });
    hit.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); cursorLabel.classList.remove('is-visible'); });
    window.MacroWatchAnalysisChart.scrollToLatest(frame);
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('korea-earnings-chart');
    if (!container || !supabaseClient) return;
    const { data: latestRows, error: latestError } = await supabaseClient.from('earnings_universe_snapshots').select('observed_on').eq('index_id', 'KOSPI100').order('observed_on', { ascending: false }).limit(1);
    const snapshotDate = latestRows?.[0]?.observed_on;
    if (latestError || !snapshotDate) { container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">KOSPI 100 구성 종목을 불러오지 못했습니다.</div>'; return; }
    const { data: members, error: memberError } = await supabaseClient.from('earnings_universe_snapshots').select('company_id,rank').eq('index_id', 'KOSPI100').eq('observed_on', snapshotDate).order('rank');
    if (memberError || !members?.length) { container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">KOSPI 100 구성 종목이 아직 없습니다.</div>'; return; }
    const companyIds = members.map((row) => row.company_id);
    const financials = await window.MacroWatchAnalysisChart.loadAllRows((from, to) => supabaseClient.from('earnings_quarterly_financials').select('company_id,fiscal_year,fiscal_quarter,period_end,revenue,operating_income,net_income,currency,consolidation_scope').in('company_id', companyIds).order('fiscal_year').order('fiscal_quarter').range(from, to));
    if (financials.error) { container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">KOSPI 100 실적을 불러오지 못했습니다.</div>'; return; }
    state.snapshotDate = snapshotDate; state.universeCount = members.length;
    state.series = calculateSeries(financials.data || [], members.length);
    render();
  }

  document.querySelector('.korea-earnings-tabs')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-korea-earnings-metric]');
    if (!button) return;
    state.metric = button.dataset.koreaEarningsMetric;
    document.querySelectorAll('[data-korea-earnings-metric]').forEach((item) => { const active = item === button; item.classList.toggle('is-active', active); item.setAttribute('aria-selected', String(active)); });
    render();
  });
  document.querySelector('[data-korea-earnings-ranges]')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-korea-earnings-range]');
    if (!button) return;
    state.years = button.dataset.koreaEarningsRange === 'max' ? 'max' : Number(button.dataset.koreaEarningsRange);
    document.querySelectorAll('[data-korea-earnings-range]').forEach((item) => item.classList.toggle('is-active', item === button));
    render();
  });
  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => { if (detail?.view === 'korea') render(); });
  window.MacroWatchKoreaEarnings = Object.freeze({ calculateSeries });
  window.MacroWatchDashboard?.registerLoader(load);
})();
