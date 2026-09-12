(() => {
  'use strict';

  const originalLibrary = window.LightweightCharts;
  if (!originalLibrary?.createChart) return;

  const ECONOMIC_RAW_LIGHT = '#111827';
  const ECONOMIC_RAW_DARK = '#ffffff';

  const isDark = () => document.documentElement.dataset.theme === 'dark';
  const css = (name, fallback) => {
    try {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    } catch {
      return fallback;
    }
  };

  const themeOptions = () => {
    const dark = isDark();
    return {
      layout: {
        background: {
          type: originalLibrary.ColorType?.Solid || 'solid',
          color: css('--theme-chart-bg', dark ? '#0b1322' : '#ffffff'),
        },
        textColor: css('--theme-chart-text', dark ? '#aebbd0' : '#6b7280'),
      },
      grid: {
        vertLines: { color: css('--theme-chart-grid', dark ? '#243247' : '#e5e7eb') },
        horzLines: { color: css('--theme-chart-grid', dark ? '#243247' : '#e5e7eb') },
      },
      rightPriceScale: { borderColor: css('--theme-chart-border', dark ? '#40536b' : '#d1d5db') },
      timeScale: { borderColor: css('--theme-chart-border', dark ? '#40536b' : '#d1d5db') },
      crosshair: {
        vertLine: { color: css('--theme-chart-crosshair', dark ? '#7f91aa' : '#9ca3af') },
        horzLine: { color: css('--theme-chart-crosshair', dark ? '#7f91aa' : '#9ca3af') },
      },
    };
  };

  const mergeTheme = (options = {}) => {
    const theme = themeOptions();
    return {
      ...options,
      layout: { ...(options.layout || {}), ...theme.layout },
      grid: {
        ...(options.grid || {}),
        vertLines: { ...(options.grid?.vertLines || {}), ...theme.grid.vertLines },
        horzLines: { ...(options.grid?.horzLines || {}), ...theme.grid.horzLines },
      },
      rightPriceScale: { ...(options.rightPriceScale || {}), ...theme.rightPriceScale },
      timeScale: { ...(options.timeScale || {}), ...theme.timeScale },
      crosshair: {
        ...(options.crosshair || {}),
        vertLine: { ...(options.crosshair?.vertLine || {}), ...theme.crosshair.vertLine },
        horzLine: { ...(options.crosshair?.horzLine || {}), ...theme.crosshair.horzLine },
      },
    };
  };

  const charts = new Set();
  const rawSeries = new Set();

  const themedCreateChart = (container, options = {}) => {
    const chart = originalLibrary.createChart(container, mergeTheme(options));
    charts.add(chart);

    const originalAddLineSeries = chart.addLineSeries?.bind(chart);
    if (originalAddLineSeries) {
      chart.addLineSeries = (seriesOptions = {}) => {
        const isEconomicRaw = String(seriesOptions.color || '').toLowerCase() === ECONOMIC_RAW_LIGHT;
        const series = originalAddLineSeries({
          ...seriesOptions,
          color: isEconomicRaw && isDark() ? ECONOMIC_RAW_DARK : seriesOptions.color,
        });
        if (isEconomicRaw) rawSeries.add(series);
        return series;
      };
    }

    const originalRemove = chart.remove?.bind(chart);
    if (originalRemove) {
      chart.remove = () => {
        charts.delete(chart);
        originalRemove();
      };
    }
    return chart;
  };

  const replacement = {};
  for (const key of Reflect.ownKeys(originalLibrary)) {
    if (key === 'createChart') continue;
    replacement[key] = originalLibrary[key];
  }
  replacement.createChart = themedCreateChart;

  try {
    window.LightweightCharts = replacement;
  } catch {
    try {
      Object.defineProperty(window, 'LightweightCharts', {
        configurable: true,
        writable: true,
        value: replacement,
      });
    } catch {
      return;
    }
  }

  window.addEventListener('macrowatch:themechange', () => {
    const options = themeOptions();
    charts.forEach((chart) => {
      try { chart.applyOptions(options); } catch { charts.delete(chart); }
    });
    const color = isDark() ? ECONOMIC_RAW_DARK : ECONOMIC_RAW_LIGHT;
    rawSeries.forEach((series) => {
      try { series.applyOptions({ color }); } catch { rawSeries.delete(series); }
    });
  });
})();
