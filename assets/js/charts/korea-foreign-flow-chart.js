(() => {
  'use strict';

  const { mainHeight: HEIGHT, mobileMinWidth: MIN_VIEWPORT_WIDTH, axisWidth: Y_AXIS_WIDTH } = window.MacroWatchAnalysisChart.chartLayout;
  const PADDING = window.MacroWatchAnalysisChart.plotPadding();
  const Z_WINDOW = 756;
  const MIN_Z_HISTORY = 60;
  const chartUtils = window.MacroWatchAnalysisChart;

  const PROFILE = chartUtils.chartProfile({
    xAxisMode: 'zero',
    cursorSeries: Object.freeze([{ key: 'value', label: '외국인 자금흐름' }]),
  });

  const state = {
    rows: [],
    selectedYears: PROFILE.defaultYears,
  };

  const scale = (value, sourceMin, sourceMax, targetMin, targetMax) => {
    if (sourceMax === sourceMin) {
      return (targetMin + targetMax) / 2;
    }
    return targetMin + ((value - sourceMin) / (sourceMax - sourceMin)) * (targetMax - targetMin);
  };

  const mean = (values) => values.reduce((sum, value) => sum + value, 0) / values.length;

  // 각 날짜에는 그날까지 공개된 최근 3년 자료만 사용하여 미래 정보가 섞이지 않게 합니다.
  function causalZScore(values, index) {
    const history = values.slice(Math.max(0, index - Z_WINDOW + 1), index + 1).filter(Number.isFinite);
    if (history.length < MIN_Z_HISTORY || !Number.isFinite(values[index])) {
      return null;
    }
    const average = mean(history);
    const variance = mean(history.map((value) => (value - average) ** 2));
    if (variance > 0) {
      return Math.max(-3, Math.min(3, (values[index] - average) / Math.sqrt(variance)));
    }
    return 0;
  }

  function calculateTenDayCumulative(rows) {
    const flowValues = [];
    const wonValues = [];

    return rows.flatMap((row, index) => {
      if (index < 10) {
        flowValues.push(Number.NaN);
        wonValues.push(Number.NaN);
        return [];
      }
      const windowRows = rows.slice(index - 9, index + 1);
      const netBuy = windowRows.reduce((sum, item) => sum + Number(item.foreign_net_buy_amount), 0);
      const tradingValue = windowRows.reduce((sum, item) => sum + Number(item.kospi_trading_value), 0);
      const previousRate = Number(rows[index - 10].usdkrw_rate);
      const currentRate = Number(row.usdkrw_rate);

      flowValues.push(tradingValue > 0 ? netBuy / tradingValue : Number.NaN);
      wonValues.push(previousRate > 0 ? -(currentRate / previousRate - 1) : Number.NaN);

      const flowZ = causalZScore(flowValues, index);
      const wonZ = causalZScore(wonValues, index);

      if (flowZ === null || wonZ === null) {
        return [];
      }

      return [{
        ...row,
        daily_flow_index: Number(row.flow_index),
        flow_index: (flowZ + wonZ) / 2,
      }];
    });
  }

  // 그래프 값은 바꾸지 않고 강화·중립·약화 상태에만 진입/해제 문턱을 다르게 적용합니다.
  function applyHysteresis(rows) {
    let regime = 'neutral';
    return rows.map((row) => {
      const value = Number(row.flow_index);
      if (regime === 'neutral') {
        if (value >= 0.4) {
          regime = 'strengthening';
        } else if (value <= -0.4) {
          regime = 'weakening';
        }
      } else if (regime === 'strengthening' && value <= 0.15) {
        regime = value <= -0.4 ? 'weakening' : 'neutral';
      } else if (regime === 'weakening' && value >= -0.15) {
        regime = value >= 0.4 ? 'strengthening' : 'neutral';
      }
      return { ...row, regime };
    });
  }

  function updateRegimeLabel(rows) {
    const regime = rows.at(-1)?.regime || 'neutral';
    const element = document.getElementById('korea-foreign-flow-state');
    if (!element) {
      return;
    }
    element.textContent = regime === 'strengthening' ? '강화' : regime === 'weakening' ? '약화' : '중립';
    element.className = `font-semibold ${regime === 'strengthening' ? 'text-rose-700' : regime === 'weakening' ? 'text-blue-700' : 'text-slate-600'}`;
  }

  function verticalScale(points) {
    const values = points
      .flatMap((point) => [point.value, point.dailyValue])
      .filter(Number.isFinite);

    const domain = chartUtils.axisDomain(values, { symmetric: true, minimumSpan: 0.2 })
      || { min: -0.125, max: 0.125, ticks: [-0.1, -0.05, 0, 0.05, 0.1], step: 0.05 };

    return domain;
  }

  function render(container, rows, selectedYears) {
    const points = rows
      .map((row) => ({
        ...row,
        timestamp: Date.parse(`${row.observation_date}T00:00:00Z`),
        value: Number(row.flow_index),
        dailyValue: Number(row.daily_flow_index),
      }))
      .filter((row) => Number.isFinite(row.timestamp) && Number.isFinite(row.value));

    if (!points.length) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도 데이터가 아직 없습니다.</div>';
      return;
    }

    const firstTimestamp = points[0].timestamp;
    const lastTimestamp = points.at(-1).timestamp;
    const viewportWidth = Math.max(MIN_VIEWPORT_WIDTH, (container.clientWidth || MIN_VIEWPORT_WIDTH) - Y_AXIS_WIDTH);
    const timelineWidth = chartUtils.timelineWidth(viewportWidth, firstTimestamp, lastTimestamp, selectedYears);

    points.forEach((point) => {
      point.x = scale(point.timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
    });

    const initialScale = verticalScale(points);

    const pathFor = (sourceDomain, key = 'value') => {
      const filtered = points
        .filter((point) => Number.isFinite(point[key]))
        .map((point) => ({
          x: point.x,
          y: scale(point[key], sourceDomain.min, sourceDomain.max, HEIGHT - PADDING.bottom, PADDING.top),
        }));
      return window.MacroWatchAnalysisChart.monotonePath(filtered);
    };

    const firstYear = new Date(firstTimestamp).getUTCFullYear();
    const lastYear = new Date(lastTimestamp).getUTCFullYear();

    const yearGuides = Array.from({ length: lastYear - firstYear + 1 }, (_, index) => {
      const year = firstYear + index;
      const timestamp = Date.UTC(year, 0, 1);
      if (timestamp < firstTimestamp || timestamp > lastTimestamp) {
        return '';
      }
      const x = scale(timestamp, firstTimestamp, lastTimestamp, PADDING.left, timelineWidth - PADDING.right);
      const label = selectedYears === 'max' ? String(year).slice(-2) : year;
      return `<line x1="${x}" y1="${PADDING.top}" x2="${x}" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-year-guide"/><text x="${x}" y="${HEIGHT - 10}" text-anchor="middle" class="policy-expectation-year">${label}</text>`;
    }).join('');

    const yPosition = (value, sourceDomain = initialScale) => {
      return scale(value, sourceDomain.min, sourceDomain.max, HEIGHT - PADDING.bottom, PADDING.top);
    };

    const tickSlots = Array.from({ length: 6 }, (_, index) => index);
    const grids = tickSlots.map((index) => {
      const value = initialScale.ticks[index];
      const y = Number.isFinite(value) ? yPosition(value) : PADDING.top;
      const zeroClass = value === 0 ? ' analysis-chart-zero-line' : '';
      const visibility = Number.isFinite(value) ? '' : ' visibility="hidden"';
      return `<line data-korea-foreign-flow-y-grid="${index}" x1="${PADDING.left}" y1="${y}" x2="${timelineWidth - PADDING.right}" y2="${y}" class="policy-expectation-y-grid${zeroClass}"${visibility}/>`;
    }).join('');

    const labels = tickSlots.map((index) => {
      const value = initialScale.ticks[index];
      const y = Number.isFinite(value) ? yPosition(value) : PADDING.top;
      const visibility = Number.isFinite(value) ? '' : ' visibility="hidden"';
      const formatted = Number.isFinite(value) ? chartUtils.formatAxisNumber(value) : '';
      return `<line data-korea-foreign-flow-y-tick="${index}" x1="${Y_AXIS_WIDTH - 5}" y1="${y}" x2="${Y_AXIS_WIDTH}" y2="${y}" class="policy-expectation-y-tick"${visibility}/><text data-korea-foreign-flow-y-index="${index}" x="${Y_AXIS_WIDTH - 9}" y="${y + 3}" text-anchor="end" class="policy-expectation-y-label"${visibility}>${formatted}</text>`;
    }).join('');

    const { frame, svg } = chartUtils.mountChartFrame({
      container,
      profile: PROFILE,
      height: HEIGHT,
      axisViewWidth: Y_AXIS_WIDTH,
      leftAxisMarkup: labels,
      ariaLabel: '한국 외국인 자금 유출입 강도',
      plotMarkup: `
        <svg class="policy-expectation-chart-svg" style="width:${timelineWidth}px" viewBox="0 0 ${timelineWidth} ${HEIGHT}" role="img" aria-label="0선을 중심으로 표시한 한국 외국인 자금 유출입 강도">
          <defs>
            <linearGradient id="korea-foreign-flow-line-gradient" gradientUnits="userSpaceOnUse" x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}">
              <stop offset="0%" stop-color="#b4535d"/>
              <stop offset="50%" stop-color="#b4535d"/>
              <stop offset="50%" stop-color="#2563a8"/>
              <stop offset="100%" stop-color="#2563a8"/>
            </linearGradient>
          </defs>
          <g>${yearGuides}</g>
          <g>${grids}</g>
          <text x="${PADDING.left + 4}" y="${yPosition(0) - 7}" class="policy-expectation-zero-label">평균적 유입 여건</text>
          <path data-korea-daily-flow-line d="${pathFor(initialScale, 'dailyValue')}" class="policy-expectation-line policy-expectation-line--raw" style="stroke:url(#korea-foreign-flow-line-gradient)"/>
          <path data-korea-flow-line d="${pathFor(initialScale)}" class="policy-expectation-line policy-expectation-line--average" style="stroke:url(#korea-foreign-flow-line-gradient)"/>
          <line data-korea-foreign-flow-cursor x1="0" y1="${PADDING.top}" x2="0" y2="${HEIGHT - PADDING.bottom}" class="policy-expectation-cursor"/>
          <text data-korea-foreign-flow-value x="0" y="16" text-anchor="middle" class="analysis-chart-cursor-text analysis-chart-cursor-value" visibility="hidden"></text>
          <text data-korea-foreign-flow-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 12}" class="analysis-chart-cursor-text analysis-chart-cursor-date" visibility="hidden"></text>
        </svg>
      `.trim(),
    });

    const line = container.querySelector('[data-korea-flow-line]');
    const dailyLine = container.querySelector('[data-korea-daily-flow-line]');
    const yLabels = [...container.querySelectorAll('[data-korea-foreign-flow-y-index]')];
    const yGrids = [...container.querySelectorAll('[data-korea-foreign-flow-y-grid]')];
    const cursor = container.querySelector('[data-korea-foreign-flow-cursor]');
    const detail = container.querySelector('[data-korea-foreign-flow-detail]');
    const cursorValue = container.querySelector('[data-korea-foreign-flow-value]');
    let animationFrame = null;

    const updateVisibleScale = () => {
      animationFrame = null;
      const visiblePoints = points.filter((point) => point.x >= frame.scrollLeft && point.x <= frame.scrollLeft + frame.clientWidth);
      if (!visiblePoints.length) {
        return;
      }
      const current = verticalScale(visiblePoints);
      line.setAttribute('d', pathFor(current));
      dailyLine.setAttribute('d', pathFor(current, 'dailyValue'));

      yLabels.forEach((label) => {
        const index = Number(label.dataset.koreaForeignFlowYIndex);
        const value = current.ticks[index];
        const tick = container.querySelector(`[data-korea-foreign-flow-y-tick="${index}"]`);
        const grid = yGrids[index];

        if (!Number.isFinite(value)) {
          label.setAttribute('visibility', 'hidden');
          tick?.setAttribute('visibility', 'hidden');
          grid?.setAttribute('visibility', 'hidden');
          return;
        }

        const y = yPosition(value, current);
        label.removeAttribute('visibility');
        tick?.removeAttribute('visibility');
        grid?.removeAttribute('visibility');
        label.textContent = chartUtils.formatAxisNumber(value, { showPlus: true });
        label.setAttribute('y', y + 3);
        tick?.setAttribute('y1', y);
        tick?.setAttribute('y2', y);
        grid?.setAttribute('y1', y);
        grid?.setAttribute('y2', y);
        grid?.classList.toggle('analysis-chart-zero-line', value === 0);
      });
    };

    frame.addEventListener('scroll', () => {
      if (animationFrame === null) {
        animationFrame = window.requestAnimationFrame(updateVisibleScale);
      }
    }, { passive: true });

    chartUtils.scrollToLatest(frame);
    window.requestAnimationFrame(updateVisibleScale);

    frame.addEventListener('pointermove', (event) => {
      const bounds = svg.getBoundingClientRect();
      const pointerX = ((event.clientX - bounds.left) / bounds.width) * timelineWidth;
      const nearest = points.reduce((closest, point) => {
        return Math.abs(point.x - pointerX) < Math.abs(closest.x - pointerX) ? point : closest;
      });

      cursor.setAttribute('x1', nearest.x);
      cursor.setAttribute('x2', nearest.x);
      const [, month, day] = String(nearest.observation_date).split('-').map(Number);
      detail.textContent = `${month}월 ${day}일`;
      detail.setAttribute('visibility', 'visible');
      cursorValue.textContent = chartUtils.cursorValueText(nearest, PROFILE.cursorSeries);
      cursorValue.setAttribute('visibility', 'visible');
      chartUtils.positionCursorText(cursorValue, nearest.x, frame);
      chartUtils.positionCursorText(detail, nearest.x, frame);
      cursor.classList.add('is-visible');
    });

    frame.addEventListener('pointerleave', () => {
      cursor.classList.remove('is-visible');
      detail.setAttribute('visibility', 'hidden');
      cursorValue.setAttribute('visibility', 'hidden');
    });
  }

  async function load({ supabaseClient }) {
    const container = document.getElementById('korea-foreign-flow-chart');
    if (!container || !supabaseClient) {
      return;
    }

    const [derived, foreign, trading, fx] = await Promise.all([
      chartUtils.loadAllRows((from, to) => supabaseClient
        .from('korea_foreign_flow_daily')
        .select('observation_date,flow_index')
        .order('observation_date')
        .range(from, to)),
      chartUtils.loadAllRows((from, to) => supabaseClient
        .from('economic_chart_series_points')
        .select('observation_date,value')
        .eq('series_code', 'KR_FOREIGN_NET_BUY')
        .order('observation_date')
        .range(from, to)),
      chartUtils.loadAllRows((from, to) => supabaseClient
        .from('economic_chart_series_points')
        .select('observation_date,value')
        .eq('series_code', 'KOSPI_TRADING_VALUE')
        .order('observation_date')
        .range(from, to)),
      chartUtils.loadAllRows((from, to) => supabaseClient
        .from('economic_chart_series_points')
        .select('observation_date,value')
        .eq('series_code', 'USDKRW')
        .order('observation_date')
        .range(from, to)),
    ]);

    if ([derived, foreign, trading, fx].some((result) => result.error)) {
      container.innerHTML = '<div class="analysis-empty-state-light flex min-h-64 items-center justify-center border border-dashed p-5 text-sm text-slate-500">한국 외국인 자금 유출입 강도를 불러오지 못했습니다.</div>';
      return;
    }

    const merge = new Map((derived.data || []).map((row) => [String(row.observation_date), { ...row }]));
    for (const [result, key] of [[foreign, 'foreign_net_buy_amount'], [trading, 'kospi_trading_value'], [fx, 'usdkrw_rate']]) {
      for (const row of result.data || []) {
        merge.set(String(row.observation_date), {
          ...(merge.get(String(row.observation_date)) || { observation_date: row.observation_date }),
          [key]: Number(row.value),
        });
      }
    }

    const data = [...merge.values()]
      .filter((row) => {
        return Number.isFinite(Number(row.flow_index))
          && Number.isFinite(Number(row.foreign_net_buy_amount))
          && Number.isFinite(Number(row.kospi_trading_value))
          && Number.isFinite(Number(row.usdkrw_rate));
      })
      .sort((a, b) => String(a.observation_date).localeCompare(String(b.observation_date)));

    state.rows = applyHysteresis(calculateTenDayCumulative(data));
    updateRegimeLabel(state.rows);
    render(container, state.rows, state.selectedYears);

    const controls = document.querySelector('[data-korea-foreign-flow-ranges]');
    if (controls && controls.dataset.bound !== 'true') {
      controls.dataset.bound = 'true';
      controls.addEventListener('click', (event) => {
        const button = event.target.closest('[data-korea-foreign-flow-range]');
        if (!button) {
          return;
        }
        state.selectedYears = button.dataset.koreaForeignFlowRange === 'max'
          ? 'max'
          : Number(button.dataset.koreaForeignFlowRange);
        controls.querySelectorAll('[data-korea-foreign-flow-range]').forEach((item) => {
          item.classList.toggle('is-active', item === button);
        });
        render(container, state.rows, state.selectedYears);
      });
    }
  }

  window.addEventListener('macrowatch:dashboard-view-changed', ({ detail }) => {
    if (detail?.view === 'stress' && detail?.stressMarket === 'korea') {
      chartUtils.scrollToLatest(document.querySelector('#korea-foreign-flow-chart [data-history-scroll]'));
    }
  });

  window.MacroWatchDashboard?.registerLoader(load);
})();
