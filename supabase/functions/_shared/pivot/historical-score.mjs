import {
  ANALYSIS_POLICY,
  coreWindow,
  darkWindow,
  days,
  dateOnly,
  shiftMonths
} from './historical-score-store.ts';

const TIMELINESS_WEIGHT = 40;
const RELATIONSHIP_WEIGHT = 30;
const CONTINUITY_WEIGHT = 30;

function valueAtDate(rows, date) {
  const exact = rows.find(row => dateOnly(row.observation_date) === date);
  if (exact) return Number(exact.value);
  const right = rows.findIndex(row => dateOnly(row.observation_date) > date);
  const before = rows[right - 1];
  const after = rows[right];
  if (!before || !after) return null;
  const from = Date.parse(`${dateOnly(before.observation_date)}T00:00:00Z`);
  const to = Date.parse(`${dateOnly(after.observation_date)}T00:00:00Z`);
  const at = Date.parse(`${date}T00:00:00Z`);
  return Number(before.value) + (Number(after.value) - Number(before.value)) * (at - from) / (to - from);
}

function manualDirectionMatches(pivot, type, pivots, rows, cycle, marketRows = []) {
  if (!pivot.isManual || !['positive', 'inverse'].includes(pivot.relationship) || pivot.keyReference) return true;
  if (pivot.designatedReference && pivot.designatedReference !== type) return false;
  if (!rows?.length) return true;

  const dayDistance = (a, b) => Math.abs(Date.parse(`${a}T00:00:00Z`) - Date.parse(`${b}T00:00:00Z`));
  const owner = pivot.designatedReference
    || [['START', cycle.startDate], ['PEAK', cycle.peakDate], ['TROUGH', cycle.troughDate]]
      .filter(([, date]) => date)
      .sort((a, b) => dayDistance(a[1], pivot.pivotDate) - dayDistance(b[1], pivot.pivotDate))[0]?.[0]
    || type;

  const end = owner === 'START' ? cycle.peakDate : owner === 'PEAK' ? cycle.troughDate : shiftMonths(cycle.troughDate, 24);
  const nextPivotStart = owner === 'TROUGH' ? (pivot.pivotDate > cycle.troughDate ? pivot.pivotDate : cycle.troughDate) : pivot.pivotDate;
  const next = pivots.find(candidate => candidate.pivotDate > nextPivotStart && candidate.pivotDate <= end);
  const to = next?.pivotDate || (owner === 'TROUGH' ? [...rows].reverse().find(row => dateOnly(row.observation_date) <= end) : null);
  const toDate = typeof to === 'string' ? to : to ? dateOnly(to.observation_date) : end;
  if (!toDate || toDate <= pivot.pivotDate) return false;

  const fromValue = valueAtDate(rows, pivot.pivotDate);
  const toValue = valueAtDate(rows, toDate);
  if (!Number.isFinite(fromValue) || !Number.isFinite(toValue)) return false;

  let direction = Math.sign(toValue - fromValue);
  if (!direction) {
    const previous = [...pivots].reverse().find(candidate => candidate.pivotDate < pivot.pivotDate);
    direction = previous ? Math.sign(fromValue - previous.pivotValue) : 0;
  }
  if (!direction) return true;

  const expected = owner === 'TROUGH' && marketRows.length
    ? indexDirection(marketRows, cycle.troughDate, toDate)
    : owner === 'PEAK' ? -1 : 1;
  if (!expected) return false;

  return direction === (pivot.relationship === 'positive' ? expected : -expected);
}

function indexDirection(marketRows, from, to) {
  const start = marketRows.find(row => dateOnly(row.market_date) === from);
  const finish = [...marketRows].reverse().find(row => dateOnly(row.market_date) <= to);
  return start && finish && dateOnly(finish.market_date) > from
    ? Math.sign(Number(finish.close) - Number(start.close)) : 0;
}

function classifyPivots(rows, cycle, manualKeys, observations, marketRows = []) {
  const refs = [['START', cycle.startDate], ['PEAK', cycle.peakDate], ['TROUGH', cycle.troughDate]].filter(([, date]) => date);
  const selected = new Map();
  for (const [type, date] of refs) {
    const window = coreWindow(date);
    const candidates = rows.filter(pivot => pivot.pivotDate >= window.from && pivot.pivotDate <= window.to
      && pivot.designatedReference !== 'UNCLEAR'
      && (!pivot.designatedReference || pivot.designatedReference === type)
      && manualDirectionMatches(pivot, type, rows, observations, cycle, marketRows))
      .sort((a, b) => Number(Boolean(b.isManual)) - Number(Boolean(a.isManual))
        || Math.abs(days(date, a.pivotDate)) - Math.abs(days(date, b.pivotDate))
        || a.pivotDate.localeCompare(b.pivotDate) || a.pivotOrder - b.pivotOrder);
    if (candidates.length) selected.set(type, candidates[0]);
  }
  return rows.map(pivot => {
    let selectedReferences = refs.filter(([type]) => selected.get(type) === pivot)
      .map(([type, date]) => ({type, date, offsetDays: days(date, pivot.pivotDate)}))
      .filter(ref => !pivot.keySuppressed && (!manualKeys.has(ref.type) || pivot.sourceDate === manualKeys.get(ref.type)));
    if (pivot.isManual && pivot.keyReference) {
      const date = cycle[`${pivot.keyReference.toLowerCase()}Date`];
      selectedReferences = [{type: pivot.keyReference, date, offsetDays: date ? days(date, pivot.pivotDate) : null}];
    }
    const extendedRefs = pivot.designatedReference === 'UNCLEAR'
      ? []
      : pivot.designatedReference
        ? refs.filter(([type]) => type === pivot.designatedReference)
        : refs;
    const extended = extendedRefs.map(([type, date]) => ({type, date, window: darkWindow(date), offsetDays: days(date, pivot.pivotDate)}))
      .filter(ref => pivot.pivotDate >= ref.window.from && pivot.pivotDate <= ref.window.to
        && manualDirectionMatches(pivot, ref.type, rows, observations, cycle, marketRows))
      .sort((a, b) => Math.abs(a.offsetDays) - Math.abs(b.offsetDays))[0];
    const wasConfirmed = refs.some(([type]) => selected.get(type) === pivot);
    const overridden = wasConfirmed && !selectedReferences.length;
    let markerStatus;
    if (pivot.designatedReference === 'UNCLEAR') {
      markerStatus = pivot.isVerified ? 'verified' : 'reference_only';
    } else if (pivot.isManual && pivot.keyReference) {
      markerStatus = 'confirmed';
    } else if (pivot.isManual) {
      if (selectedReferences.length) {
        markerStatus = 'confirmed';
      } else if (extended) {
        markerStatus = 'manual_standard';
      } else if (pivot.isVerified) {
        markerStatus = 'verified';
      } else {
        markerStatus = 'reference_only';
      }
    } else {
      markerStatus = overridden ? 'overridden_key' : selectedReferences.length ? 'confirmed' : extended ? 'near_miss' : 'reference_only';
    }
    return {
      ...pivot,
      selectedReferences,
      markerStatus,
      nearReferenceType: extended?.type || null,
      referenceType: pivot.designatedReference || selectedReferences[0]?.type || extended?.type || null
    };
  });
}

export function mergedPivots({automatic = [], manual = [], fallbackAutomatic = [], cycle, indexCode, observations = [], marketRows = []}) {
  const blocked = new Set(manual.map(pivot => dateOnly(pivot.source_date)));
  const occupied = new Set(manual.map(pivot => dateOnly(pivot.pivot_date)).filter(Boolean));
  const source = automatic.length ? automatic.map(row => ({
    pivotOrder: Number(row.pivot_order), pivotDate: dateOnly(row.pivot_date),
    pivotValue: Number(row.pivot_value), pivotType: String(row.pivot_type || ''),
    pivotReason: String(row.selection_reason || '')
  })) : fallbackAutomatic.filter(row => ['A', 'B', 'C'].includes(String(row.grade || '').toUpperCase()))
    .map((row, index) => ({pivotOrder: index, pivotDate: dateOnly(row.date), pivotValue: Number(row.value),
      pivotType: String(row.type || ''), pivotReason: String(row.reason || '')}));
  const automaticRows = [...new Map(source.filter(row => !blocked.has(row.pivotDate) && !occupied.has(row.pivotDate))
    .map(row => [row.pivotDate, row])).values()];
  const active = manual;
  const manualRows = active.map(row => {
    const rawKey = row.key_references?.[indexCode];
    const hasAnyVerified = Object.values(row.key_references || {}).some(val => val === 'VERIFIED' || (typeof val === 'string' && val.endsWith('_VERIFIED')));
    const isUnclear = rawKey === 'UNCLEAR' || rawKey === 'UNCLEAR_VERIFIED';
    const isVerified = Boolean(row.is_verified || rawKey === 'VERIFIED' || (typeof rawKey === 'string' && rawKey.endsWith('_VERIFIED')) || (!row.key_references && hasAnyVerified));
    const isKey = typeof rawKey === 'string' && ['START', 'PEAK', 'TROUGH'].includes(rawKey.replace('_VERIFIED', '')) && !rawKey.endsWith('_REF') && !isUnclear;
    const keyReference = isKey ? rawKey.replace('_VERIFIED', '') : null;
    const isNonKeyRef = typeof rawKey === 'string' && rawKey.endsWith('_REF');
    const isVerifiedRef = typeof rawKey === 'string' && rawKey !== 'VERIFIED' && rawKey.endsWith('_VERIFIED') && !isUnclear;
    const designatedReference = keyReference || (isNonKeyRef ? rawKey.replace('_REF', '') : isVerifiedRef ? rawKey.replace('_VERIFIED', '') : isUnclear ? 'UNCLEAR' : null);
    const override = row.index_overrides?.[indexCode] || {};
    const relationship = override.relationship || row.relationship;
    const reason = override.reason || [row.reason, row.comment].filter(Boolean).join('\n');
    return {
      pivotOrder: Number.MAX_SAFE_INTEGER,
      pivotDate: dateOnly(row.pivot_date),
      pivotValue: Number(row.pivot_value),
      pivotReason: reason,
      relationship: relationship,
      sourceDate: dateOnly(row.source_date),
      keyReference: keyReference,
      designatedReference,
      isVerified,
      keySuppressed: Boolean(row.keySuppressed || rawKey === false || isUnclear),
      isManual: true
    };
  });
  const keys = new Map(active.filter(row => row.key_references?.[indexCode] && ['START', 'PEAK', 'TROUGH'].includes(row.key_references[indexCode]))
    .map(row => [row.key_references[indexCode], dateOnly(row.source_date)]));
  return classifyPivots([...automaticRows, ...manualRows].sort((a, b) => a.pivotDate.localeCompare(b.pivotDate)), cycle, keys, observations, marketRows);
}

function timeliness(referenceDate, pivot) {
  if (!referenceDate || !pivot) return 0;
  const core = coreWindow(referenceDate), dark = darkWindow(referenceDate), date = pivot.pivotDate;
  if (pivot.markerStatus === 'confirmed') {
    if (date < core.from || date > core.to) return 0;
    return Math.floor(100 - 50 * days(core.from, date) / days(core.from, core.to));
  }
  if (!['near_miss', 'overridden_key', 'manual_standard'].includes(pivot.markerStatus)) return 0;
  if (date > core.from && date < core.to) return Math.floor((100 - 50 * days(core.from, date) / days(core.from, core.to)) * .5);
  if (date >= dark.from && date <= core.from) return Math.floor((50 + 49 * days(dark.from, date) / days(dark.from, core.from)) * .5);
  if (date >= core.to && date <= dark.to) return Math.floor((49 - 24 * days(core.to, date) / days(core.to, dark.to)) * .5);
  return 0;
}
function referencePivot(pivots, type, date) {
  const magenta = pivots.find(pivot => pivot.markerStatus === 'confirmed' && pivot.selectedReferences.some(ref => ref.type === type));
  if (magenta) return magenta;
  return pivots.filter(pivot => ['near_miss', 'manual_standard'].includes(pivot.markerStatus) && (pivot.referenceType === type || pivot.nearReferenceType === type))
    .sort((a, b) => timeliness(date, b) - timeliness(date, a) || Math.abs(days(date, a.pivotDate)) - Math.abs(days(date, b.pivotDate)))[0] || null;
}
function relationshipSuitability(referenceDate, nextReferenceDate, pivot, rows, cycle, marketRows = []) {
  if (!pivot) return 0;
  if (pivot.relationship === 'unclear') return 0;
  const windowDays = Math.max(1, days(referenceDate, nextReferenceDate));
  const pivotDays = days(referenceDate, pivot.pivotDate);
  const startDay = pivotDays < 0 ? pivotDays : 0;
  const length = Math.max(1, windowDays - startDay);
  let correctDays = 0;
  const points = [{date: referenceDate, value: valueAtDate(rows, referenceDate)},
    ...rows.filter(row => dateOnly(row.observation_date) > referenceDate && dateOnly(row.observation_date) <= nextReferenceDate)
      .map(row => ({date: dateOnly(row.observation_date), value: Number(row.value)}))];
  const direction = pivot.relationship === 'positive' ? 1 : -1;
  for (let i = 0; i < points.length - 1; i++) {
    const span = Math.max(0, days(points[i].date, points[i + 1].date));
    const delta = points[i + 1].value - points[i].value;
    if (Math.sign(delta) === direction) correctDays += span;
    else if (delta === 0) correctDays += span * .5;
  }
  return Math.min(100, Math.floor(100 * correctDays / length));
}
function continuity(referenceDate, nextReferenceDate, pivot, pivots, rows) {
  if (!pivot) return 0;
  const benchmarkDays = Math.max(1, days(referenceDate, nextReferenceDate));
  const intermediates = pivots.filter(point => point.pivotDate > pivot.pivotDate && point.pivotDate < nextReferenceDate);
  const boundary = intermediates[0]?.pivotDate || nextReferenceDate;
  const earnedDays = Math.max(0, days(pivot.pivotDate, boundary));
  const rawScore = Math.floor(100 * earnedDays / benchmarkDays);
  return ['near_miss', 'overridden_key', 'manual_standard'].includes(pivot.markerStatus) ? Math.floor(rawScore * .5) : rawScore;
}
function compositeScore(timelinessScore, relationshipScore, continuityScore) {
  return Math.floor((timelinessScore * TIMELINESS_WEIGHT + relationshipScore * RELATIONSHIP_WEIGHT + continuityScore * CONTINUITY_WEIGHT) / 100);
}

export function scoreReferences({pivots = [], rows = [], cycle, marketRows = []}) {
  const result = {};
  const references = [
    {type: 'START', date: cycle.startDate, nextDate: cycle.peakDate},
    {type: 'PEAK', date: cycle.peakDate, nextDate: cycle.troughDate},
    {type: 'TROUGH', date: cycle.troughDate, nextDate: shiftMonths(cycle.troughDate, 24)}
  ];
  let totalScore = 0;
  let count = 0;
  for (const ref of references) {
    if (!ref.date) {
      result[ref.type] = {
        score: 0, timelinessScore: 0, relationshipSuitabilityScore: 0, continuityScore: 0,
        pivotDate: null, pivotValue: null, relationship: 'unclear', markerStatus: null, offsetDays: null
      };
      continue;
    }
    const pivot = referencePivot(pivots, ref.type, ref.date);
    if (!pivot) {
      result[ref.type] = {
        score: 0, timelinessScore: 0, relationshipSuitabilityScore: 0, continuityScore: 0,
        pivotDate: null, pivotValue: null, relationship: 'unclear', markerStatus: null, offsetDays: null
      };
      continue;
    }
    const timelinessScore = timeliness(ref.date, pivot);
    const relationshipScore = relationshipSuitability(ref.date, ref.nextDate, pivot, rows, cycle, marketRows);
    const continuityScore = continuity(ref.date, ref.nextDate, pivot, pivots, rows);
    const score = compositeScore(timelinessScore, relationshipScore, continuityScore);
    result[ref.type] = {
      score,
      timelinessScore,
      relationshipSuitabilityScore: relationshipScore,
      continuityScore,
      pivotDate: pivot.pivotDate,
      pivotValue: pivot.pivotValue,
      relationship: pivot.relationship || 'unclear',
      markerStatus: pivot.markerStatus,
      offsetDays: days(ref.date, pivot.pivotDate)
    };
    totalScore += score;
    count += 1;
  }
  const darkPivots = pivots.filter(pivot => ['near_miss', 'manual_standard'].includes(pivot.markerStatus))
    .map(pivot => {
      const ref = references.find(item => item.type === (pivot.referenceType || pivot.nearReferenceType)) || references[0];
      const timelinessScore = timeliness(ref.date, pivot);
      const relationshipScore = relationshipSuitability(ref.date, ref.nextDate, pivot, rows, cycle, marketRows);
      const continuityScore = continuity(ref.date, ref.nextDate, pivot, pivots, rows);
      return {
        score: compositeScore(timelinessScore, relationshipScore, continuityScore),
        timelinessScore,
        relationshipSuitabilityScore: relationshipScore,
        continuityScore,
        pivotDate: pivot.pivotDate,
        pivotValue: pivot.pivotValue,
        relationship: pivot.relationship || 'unclear',
        markerStatus: pivot.markerStatus,
        offsetDays: ref.date ? days(ref.date, pivot.pivotDate) : null,
        referenceType: ref.type
      };
    });
  result.LIST = {
    score: count ? Math.floor(totalScore / count) : 0,
    darkPivots
  };
  return result;
}
