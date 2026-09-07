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


  function historyWidth(rows, dateKey, years, baseWidth = 920) {
    const dates = rows.map(row => Date.parse(row[dateKey])).filter(Number.isFinite);
    return dates.length ? timelineWidth(baseWidth, Math.min(...dates), Math.max(...dates), years) : baseWidth;
  }

  function visibleAxisDomain(points, left, right, symmetric = false) {
    // Include the neighbouring samples at viewport edges so crossing curves fit too.
    const values = points.filter(point => Number.isFinite(point.value)
      && point.x >= left && point.x <= right).map(point => point.value);
    const before = points.filter(point => point.x < left && Number.isFinite(point.value)).at(-1);
    const after = points.find(point => point.x > right && Number.isFinite(point.value));
    points.forEach(point => {
      if (Number.isFinite(point.value) && (point.x === before?.x || point.x === after?.x)) values.push(point.value);
    });
    if (!values.length) return null;
    const min = Math.min(...values), max = Math.max(...values);
    const margin = Math.max((max - min) * .12, Math.max(Math.abs(min), Math.abs(max)) * .02, .0001);
    if (symmetric) {
      const extent = Math.max(Math.abs(min), Math.abs(max)) + margin;
      return { min: -extent, max: extent };
    }
    return { min: min - margin, max: max + margin };
  }

  let axisClipSequence = 0;
  function bindVisibleAxes(svg, frame, shell, width, config) {
    const { top, bottom, axes } = config;
    const ns = 'http://www.w3.org/2000/svg';
    const clip = document.createElementNS(ns, 'clipPath');
    clip.id = `visible-axis-${++axisClipSequence}`;
    const rectangle = document.createElementNS(ns, 'rect');
    Object.entries({ x: 0, y: top, width, height: bottom - top }).forEach(([key, value]) => rectangle.setAttribute(key, value));
    clip.append(rectangle);
    const defs = document.createElementNS(ns, 'defs');
    defs.append(clip); svg.append(defs);
    const bindings = axes.map(axis => ({
      ...axis,
      paths: [...svg.querySelectorAll(axis.selector)].filter(node => node.tagName === 'path').map(node => {
        node.setAttribute('clip-path', `url(#${clip.id})`);
        return { node, original: node.getAttribute('d') || '' };
      }),
      dots: [...svg.querySelectorAll(axis.selector)].filter(node => node.tagName === 'circle').map(node => ({ node, original: Number(node.getAttribute('cy')) })),
      labels: [...shell.querySelectorAll('svg text')].filter(node => {
        const x = Number(node.getAttribute('x'));
        const pixel = Number(node.getAttribute('y')) - 3;
        if (pixel < top - 1 || pixel > bottom + 1) return false;
        return axis.side === 'right' ? x > width - 52 : x <= 55;
      }).map(node => ({ node, pixel: Number(node.getAttribute('y')) - 3 })),
    }));
    let pending = null;
    const update = () => {
      pending = null;
      if (!frame.clientWidth || !svg.getBoundingClientRect().width) return;
      shell.querySelectorAll(':scope > svg').forEach(axis => { axis.style.height = `${svg.getBoundingClientRect().height}px`; });
      const unitsPerPixel = width / svg.getBoundingClientRect().width;
      const left = frame.scrollLeft * unitsPerPixel;
      const right = (frame.scrollLeft + frame.clientWidth) * unitsPerPixel;
      bindings.forEach(axis => {
        const domain = visibleAxisDomain(axis.points, left, right, axis.symmetric);
        if (!domain) return;
        const inverted = axis.y(1) > axis.y(0);
        const map = value => top + (inverted ? value - domain.min : domain.max - value) / (domain.max - domain.min) * (bottom - top);
        const a = (map(1) - map(0)) / (axis.y(1) - axis.y(0));
        const b = map(0) - a * axis.y(0);
        axis.paths.forEach(({ node, original }) => {
          let coordinate = 0;
          node.setAttribute('d', original.replace(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi, number => (++coordinate % 2 ? number : (Number(number) * a + b).toFixed(2))));
        });
        axis.dots.forEach(({ node, original }) => node.setAttribute('cy', original * a + b));
        axis.labels.forEach(({ node, pixel }) => {
          const ratio = (pixel - top) / (bottom - top);
          const value = inverted ? domain.min + ratio * (domain.max - domain.min) : domain.max - ratio * (domain.max - domain.min);
          node.textContent = axis.format ? axis.format(value) : value.toLocaleString('en-US', { maximumFractionDigits: Math.abs(value) < 100 ? 2 : 0 });
        });
      });
    };
    const schedule = () => { if (pending === null) pending = window.requestAnimationFrame(update); };
    frame.addEventListener('scroll', schedule, { passive: true });
    new ResizeObserver(schedule).observe(frame);
    schedule();
  }

  function scrollableSvg(svg, width, baseWidth = 920, axes = null) {
    if (!svg) return;
    const frame = document.createElement('div');
    const shell = document.createElement('div');
    shell.style.cssText = 'position:relative;width:100%;min-width:0;background:#fff;';
    frame.dataset.historyScroll = 'true';
    frame.style.cssText = 'overflow-x:auto;overflow-y:hidden;width:100%;min-width:0;background:#fff;';
    frame.tabIndex = 0;
    frame.setAttribute('aria-label', '전체 이력 가로 스크롤');
    svg.before(shell);
    shell.append(frame);
    frame.append(svg);
    svg.style.width = `${width / baseWidth * 100}%`;
    svg.style.maxWidth = 'none';
    svg.style.display = 'block';

    // Y축은 SVG 내부에 있으면 최신 구간으로 스크롤할 때 함께 화면 밖으로 나간다.
    // 축의 라벨과 세로선만 별도 SVG에 복제해 왼쪽에 고정한다.
    const [,, viewWidth, viewHeight] = (svg.getAttribute('viewBox') || '').trim().split(/\s+/).map(Number);
    const axisNodes = [...svg.querySelectorAll('text,line')].filter((node) => {
      const x = Number(node.getAttribute('x'));
      const x1 = Number(node.getAttribute('x1'));
      const x2 = Number(node.getAttribute('x2'));
      return (node.tagName === 'text' && Number.isFinite(x) && x <= 55)
        || (node.tagName === 'line' && Number.isFinite(x1) && x1 === x2 && x1 <= 65);
    });
    if (axisNodes.length && Number.isFinite(viewHeight)) {
      const fixedAxis = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      fixedAxis.setAttribute('viewBox', `0 0 72 ${viewHeight}`);
      fixedAxis.setAttribute('aria-hidden', 'true');
      fixedAxis.style.cssText = 'position:absolute;z-index:2;left:0;top:0;width:72px;height:100%;background:#fff;pointer-events:none;';
      axisNodes.forEach((node) => {
        fixedAxis.append(node.cloneNode(true));
        node.setAttribute('visibility', 'hidden');
      });
      shell.append(fixedAxis);
    }

    if (axes) bindVisibleAxes(svg, frame, shell, width, axes);

    // Relative width works even when a dashboard panel starts hidden.
    const observer = new ResizeObserver(() => {
      if (frame.clientWidth > 0) {
        scrollToLatest(frame);
        observer.disconnect();
      }
    });
    observer.observe(frame);
    frame.addEventListener('scroll', () => {
      const card = frame.closest('[data-dashboard-panel]');
      if (!card) return;
      const ratio = frame.scrollLeft / Math.max(1, frame.scrollWidth - frame.clientWidth);
      card.querySelectorAll('[data-history-scroll]').forEach(peer => {
        if (peer !== frame) {
          const target = ratio * Math.max(0, peer.scrollWidth - peer.clientWidth);
          if (Math.abs(peer.scrollLeft - target) > 1) peer.scrollLeft = target;
        }
      });
    });
  }

  window.MacroWatchAnalysisChart = { niceStep, visibleAxisDomain, historyWidth, scrollableSvg, timelineWidth, rowsForRecentHistory, scrollToLatest, loadAllRows, monotonePath, monotonePathSegments };
})();

