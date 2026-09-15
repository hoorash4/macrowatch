(() => {
  'use strict';

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const median = values => {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
  };
  const quantile = (values, q) => {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const position = (sorted.length - 1) * q;
    const base = Math.floor(position);
    const rest = position - base;
    return sorted[base + 1] === undefined
      ? sorted[base]
      : sorted[base] + rest * (sorted[base + 1] - sorted[base]);
  };
  const odd = value => value % 2 ? value : value + 1;

  function centeredMedian(values, width) {
    if (width <= 1) return [...values];
    const half = Math.floor(width / 2);
    return values.map((_, index) => median(values.slice(
      Math.max(0, index - half),
      Math.min(values.length, index + half + 1)
    )));
  }

  function regressionStats(values) {
    const n = values.length;
    if (n < 3) return { slope: 0, r2: 0, efficiency: 0, predictedSpan: 0 };
    const meanX = (n - 1) / 2;
    const meanY = values.reduce((sum, value) => sum + value, 0) / n;
    let covariance = 0;
    let varianceX = 0;
    let totalSquares = 0;
    for (let index = 0; index < n; index++) {
      const dx = index - meanX;
      const dy = values[index] - meanY;
      covariance += dx * dy;
      varianceX += dx * dx;
      totalSquares += dy * dy;
    }
    const slope = varianceX ? covariance / varianceX : 0;
    const intercept = meanY - slope * meanX;
    let residualSquares = 0;
    let travel = 0;
    for (let index = 0; index < n; index++) {
      const residual = values[index] - (intercept + slope * index);
      residualSquares += residual * residual;
      if (index) travel += Math.abs(values[index] - values[index - 1]);
    }
    const net = values.at(-1) - values[0];
    return {
      slope,
      r2: totalSquares ? clamp(1 - residualSquares / totalSquares, 0, 1) : 0,
      efficiency: travel ? Math.abs(net) / travel : 0,
      predictedSpan: Math.abs(slope) * (n - 1),
    };
  }

  function rawExtremeIndex(rows, center, radius, type) {
    const from = Math.max(0, center - radius);
    const to = Math.min(rows.length - 1, center + radius);
    let selected = from;
    for (let index = from + 1; index <= to; index++) {
      if (type === 'high' && rows[index].value > rows[selected].value) selected = index;
      if (type === 'low' && rows[index].value < rows[selected].value) selected = index;
    }
    return selected;
  }

  function isLocalExtreme(values, index, radius, type) {
    const from = Math.max(0, index - radius);
    const to = Math.min(values.length - 1, index + radius);
    const value = values[index];
    for (let i = from; i <= to; i++) {
      if (i === index) continue;
      if (type === 'high' && values[i] > value) return false;
      if (type === 'low' && values[i] < value) return false;
    }
    return true;
  }

  function transientSpike(rows, index, noise, robustRange) {
    if (index < 2 || index > rows.length - 3) return false;
    const left = median([rows[index - 2].value, rows[index - 1].value]);
    const right = median([rows[index + 1].value, rows[index + 2].value]);
    const baseline = (left + right) / 2;
    const excursion = Math.abs(rows[index].value - baseline);
    const sideGap = Math.abs(left - right);
    const floor = Math.max(noise * 5.5, robustRange * 0.06, 1e-12);
    return excursion >= floor && sideGap <= excursion * 0.34;
  }

  function localRegime(smoothed, index, coarseHalfWidth, fineHalfWidth, visibleRange, noise) {
    const coarseFrom = Math.max(0, index - coarseHalfWidth);
    const coarseTo = Math.min(smoothed.length - 1, index + coarseHalfWidth);
    const fineFrom = Math.max(0, index - fineHalfWidth);
    const fineTo = Math.min(smoothed.length - 1, index + fineHalfWidth);
    const coarse = smoothed.slice(coarseFrom, coarseTo + 1);
    const fine = smoothed.slice(fineFrom, fineTo + 1);
    if (coarse.length < 5 || fine.length < 3) {
      return { confidence: 0, state: 'choppy', direction: 0, multiplier: 0.78, radiusMultiplier: 0.8 };
    }

    const stats = regressionStats(coarse);
    const coarseRange = Math.max(quantile(coarse, 0.9) - quantile(coarse, 0.1), noise * 2, 1e-12);
    const directionalExtent = clamp(stats.predictedSpan / Math.max(coarseRange * 0.72, noise * 5, 1e-12), 0, 1);
    const third = Math.max(1, Math.floor(coarse.length / 3));
    const first = median(coarse.slice(0, third));
    const middle = median(coarse.slice(third, coarse.length - third));
    const last = median(coarse.slice(coarse.length - third));
    const direction = stats.slope > 0 ? 1 : stats.slope < 0 ? -1 : 0;
    const monotone = direction > 0
      ? first <= middle && middle <= last
      : direction < 0
        ? first >= middle && middle >= last
        : false;
    const trendConfidence = clamp(
      (stats.r2 * 0.58 + stats.efficiency * 0.42) * directionalExtent * (monotone ? 1 : 0.68) * 1.18,
      0,
      1
    );

    const fineStats = regressionStats(fine);
    const medianDrift = Math.max(first, middle, last) - Math.min(first, middle, last);
    const bandStability = clamp(1 - medianDrift / Math.max(coarseRange * 0.58, noise * 4, 1e-12), 0, 1);
    const compression = clamp(1 - coarseRange / Math.max(visibleRange * 0.34, noise * 10, 1e-12), 0, 1);
    const sidewaysConfidence = clamp(
      (1 - fineStats.efficiency) * (compression * 0.58 + bandStability * 0.42) * (1 - trendConfidence * 0.55),
      0,
      1
    );

    let state = 'choppy';
    let confidence = Math.max(trendConfidence, sidewaysConfidence);
    if (trendConfidence >= 0.5 && trendConfidence >= sidewaysConfidence * 1.05) state = 'trend';
    else if (sidewaysConfidence >= 0.54) state = 'sideways';

    if (state === 'trend') {
      return {
        confidence,
        state,
        direction,
        multiplier: clamp(1.05 + confidence * 0.7, 1.05, 1.75),
        radiusMultiplier: clamp(1.05 + confidence * 0.55, 1.05, 1.6),
      };
    }
    if (state === 'sideways') {
      return {
        confidence,
        state,
        direction: 0,
        multiplier: clamp(1.12 + confidence * 0.62, 1.12, 1.72),
        radiusMultiplier: clamp(1.1 + confidence * 0.55, 1.1, 1.62),
      };
    }
    const ambiguity = clamp(1 - Math.max(trendConfidence, sidewaysConfidence), 0, 1);
    return {
      confidence,
      state,
      direction: 0,
      multiplier: clamp(0.9 - ambiguity * 0.18, 0.72, 0.9),
      radiusMultiplier: clamp(0.92 - ambiguity * 0.22, 0.68, 0.92),
    };
  }

  function pivotFromWork(ctx, workIndex, type, source, strength = 1) {
    const { rows, work, smoothed, workStart, rawRadius, noise, robustRange, regimes, threshold, radius } = ctx;
    let rawIndex = rawExtremeIndex(work, workIndex, rawRadius, type);
    if (transientSpike(work, rawIndex, noise, robustRange)) {
      if (!transientSpike(work, workIndex, noise, robustRange)) rawIndex = workIndex;
      else return null;
    }
    const absoluteIndex = workStart + rawIndex;
    const point = rows[absoluteIndex];
    const regime = regimes[workIndex] || { confidence: 0, state: 'choppy', direction: 0, multiplier: 0.8, radiusMultiplier: 0.8 };
    return {
      type,
      index: absoluteIndex,
      date: point.time,
      value: point.value,
      requiredThreshold: threshold * regime.multiplier,
      localRadius: Math.max(2, Math.round(radius * regime.radiusMultiplier)),
      regimeConfidence: regime.confidence,
      regimeState: regime.state,
      regimeDirection: regime.direction,
      source,
      structureStrength: strength,
    };
  }

  function sameTypeRepresentative(a, b) {
    const better = b.type === 'high' ? b.value > a.value : b.value < a.value;
    if (better) return b;
    if (b.structureStrength > a.structureStrength * 1.15) return b;
    return a;
  }

  function normalizeAlternation(items) {
    const sorted = [...items].sort((a, b) => a.index - b.index);
    const out = [];
    for (const item of sorted) {
      const last = out.at(-1);
      if (!last || last.type !== item.type) {
        out.push(item);
      } else {
        out[out.length - 1] = sameTypeRepresentative(last, item);
      }
    }
    return out;
  }

  function coarseSkeleton(ctx) {
    const { smoothed, visibleLocalStart, visibleLocalEnd, coarseRadius, coarseThreshold } = ctx;
    const candidates = [];
    for (let index = Math.max(coarseRadius, visibleLocalStart); index <= Math.min(smoothed.length - coarseRadius - 1, visibleLocalEnd); index++) {
      const left = smoothed.slice(index - coarseRadius, index);
      const right = smoothed.slice(index + 1, index + coarseRadius + 1);
      if (!left.length || !right.length) continue;
      const value = smoothed[index];
      const leftMin = Math.min(...left);
      const rightMin = Math.min(...right);
      const leftMax = Math.max(...left);
      const rightMax = Math.max(...right);
      const highProminence = Math.min(value - leftMin, value - rightMin);
      const lowProminence = Math.min(leftMax - value, rightMax - value);
      if (value >= leftMax && value >= rightMax && highProminence >= coarseThreshold * 0.55) {
        const pivot = pivotFromWork(ctx, index, 'high', 'coarse', highProminence / coarseThreshold);
        if (pivot) candidates.push(pivot);
      }
      if (value <= leftMin && value <= rightMin && lowProminence >= coarseThreshold * 0.55) {
        const pivot = pivotFromWork(ctx, index, 'low', 'coarse', lowProminence / coarseThreshold);
        if (pivot) candidates.push(pivot);
      }
    }

    let normalized = normalizeAlternation(candidates);
    const minimumMove = coarseThreshold * 0.8;
    const reduced = [];
    for (const item of normalized) {
      const last = reduced.at(-1);
      if (!last) {
        reduced.push(item);
        continue;
      }
      if (Math.abs(item.value - last.value) < minimumMove) {
        if (item.type === last.type) reduced[reduced.length - 1] = sameTypeRepresentative(last, item);
        continue;
      }
      reduced.push(item);
    }
    normalized = normalizeAlternation(reduced);
    return normalized;
  }

  function persistentDeviation(residuals, index, sign, floor) {
    let left = index;
    let right = index;
    while (left - 1 >= 0 && sign * residuals[left - 1] >= floor) left--;
    while (right + 1 < residuals.length && sign * residuals[right + 1] >= floor) right++;
    return { width: right - left + 1, left, right };
  }

  function bestDeviationCandidate(ctx, leftAnchor, rightAnchor, depth) {
    const { smoothed, threshold, noise, robustRange, radius } = ctx;
    const a = leftAnchor.workIndex;
    const b = rightAnchor.workIndex;
    const length = b - a;
    const minSegment = Math.max(8, Math.round(ctx.visibleCount * 0.035), Math.round(radius * 1.25));
    if (length < minSegment * 2) return null;

    const segment = smoothed.slice(a, b + 1);
    const stats = regressionStats(segment);
    const segmentMove = Math.abs(smoothed[b] - smoothed[a]);
    const segmentRange = Math.max(quantile(segment, 0.9) - quantile(segment, 0.1), noise * 2, 1e-12);
    const trendQuality = clamp(stats.r2 * 0.58 + stats.efficiency * 0.42, 0, 1);
    const deviationFloor = Math.max(
      threshold * (0.78 + trendQuality * 0.42),
      segmentMove * (0.06 + trendQuality * 0.07),
      segmentRange * 0.085,
      robustRange * 0.012,
      noise * 3.4,
      1e-12
    );

    const residuals = [];
    for (let offset = 0; offset <= length; offset++) {
      const expected = smoothed[a] + (smoothed[b] - smoothed[a]) * (offset / length);
      residuals.push(smoothed[a + offset] - expected);
    }

    const localRadius = Math.max(2, Math.min(Math.round(length * 0.06), Math.max(2, Math.round(radius * 0.9))));
    const edgeGap = Math.max(localRadius, Math.round(length * 0.08));
    let best = null;

    for (let local = edgeGap; local <= length - edgeGap; local++) {
      const workIndex = a + local;
      const residual = residuals[local];
      const magnitude = Math.abs(residual);
      if (magnitude < deviationFloor * 0.72) continue;
      const type = residual > 0 ? 'high' : 'low';
      if (!isLocalExtreme(smoothed, workIndex, localRadius, type)) continue;

      const persistence = persistentDeviation(residuals, local, residual > 0 ? 1 : -1, deviationFloor * 0.35);
      const persistenceRatio = persistence.width / Math.max(2, Math.round(length * 0.08));
      const strength = magnitude / deviationFloor;
      const edgePenalty = Math.min(local, length - local) / Math.max(edgeGap, 1);
      const score = strength * (0.78 + 0.18 * Math.min(1.5, persistenceRatio)) * Math.min(1, edgePenalty);
      if (!best || score > best.score) {
        best = { workIndex, type, strength, score, persistenceWidth: persistence.width, deviationFloor };
      }
    }

    if (!best || best.score < 0.98) return null;
    const pivot = pivotFromWork(ctx, best.workIndex, best.type, 'deviation', best.strength);
    if (!pivot) return null;
    pivot.deviationStrength = best.strength;
    pivot.deviationPersistence = best.persistenceWidth;
    pivot.deviationDepth = depth;
    return pivot;
  }

  function recursiveDeviationSplit(ctx, leftAnchor, rightAnchor, out, depth = 0) {
    if (depth >= 6) return;
    const pivot = bestDeviationCandidate(ctx, leftAnchor, rightAnchor, depth);
    if (!pivot) return;
    const workIndex = pivot.index - ctx.workStart;
    if (workIndex <= leftAnchor.workIndex || workIndex >= rightAnchor.workIndex) return;
    const anchor = { workIndex, value: ctx.smoothed[workIndex] };
    recursiveDeviationSplit(ctx, leftAnchor, anchor, out, depth + 1);
    out.push(pivot);
    recursiveDeviationSplit(ctx, anchor, rightAnchor, out, depth + 1);
  }

  function topDownCandidates(ctx, coarse) {
    const anchors = [
      { workIndex: ctx.visibleLocalStart, value: ctx.smoothed[ctx.visibleLocalStart], boundary: true },
      ...coarse.map(pivot => ({ workIndex: pivot.index - ctx.workStart, value: pivot.value, pivot })),
      { workIndex: ctx.visibleLocalEnd, value: ctx.smoothed[ctx.visibleLocalEnd], boundary: true },
    ].sort((a, b) => a.workIndex - b.workIndex);

    const deduped = [];
    for (const anchor of anchors) {
      if (!deduped.length || anchor.workIndex !== deduped.at(-1).workIndex) deduped.push(anchor);
      else if (anchor.pivot) deduped[deduped.length - 1] = anchor;
    }

    const deviations = [];
    for (let index = 0; index < deduped.length - 1; index++) {
      recursiveDeviationSplit(ctx, deduped[index], deduped[index + 1], deviations, 0);
    }
    return [...coarse, ...deviations];
  }

  function continuationDirection(left, right) {
    if (left.type !== right.type) return 0;
    if (left.type === 'low' && right.value < left.value) return -1;
    if (left.type === 'high' && right.value > left.value) return 1;
    return 0;
  }

  function structurePrune(items) {
    let out = normalizeAlternation(items);
    let changed = true;
    while (changed && out.length >= 3) {
      changed = false;
      for (let index = 1; index < out.length - 1; index++) {
        const left = out[index - 1];
        const current = out[index];
        const right = out[index + 1];
        const leftMove = Math.abs(current.value - left.value);
        const rightMove = Math.abs(right.value - current.value);
        const excursion = Math.min(leftMove, rightMove);
        const dominant = Math.max(leftMove, rightMove, 1e-12);
        const threshold = Math.max((left.requiredThreshold + current.requiredThreshold + right.requiredThreshold) / 3, 1e-12);
        const direction = continuationDirection(left, right);
        const trendSupport = [left, current, right]
          .filter(item => item.regimeState === 'trend' && item.regimeDirection === direction)
          .reduce((sum, item) => sum + item.regimeConfidence, 0) / 3;
        const sidewaysSupport = [left, current, right]
          .filter(item => item.regimeState === 'sideways')
          .reduce((sum, item) => sum + item.regimeConfidence, 0) / 3;
        const choppyCount = [left, current, right].filter(item => item.regimeState === 'choppy').length;
        const protectedByStructure = current.source === 'coarse' || (current.source === 'deviation' && current.structureStrength >= 1.15);

        const correctionRatio = excursion / dominant;
        const trendContinuation = !protectedByStructure
          && direction !== 0
          && trendSupport >= 0.34
          && correctionRatio < 0.22 + trendSupport * 0.22
          && excursion < threshold * (1.12 + trendSupport * 0.48);

        const outerGap = Math.abs(right.value - left.value);
        const sidewaysNoise = !protectedByStructure
          && sidewaysSupport >= 0.3
          && outerGap <= threshold * 0.95
          && excursion < threshold * (1.25 + sidewaysSupport * 0.35);

        const choppyFloor = choppyCount >= 2 ? 0.68 : 1;
        const weak = !protectedByStructure && excursion < threshold * choppyFloor;
        const localGap = Math.min(current.index - left.index, right.index - current.index);
        const localRadius = Math.max(2, Math.round((left.localRadius + current.localRadius + right.localRadius) / 3));
        const tooTight = !protectedByStructure
          && localGap < localRadius * 0.58
          && excursion < threshold * (choppyCount >= 2 ? 1.02 : 1.35);

        if (!trendContinuation && !sidewaysNoise && !weak && !tooTight) continue;
        out.splice(index, 1);
        out = normalizeAlternation(out);
        changed = true;
        break;
      }
    }
    return out;
  }

  function detect(rows, { startIndex = 0, endIndex = null } = {}) {
    const count = rows.length;
    if (count < 5) {
      return Object.freeze({ pivots: Object.freeze([]), threshold: 0, radius: 0, smoothingWidth: 1, startIndex: 0, endIndex: Math.max(0, count - 1) });
    }

    const visibleStart = clamp(Math.floor(startIndex), 0, count - 1);
    const visibleEnd = clamp(Math.ceil(endIndex == null ? count - 1 : endIndex), visibleStart, count - 1);
    const visibleCount = Math.max(1, visibleEnd - visibleStart + 1);
    const buffer = Math.max(3, Math.round(visibleCount * 0.24));
    const workStart = Math.max(0, visibleStart - buffer);
    const workEnd = Math.min(count - 1, visibleEnd + buffer);
    const work = rows.slice(workStart, workEnd + 1);
    const visibleLocalStart = visibleStart - workStart;
    const visibleLocalEnd = visibleEnd - workStart;

    const smoothingWidth = odd(clamp(Math.round(visibleCount * 0.016), 1, 31));
    const smoothed = centeredMedian(work.map(row => row.value), smoothingWidth);
    const visibleValues = smoothed.slice(visibleLocalStart, visibleLocalEnd + 1);
    const robustRange = Math.max(quantile(visibleValues, 0.95) - quantile(visibleValues, 0.05), 1e-12);
    const diffs = [];
    for (let index = visibleLocalStart + 1; index <= visibleLocalEnd; index++) {
      diffs.push(Math.abs(smoothed[index] - smoothed[index - 1]));
    }
    const noise = Math.max(median(diffs), 1e-12);
    const threshold = Math.max(robustRange * 0.046, noise * 3.05, 1e-12);
    const radius = clamp(Math.round(visibleCount * 0.032), 2, Math.max(2, Math.round(visibleCount * 0.11)));
    const rawRadius = Math.max(1, Math.floor(smoothingWidth / 2));
    const coarseRadius = clamp(Math.round(visibleCount * 0.11), 4, Math.max(4, Math.round(visibleCount * 0.2)));
    const coarseThreshold = Math.max(robustRange * 0.105, noise * 5.2, threshold * 1.7);
    const coarseHalfWidth = clamp(Math.round(visibleCount * 0.14), 6, Math.max(6, Math.round(visibleCount * 0.24)));
    const fineHalfWidth = clamp(Math.round(visibleCount * 0.055), 3, Math.max(3, Math.round(visibleCount * 0.11)));
    const regimes = smoothed.map((_, index) => localRegime(smoothed, index, coarseHalfWidth, fineHalfWidth, robustRange, noise));

    const ctx = {
      rows,
      work,
      smoothed,
      workStart,
      visibleStart,
      visibleEnd,
      visibleLocalStart,
      visibleLocalEnd,
      visibleCount,
      robustRange,
      noise,
      threshold,
      radius,
      rawRadius,
      coarseRadius,
      coarseThreshold,
      regimes,
    };

    const coarse = coarseSkeleton(ctx);
    let candidates = topDownCandidates(ctx, coarse);
    candidates = candidates.filter(item => item.index >= visibleStart && item.index <= visibleEnd);
    candidates = normalizeAlternation(candidates);

    const accepted = [];
    for (const item of candidates) {
      const last = accepted.at(-1);
      if (!last) {
        accepted.push(item);
        continue;
      }
      if (last.type === item.type) {
        accepted[accepted.length - 1] = sameTypeRepresentative(last, item);
        continue;
      }
      const move = Math.abs(item.value - last.value);
      const pairThreshold = (last.requiredThreshold + item.requiredThreshold) / 2;
      const structural = item.source === 'coarse' || last.source === 'coarse' || item.structureStrength >= 1.12 || last.structureStrength >= 1.12;
      const choppy = item.regimeState === 'choppy' || last.regimeState === 'choppy';
      const floor = structural ? 0.62 : choppy ? 0.76 : 0.96;
      if (move < pairThreshold * floor) continue;
      accepted.push(item);
    }

    const pivots = structurePrune(accepted);
    const visibleRegimes = regimes.slice(visibleLocalStart, visibleLocalEnd + 1);
    const regimeCounts = visibleRegimes.reduce((acc, item) => {
      acc[item.state] = (acc[item.state] || 0) + 1;
      return acc;
    }, { trend: 0, sideways: 0, choppy: 0 });

    return Object.freeze({
      pivots: Object.freeze(pivots.map(item => Object.freeze({ ...item }))),
      threshold,
      radius,
      smoothingWidth,
      robustRange,
      noise,
      coarseCount: coarse.length,
      deviationCount: pivots.filter(item => item.source === 'deviation').length,
      regimeCounts: Object.freeze(regimeCounts),
      startIndex: visibleStart,
      endIndex: visibleEnd,
      startDate: rows[visibleStart]?.time || null,
      endDate: rows[visibleEnd]?.time || null,
    });
  }

  window.PivotLabEngine = Object.freeze({ detect });
})();
