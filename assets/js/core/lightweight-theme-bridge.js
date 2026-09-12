(() => {
  'use strict';

  const originalLibrary = window.LightweightCharts;
  if (!originalLibrary?.createChart) return;

  const css = (name, fallback) => {
    try {
      return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
    } catch {
      return fallback;
    }
  };

  const themeOptions = () => {
    const dark = document.documentElement.dataset.theme === 'dark';
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
  const createChart = (container, options = {}) => {
    const chart = originalLibrary.createChart(container, mergeTheme(options));
    charts.add(chart);

    const originalRemove = chart.remove?.bind(chart);
    if (originalRemove) {
      chart.remove = () => {
        charts.delete(chart);
        originalRemove();
      };
    }
    return chart;
  };

  const proxy = new Proxy(originalLibrary, {
    get(target, property, receiver) {
      if (property === 'createChart') return createChart;
      return Reflect.get(target, property, receiver);
    },
  });

  try {
    window.LightweightCharts = proxy;
  } catch {
    try {
      Object.defineProperty(window, 'LightweightCharts', {
        configurable: true,
        writable: true,
        value: proxy,
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
  });
})();
