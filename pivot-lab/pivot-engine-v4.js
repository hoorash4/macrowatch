(() => {
  'use strict';

  const baseEngine = window.PivotLabEngine;
  if (!baseEngine?.detect) throw new Error('Pivot Lab v3 engine must load before v4.');

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
  const median = values => {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  };
  const quantile = (values, q) => {
    if (!values.length) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const pos = (sorted.length - 1) * q;
    const base = Math.floor(pos);
    const rest = pos - base;
    return sorted[base + 1] === undefined ? sorted[base] : sorted[base] + rest * (sorted[base + 1] - sorted[base]);
  };
  const odd = value => value % 2 ? value : value + 1;

  function smooth(values, width) {
    if (width <= 1) return [...values];
    const half = Math.floor(width / 2);
    return values.map((_, index) => median(values.slice(Math.max(0, index - half), Math.min(values.length, index + half + 1))));
  }

  function efficiency(values) {
    if (values.length < 2) return 0;
    let travel = 0;
    for (let i = 1; i < values.length; i++) travel += Math.abs(values[i] - values[i - 1]);
    return travel ? Math.abs(values.at(-1) - values[0]) / travel : 0;
  }

  function localExtreme(rows, center, radius, type) {
    const from = Math.max(0, center - radius);
    const to = Math.min(rows.length - 1, center + radius);
    let selected = from;
    for (let i = from + 1; i <= to; i++) {
      if (type === 'high' && rows[i].value > rows[selected].value) selected = i;
      if (type === 'low' && rows[i].value < rows[selected].value) selected = i;
    }
    return selected;
  }

  function makePivot(rows, index, type, source, threshold, radius, strength = 1) {
    const rawIndex = localExtreme(rows, index, Math.max(1, Math.round(radius * 0.45)), type);
    const point = rows[rawIndex];
    return {
      type,
      index: rawIndex,
      date: point.time,
      value: point.value,
      source,
      structureStrength: strength,
      requiredThreshold: threshold,
      localRadius: radius,
      regimeConfidence: 1,
      regimeState: source === 'sideways-boundary' ? 'sideways' : 'choppy',
      regimeDirection: 0,
    };
  }

  function samePivot(a, b) {
    return a.type === b.type && Math.abs(a.index - b.index) <= 1;
  }

  function mergePivots(base, extras) {
    const all = [...base.map(item => ({ ...item })), ...extras].sort((a, b) => a.index - b.index);
    const out = [];
    for (const item of all) {
      const duplicate = out.find(existing => samePivot(existing, item));
      if (duplicate) {
        const protectedItem = item.source === 'sideways-boundary' || item.source === 'deviation-final';
        const protectedExisting = duplicate.source === 'sideways-boundary' || duplicate.source === 'deviation-final';
        if (protectedItem && !protectedExisting) Object.assign(duplicate, item);
        continue;
      }
      out.push(item);
    }
    return out.sort((a, b) => a.index - b.index);
  }

  function detectSidewaysBoundaries(rows, ctx) {
    const { start, end, visibleCount, robustRange, noise, radius, threshold, smoothed } = ctx;
    const half = clamp(Math.round(visibleCount * 0.035), 3, Math.max(3, Math.round(visibleCount * 0.08)));
    const minimumRun = Math.max(4, Math.round(visibleCount * 0.025));
    const mask = new Array(rows.length).fill(false);

    for (let index = start + half; index <= end - half; index++) {
      const values = smoothed.slice(index - half, index + half + 1);
      const localRange = Math.max(quantile(values, 0.9) - quantile(values, 0.1), 1e-12);
      const eff = efficiency(values);
      const net = Math.abs(values.at(-1) - values[0]);
      const directionalRatio = net / localRange;
      const compressed = localRange <= Math.max(robustRange * 0.22, noise * 8);
      mask[index] = eff <= 0.34 && directionalRatio <= 0.48 && compressed;
    }

    const zones = [];
    let runStart = null;
    for (let index = start; index <= end + 1; index++) {
      const active = index <= end && mask[index];
      if (active && runStart == null) runStart = index;
      if ((!active || index === end) && runStart != null) {
        const runEnd = active && index === end ? index : index - 1;
        if (runEnd - runStart + 1 >= minimumRun) {
          zones.push({ start: Math.max(start, runStart - half), end: Math.min(end, runEnd + half) });
        }
        runStart = null;
      }
    }

    const extras = [];
    for (const zone of zones) {
      if (zone.end - zone.start < minimumRun) continue;
      const beforeFrom = Math.max(start, zone.start - half);
      const before = smoothed.slice(beforeFrom, zone.start + 1);
      const afterTo = Math.min(end, zone.end + half);
      const after = smoothed.slice(zone.end, afterTo + 1);
      const beforeSlope = before.length > 1 ? before.at(-1) - before[0] : 0;
      const afterSlope = after.length > 1 ? after.at(-1) - after[0] : 0;
      const slopeFloor = Math.max(noise * 2.5, robustRange * 0.012, 1e-12);

      let startType = null;
      if (beforeSlope <= -slopeFloor) startType = 'low';
      else if (beforeSlope >= slopeFloor) startType = 'high';

      let endType = null;
      if (afterSlope >= slopeFloor) endType = 'low';
      else if (afterSlope <= -slopeFloor) endType = 'high';

      if (startType) extras.push(makePivot(rows, zone.start, startType, 'sideways-boundary', threshold, radius, 1.35));
      if (endType) extras.push(makePivot(rows, zone.end, endType, 'sideways-boundary', threshold, radius, 1.35));
    }
    return extras;
  }

  function persistentWidth(residuals, index, sign, floor) {
    let left = index;
    let right = index;
    while (left > 0 && sign * residuals[left - 1] >= floor) left--;
    while (right < residuals.length - 1 && sign * residuals[right + 1] >= floor) right++;
    return right - left + 1;
  }

  function findDeviationPivot(rows, left, right, ctx) {
    const { threshold, robustRange, noise, radius, smoothed, visibleCount } = ctx;
    const a = left.index;
    const b = right.index;
    const length = b - a;
    const minSegment = Math.max(8, Math.round(visibleCount * 0.03), Math.round(radius * 1.2));
    if (length < minSegment * 2) return null;

    const segment = smoothed.slice(a, b + 1);
    const segmentMove = Math.abs(smoothed[b] - smoothed[a]);
    const segmentRange = Math.max(quantile(segment, 0.9) - quantile(segment, 0.1), noise * 2, 1e-12);
    const deviationFloor = Math.max(
      threshold * 0.82,
      segmentMove * 0.07,
      segmentRange * 0.075,
      robustRange * 0.012,
      noise * 3.2,
      1e-12
    );

    const residuals = [];
    for (let offset = 0; offset <= length; offset++) {
      const expected = smoothed[a] + (smoothed[b] - smoothed[a]) * (offset / length);
      residuals.push(smoothed[a + offset] - expected);
    }

    const edgeGap = Math.max(3, Math.round(length * 0.07));
    const localRadius = Math.max(2, Math.min(Math.round(length * 0.045), Math.max(2, radius)));
    let best = null;

    for (let local = edgeGap; local <= length - edgeGap; local++) {
      const absolute = a + local;
      const residual = residuals[local];
      const magnitude = Math.abs(residual);
      if (magnitude < deviationFloor * 0.72) continue;
      const type = residual > 0 ? 'high' : 'low';
      const extreme = localExtreme(rows, absolute, localRadius, type);
      if (Math.abs(extreme - absolute) > localRadius) continue;
      const width = persistentWidth(residuals, local, residual > 0 ? 1 : -1, deviationFloor * 0.32);
      const persistenceScore = clamp(width / Math.max(2, Math.round(length * 0.06)), 0, 1.5);
      const strength = magnitude / deviationFloor;
      const score = strength * (0.8 + 0.2 * persistenceScore);
      if (!best || score > best.score) best = { absolute, type, width, strength, score };
    }

    if (!best || best.score < 1.02) return null;
    const pivot = makePivot(rows, best.absolute, best.type, 'deviation-final', threshold, radius, best.strength);
    pivot.deviationStrength = best.strength;
    pivot.deviationPersistence = best.width;
    return pivot;
  }

  function refineFinalSegments(rows, pivots, ctx) {
    let out = [...pivots].sort((a, b) => a.index - b.index);
    const maxRounds = 6;
    const maxAdditions = Math.max(4, Math.min(18, Math.round(ctx.visibleCount / 24)));
    let additions = 0;

    for (let round = 0; round < maxRounds && additions < maxAdditions; round++) {
      let added = false;
      for (let index = 0; index < out.length - 1 && additions < maxAdditions; index++) {
        const left = out[index];
        const right = out[index + 1];
        const candidate = findDeviationPivot(rows, left, right, ctx);
        if (!candidate) continue;
        if (out.some(item => Math.abs(item.index - candidate.index) <= Math.max(1, Math.round(ctx.radius * 0.35)))) continue;
        out.push(candidate);
        out.sort((a, b) => a.index - b.index);
        additions++;
        added = true;
        break;
      }
      if (!added) break;
    }
    return { pivots: out, additions };
  }

  function buildContext(rows, startIndex, endIndex, baseResult) {
    const start = clamp(Math.floor(startIndex ?? 0), 0, rows.length - 1);
    const end = clamp(Math.ceil(endIndex ?? rows.length - 1), start, rows.length - 1);
    const visibleCount = Math.max(1, end - start + 1);
    const smoothingWidth = odd(clamp(Math.round(visibleCount * 0.014), 1, 25));
    const smoothed = smooth(rows.map(row => row.value), smoothingWidth);
    const visible = smoothed.slice(start, end + 1);
    const robustRange = Math.max(quantile(visible, 0.95) - quantile(visible, 0.05), 1e-12);
    const diffs = [];
    for (let i = start + 1; i <= end; i++) diffs.push(Math.abs(smoothed[i] - smoothed[i - 1]));
    const noise = Math.max(median(diffs), 1e-12);
    return {
      start,
      end,
      visibleCount,
      smoothed,
      robustRange,
      noise,
      threshold: Math.max(baseResult.threshold || 0, robustRange * 0.042, noise * 2.9, 1e-12),
      radius: Math.max(2, baseResult.radius || Math.round(visibleCount * 0.03)),
    };
  }

  function detect(rows, options = {}) {
    const baseResult = baseEngine.detect(rows, options);
    if (!rows.length || baseResult.pivots.length < 2) return baseResult;

    const ctx = buildContext(rows, options.startIndex, options.endIndex, baseResult);
    const sidewaysBoundaries = detectSidewaysBoundaries(rows, ctx);
    let pivots = mergePivots(baseResult.pivots, sidewaysBoundaries)
      .filter(item => item.index >= ctx.start && item.index <= ctx.end);

    const refined = refineFinalSegments(rows, pivots, ctx);
    pivots = mergePivots(refined.pivots, []);

    return Object.freeze({
      ...baseResult,
      pivots: Object.freeze(pivots.map(item => Object.freeze({ ...item }))),
      sidewaysBoundaryCount: sidewaysBoundaries.length,
      finalDeviationAdditions: refined.additions,
    });
  }

  window.PivotLabEngine = Object.freeze({ detect });
})();
