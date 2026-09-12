(() => {
  'use strict';

  const library = window.LightweightCharts;
  if (!library?.createChart || library.createChart.__macroWatchBackgroundCompat) return;

  const solidType = library.ColorType?.Solid || 'solid';
  const normalizeBackground = (options = {}) => {
    const background = options?.layout?.background;
    if (!background) return options;
    return {
      ...options,
      layout: {
        ...options.layout,
        background: {
          ...background,
          type: background.type || solidType,
        },
      },
    };
  };

  const originalCreateChart = library.createChart.bind(library);
  const wrappedCreateChart = (container, options = {}) => {
    const chart = originalCreateChart(container, normalizeBackground(options));
    const originalApplyOptions = chart.applyOptions?.bind(chart);
    if (originalApplyOptions) {
      chart.applyOptions = (nextOptions = {}) => originalApplyOptions(normalizeBackground(nextOptions));
    }
    return chart;
  };

  wrappedCreateChart.__macroWatchBackgroundCompat = true;
  library.createChart = wrappedCreateChart;
})();
