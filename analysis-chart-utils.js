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

  // 한 시계열의 접선은 전체 이웃점을 기준으로 계산하고, 반환 단계에서만
  // 원하는 선분을 분리한다. 따라서 선분의 스타일이 달라도 곡률은 바뀌지 않는다.
  function monotonePathSegments(points, edgeKeys = []) {
    const result = [];
    let run = [];
    const flush = () => {
      if (run.length === 1) {
        result.push({ key: edgeKeys[run[0].index] ?? 'default', path: `M ${run[0].x.toFixed(2)} ${run[0].y.toFixed(2)}` });
        run = [];
        return;
      }
      if (!run.length) return;
      const slopes = run.slice(1).map((point, index) => {
        const dx = point.x - run[index].x;
        return dx > 0 ? (point.y - run[index].y) / dx : 0;
      });
      const tangents = run.map((_, index) => index === 0 ? slopes[0] : index === run.length - 1 ? slopes.at(-1) : (slopes[index - 1] + slopes[index]) / 2);
      slopes.forEach((slope, index) => {
        if (slope === 0) { tangents[index] = 0; tangents[index + 1] = 0; return; }
        let left = tangents[index] / slope, right = tangents[index + 1] / slope;
        if (left < 0) tangents[index] = left = 0;
        if (right < 0) tangents[index + 1] = right = 0;
        const magnitude = Math.hypot(left, right);
        if (magnitude > 3) {
          const scale = 3 / magnitude;
          tangents[index] = scale * left * slope;
          tangents[index + 1] = scale * right * slope;
        }
      });
      let active = null;
      run.slice(1).forEach((point, index) => {
        const previous = run[index], dx = point.x - previous.x;
        const curve = `C ${(previous.x + dx / 3).toFixed(2)} ${(previous.y + tangents[index] * dx / 3).toFixed(2)} ${(point.x - dx / 3).toFixed(2)} ${(point.y - tangents[index + 1] * dx / 3).toFixed(2)} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`;
        const key = edgeKeys[point.index] ?? 'default';
        if (!active || active.key !== key) {
          active = { key, path: `M ${previous.x.toFixed(2)} ${previous.y.toFixed(2)} ${curve}` };
          result.push(active);
        } else active.path += ` ${curve}`;
      });
      run = [];
    };
    (points || []).forEach((point, index) => {
      if (Number.isFinite(point?.x) && Number.isFinite(point?.y)) run.push({ ...point, index });
      else flush();
    });
    flush();
    return result;
  }

  function monotonePath(points) {
    return monotonePathSegments(points).map((segment) => segment.path).join(' ');
  }

  window.MacroWatchAnalysisChart = { niceStep, timelineWidth, rowsForRecentHistory, scrollToLatest, loadAllRows, monotonePath, monotonePathSegments };
})();
