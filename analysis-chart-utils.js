(() => {
  'use strict';

  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;
  const SCROLL_HISTORY_YEARS = 10;

  // 작은 진폭에서도 축이 과도하게 뭉개지지 않도록 일반적인 1·2·5 단계보다 촘촘한 눈금을 사용합니다.
  function niceStep(value) {
    const safeValue = Math.max(Math.abs(value), Number.EPSILON);
    const magnitude = 10 ** Math.floor(Math.log10(safeValue));
    const normalized = safeValue / magnitude;
    const factors = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
    return (factors.find((factor) => normalized <= factor) || 10) * magnitude;
  }

  function timelineWidth(viewportWidth, firstTimestamp, lastTimestamp, selectedYears) {
    if (selectedYears === 'max' || selectedYears === 10) return viewportWidth;
    return Math.max(viewportWidth, viewportWidth * ((lastTimestamp - firstTimestamp) / (Number(selectedYears) * YEAR_MS)));
  }

  function rowsForRecentHistory(rows, dateKey, selectedYears) {
    if (selectedYears === 'max' || !rows.length) return rows;
    const latestDate = new Date(`${rows[rows.length - 1][dateKey]}T00:00:00Z`);
    const cutoff = new Date(latestDate);
    cutoff.setUTCFullYear(cutoff.getUTCFullYear() - SCROLL_HISTORY_YEARS);
    return rows.filter((row) => Date.parse(`${row[dateKey]}T00:00:00Z`) >= cutoff.getTime());
  }

  function scrollToLatest(frame) {
    if (!frame) return;
    window.requestAnimationFrame(() => { frame.scrollLeft = frame.scrollWidth - frame.clientWidth; });
  }

  window.MacroWatchAnalysisChart = { niceStep, timelineWidth, rowsForRecentHistory, scrollToLatest };
})();
