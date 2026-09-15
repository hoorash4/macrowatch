(() => {
  'use strict';

  const cfg = window.MACROWATCH_CONFIG || {};
  const supabaseClient = window.supabase?.createClient(cfg.supabaseUrl, cfg.supabasePublishableKey);
  const host = document.getElementById('historical-chart-host');
  const status = document.getElementById('historical-chart-status');
  const meta = document.getElementById('historical-chart-meta');
  const marketButtons = [...document.querySelectorAll('[data-historical-index]')];

  if (!host || !supabaseClient || !window.LightweightCharts) return;

  const INDEX_META = {
    SP500: { label: 'S&P 500', initialFrom: '1995-01-01', initialTo: '2004-12-31' },
    NASDAQ_COMPOSITE: { label: 'NASDAQ Composite', initialFrom: '1995-01-01', initialTo: '2004-12-31' },
    KOSPI: { label: 'KOSPI', initialFrom: '1995-01-01', initialTo: '2004-12-31' },
  };

  let chart = null;
  let series = null;
  let resizeObserver = null;
  let activeCode = 'NASDAQ_COMPOSITE';
  let requestToken = 0;
  const cache = new Map();

  function themeColors() {
    const css = getComputedStyle(document.documentElement);
    return {
      background: css.getPropertyValue('--theme-chart-bg').trim() || '#ffffff',
      text: css.getPropertyValue('--theme-chart-text').trim() || '#6b7280',
      grid: css.getPropertyValue('--theme-chart-grid').trim() || '#e5e7eb',
      border: css.getPropertyValue('--theme-chart-border').trim() || '#d1d5db',
      line: css.getPropertyValue('--color-brand-navy').trim() || '#071b42',
      crosshair: css.getPropertyValue('--theme-chart-crosshair').trim() || '#9ca3af',
    };
  }

  function chartOptions() {
    const c = themeColors();
    return {
      width: host.clientWidth,
      height: host.clientHeight,
      layout: {
        background: { color: c.background },
        textColor: c.text,
        fontFamily: 'Pretendard, system-ui, sans-serif',
        fontSize: 11,
        attributionLogo: false,
      },
      localization: { locale: 'ko-KR', dateFormat: 'yyyy. MM. dd.' },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border, scaleMargins: { top: .08, bottom: .08 } },
      timeScale: { borderColor: c.border, timeVisible: false, secondsVisible: false, rightOffset: 8 },
      crosshair: {
        mode: window.LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: c.crosshair, width: 1, style: 2 },
        horzLine: { color: c.crosshair, width: 1, style: 2 },
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true },
    };
  }

  function ensureChart() {
    if (chart) return;
    chart = window.LightweightCharts.createChart(host, chartOptions());
    const c = themeColors();
    series = chart.addLineSeries({
      color: c.line,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
    });
    resizeObserver = new ResizeObserver(() => {
      if (!chart) return;
      chart.applyOptions({ width: host.clientWidth, height: host.clientHeight });
    });
    resizeObserver.observe(host);
  }

  async function fetchIndex(code) {
    if (cache.has(code)) return cache.get(code);
    const rows = [];
    for (let from = 0; ; from += 1000) {
      const { data, error } = await supabaseClient
        .from('market_index_prices')
        .select('market_date,close')
        .eq('index_code', code)
        .order('market_date', { ascending: true })
        .range(from, from + 999);
      if (error) throw error;
      rows.push(...(data || []));
      if (!data || data.length < 1000) break;
    }
    const normalized = rows
      .map(row => ({ time: String(row.market_date).slice(0, 10), value: Number(row.close) }))
      .filter(row => row.time.length === 10 && Number.isFinite(row.value));
    cache.set(code, normalized);
    return normalized;
  }

  function setActiveButton(code) {
    for (const button of marketButtons) {
      const active = button.dataset.historicalIndex === code;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    }
  }

  function showInitialWindow(code, rows) {
    const config = INDEX_META[code];
    if (!config || !rows.length) return;
    const first = rows[0].time;
    const last = rows[rows.length - 1].time;
    const from = config.initialFrom < first ? first : config.initialFrom;
    const to = config.initialTo > last ? last : config.initialTo;
    chart.timeScale().setVisibleRange({ from, to });
  }

  async function renderIndex(code) {
    const token = ++requestToken;
    activeCode = code;
    setActiveButton(code);
    status.textContent = '지수 데이터 불러오는 중';
    meta.textContent = `${INDEX_META[code]?.label || code} · 1990년 이후 일간 종가`;
    try {
      const rows = await fetchIndex(code);
      if (token !== requestToken) return;
      ensureChart();
      series.setData(rows);
      showInitialWindow(code, rows);
      const first = rows[0]?.time || '—';
      const last = rows[rows.length - 1]?.time || '—';
      status.textContent = `${rows.length.toLocaleString('ko-KR')}개 · ${first} ~ ${last}`;
    } catch (error) {
      if (token !== requestToken) return;
      console.error('[Historical Insight] index load failed', error);
      status.textContent = '지수 데이터를 불러오지 못했습니다.';
    }
  }

  for (const button of marketButtons) {
    button.addEventListener('click', () => renderIndex(button.dataset.historicalIndex));
  }

  const themeObserver = new MutationObserver(() => {
    if (!chart || !series) return;
    const c = themeColors();
    chart.applyOptions(chartOptions());
    series.applyOptions({ color: c.line });
  });
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

  window.addEventListener('beforeunload', () => resizeObserver?.disconnect(), { once: true });
  renderIndex(activeCode);
})();
