(() => {
  'use strict';

  const YEAR_MS = 365.25 * 24 * 60 * 60 * 1000;
  const SCROLL_HISTORY_YEARS = 10;
  const FULL_HISTORY_SCROLL_RANGES = new Set([5, 10]);

  // 작은 진폭에서도 축이 과도하게 뭉개지지 않도록 일반적인 1·2·5 단계보다 촘촘한 눈금을 사용합니다.
  function niceStep(value) {
    const safeValue = Math.max(Math.abs(value), Number.EPSILON);
    const magnitude = 10 ** Math.floor(Math.log10(safeValue));
    const normalized = safeValue / magnitude;
    const factors = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10];
    return (factors.find((factor) => normalized <= factor) || 10) * magnitude;
  }

  function timelineWidth(viewportWidth, firstTimestamp, lastTimestamp, selectedYears) {
    if (selectedYears === 'max') return viewportWidth;
    return Math.max(viewportWidth, viewportWidth * ((lastTimestamp - firstTimestamp) / (Number(selectedYears) * YEAR_MS)));
  }

  function rowsForRecentHistory(rows, dateKey, selectedYears) {
    // 5년·10년은 선택 기간을 한 화면 폭으로 삼아 전체 이력을 탐색합니다.
    // MAX는 전체 기간을 한 화면에 압축하고, 짧은 범위는 최근 10년만 그립니다.
    if (selectedYears === 'max' || FULL_HISTORY_SCROLL_RANGES.has(selectedYears) || !rows.length) return rows;
    const latestDate = new Date(`${rows[rows.length - 1][dateKey]}T00:00:00Z`);
    const cutoff = new Date(latestDate);
    cutoff.setUTCFullYear(cutoff.getUTCFullYear() - SCROLL_HISTORY_YEARS);
    return rows.filter((row) => Date.parse(`${row[dateKey]}T00:00:00Z`) >= cutoff.getTime());
  }

  function scrollToLatest(frame) {
    if (!frame) return;
    window.requestAnimationFrame(() => { frame.scrollLeft = frame.scrollWidth - frame.clientWidth; });
  }

  // Supabase REST 조회는 프로젝트 설정과 무관하게 한 요청에서 반환되는 행 수가
  // 제한될 수 있으므로, 장기 일별 시계열은 마지막 페이지까지 나누어 읽습니다.
  async function loadAllRows(fetchPage, pageSize = 1000) {
    const rows = [];
    for (let from = 0; ; from += pageSize) {
      const { data, error } = await fetchPage(from, from + pageSize - 1);
      if (error) return { data: null, error };
      const page = data || [];
      rows.push(...page);
      if (page.length < pageSize) return { data: rows, error: null };
    }
  }

  window.MacroWatchAnalysisChart = { niceStep, timelineWidth, rowsForRecentHistory, scrollToLatest, loadAllRows };
})();
