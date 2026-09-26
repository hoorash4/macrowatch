export const SCORE_VERSION = 'historical-pivot-4-3-3-v12';
const DAY = 86400000;
const dateOnly = value => String(value || '').slice(0, 10);
const day = value => Math.floor(Date.parse(`${dateOnly(value)}T00:00:00Z`) / DAY);
const days = (from, to) => day(to) - day(from);
export function shiftMonths(value, amount) {
  const [year, month, date] = dateOnly(value).split('-').map(Number);
  const target = new Date(Date.UTC(year, month - 1 + amount, 1));
  const last = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(date, last));
  return target.toISOString().slice(0, 10);
}
const coreWindow = date => ({from: shiftMonths(date, -3), to: shiftMonths(date, 1)});
const darkWindow = date => ({from: shiftMonths(date, -6), to: shiftMonths(date, 2)});

function manualDirectionMatches(pivot, type, pivots, observations, cycle, marketRows = []) {
  if (!pivot.isManual || !['positive', 'inverse'].includes(pivot.relationship) || pivot.keyReference) return true;
  if (pivot.designatedReference && pivot.designatedReference !== type) return false;
  if (!observations.length) return true;
  const owner = pivot.designatedReference
    || [['START', cycle.startDate], ['PEAK', cycle.peakDate], ['TROUGH', cycle.troughDate]]
      .filter(([, date]) => date).sort((a, b) => Math.abs(days(a[1], pivot.pivotDate)) - Math.abs(days(b[1], pivot.pivotDate)))[0]?.[0] || type;
  const bufferEnd = shiftMonths(cycle.troughDate, 24);
  const end = owner === 'START' ? cycle.peakDate : owner === 'PEAK' ? cycle.troughDate : bufferEnd;
  const nextPivotStart = owner === 'TROUGH' ? (pivot.pivotDate > cycle.troughDate ? pivot.pivotDate : cycle.troughDate) : pivot.pivotDate;
  const next = pivots.find(candidate => candidate.pivotDate > nextPivotStart && candidate.pivotDate <= end);
  const to = next?.pivotDate || (owner === 'TROUGH' ? lastObservationDate(observations, bufferEnd) : end);
  if (!to || to <= pivot.pivotDate) return false;
  const fromValue = valueAt(observations, pivot.pivotDate), toValue = valueAt(observations, to);
  if (!Number.isFinite(fromValue) || !Number.isFinite(toValue)) return false;
  let direction = Math.sign(toValue - fromValue);
  if (!direction) {
    const previous = [...pivots].reverse().find(candidate => candidate.pivotDate < pivot.pivotDate);
    direction = previous ? Math.sign(fromValue - previous.pivotValue) : 0;
  }
  if (!direction) return true;
  const expected = owner === 'TROUGH' ? marketDirection(marketRows, cycle.troughDate, to)
    : owner === 'PEAK' ? -1 : 1;
  if (!expected) return false;
  return direction === (pivot.relationship === 'positive' ? expected : -expected);
}

function lastObservationDate(rows, end) {
  return [...rows].reverse().find(row => dateOnly(row.observation_date) <= end)?.observation_date?.slice(0, 10) || null;
}
function marketDirection(rows, from, to) {
  if (!rows.length) return 1;
  const start = rows.find(row => dateOnly(row.market_date) === from);
  const finish = [...rows].reverse().find(row => dateOnly(row.market_date) <= to);
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
    const isVerified = rawKey === 'VERIFIED' || (typeof rawKey === 'string' && rawKey.endsWith('_VERIFIED')) || hasAnyVerified;
    const isKey = ['START', 'PEAK', 'TROUGH'].includes(rawKey) || (typeof rawKey === 'string' && ['START_VERIFIED', 'PEAK_VERIFIED', 'TROUGH_VERIFIED'].includes(rawKey));
    const keyReference = isKey ? String(rawKey).replace('_VERIFIED', '') : null;
    const isUnclear = rawKey === 'UNCLEAR' || rawKey === 'UNCLEAR_VERIFIED';
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
  return pivots.filter(pivot => ['near_miss', 'overridden_key', 'manual_standard'].includes(pivot.markerStatus) && timeliness(date, pivot) > 0)
    .sort((a, b) => timeliness(date, b) - timeliness(date, a))[0] || null;
}
const intermediates = (pivots, from, to) => pivots.filter(pivot => pivot.markerStatus === 'reference_only' && pivot.pivotDate > from && pivot.pivotDate < to)
  .sort((a, b) => a.pivotDate.localeCompare(b.pivotDate));
function valueAt(rows, date) {
  const exact = rows.find(row => dateOnly(row.observation_date) === date);
  if (exact) return Number(exact.value);
  const right = rows.findIndex(row => dateOnly(row.observation_date) > date);
  if (right < 1) return null;
  const before = rows[right - 1], after = rows[right];
  const a = day(before.observation_date), b = day(after.observation_date);
  return Number(before.value) + (Number(after.value) - Number(before.value)) * (day(date) - a) / (b - a);
}
function relationship({pivots, rows, from, to, benchmarkEnd, expectedDirection, factor, manualRelationship, startingPivot}) {
  const totalDays = days(from, benchmarkEnd);
  if (totalDays <= 0 || !factor || manualRelationship === 'unclear') return {relationship: manualRelationship || 'unclear', score: 0};
  const coveredFrom = startingPivot?.pivotDate || from;
  if (coveredFrom >= to) return {relationship: manualRelationship || 'unclear', score: 0};
  const points = [coveredFrom, ...intermediates(pivots, coveredFrom, to).map(pivot => pivot.pivotDate), to];
  const segments = [];
  for (let i = 1; i < points.length; i++) {
    const a = valueAt(rows, points[i - 1]), b = valueAt(rows, points[i]);
    if (!Number.isFinite(a) || !Number.isFinite(b)) return {relationship: 'unclear', score: 0};
    segments.push({direction: Math.sign(b - a), days: days(points[i - 1], points[i])});
  }
  const startValue = valueAt(rows, coveredFrom);
  const priorPivots = pivots.filter(pivot => pivot.pivotDate < coveredFrom)
    .sort((a, b) => b.pivotDate.localeCompare(a.pivotDate));
  const entryDirection = priorPivots.map(pivot => Math.sign(startValue - pivot.pivotValue))
    .find(direction => direction) || 0;
  const savedDirection = manualRelationship === 'positive' ? expectedDirection
    : manualRelationship === 'inverse' ? -expectedDirection : 0;
  const totals = {up: 0, down: 0};
  for (let i = 0; i < segments.length; i++) {
    const segment = segments[i];
    let direction = segment.direction;
    if (!direction) direction = segments.slice(0, i).reverse().find(value => value.direction)?.direction
      || entryDirection || savedDirection;
    if (direction > 0) totals.up += segment.days * (segment.direction ? 1 : .5);
    if (direction < 0) totals.down += segment.days * (segment.direction ? 1 : .5);
  }
  const matching = expectedDirection > 0 ? totals.up : totals.down;
  const opposing = expectedDirection > 0 ? totals.down : totals.up;
  const automatic = matching === opposing ? 'unclear' : matching > opposing ? 'positive' : 'inverse';
  const relation = manualRelationship || automatic;
  const aligned = relation === 'positive' ? matching : relation === 'inverse' ? opposing : 0;
  return {relationship: relation, score: Math.floor(100 * Math.min(1, aligned / totalDays) * factor)};
}

export function scoreReferences({pivots, rows, cycle, marketRows = []}) {
  const dates = {START: cycle.startDate, PEAK: cycle.peakDate, TROUGH: cycle.troughDate};
  if (!dates.START || !dates.PEAK || !dates.TROUGH) return {START: null, PEAK: null, TROUGH: null};
  const selected = Object.fromEntries(Object.entries(dates).map(([type, date]) => [type, referencePivot(pivots, type, date)]));
  const bufferEnd = shiftMonths(dates.TROUGH, 24), result = {};
  function scorePivot(type, pivot) {
    const from = dates[type], benchmarkEnd = type === 'START' ? dates.PEAK : type === 'PEAK' ? dates.TROUGH : bufferEnd;
    const firstAfterTrough = type === 'TROUGH' ? pivots.filter(candidate => candidate.pivotDate > (pivot?.pivotDate > from ? pivot.pivotDate : from)
      && candidate.pivotDate <= bufferEnd).sort((a, b) => a.pivotDate.localeCompare(b.pivotDate))[0] : null;
    const to = type === 'TROUGH' ? firstAfterTrough?.pivotDate || lastObservationDate(rows, bufferEnd) : benchmarkEnd;
    const factor = pivot?.markerStatus === 'confirmed' ? 1 : pivot ? .5 : 0;
    const relation = pivot ? relationship({pivots, rows, from, to, benchmarkEnd: type === 'TROUGH' ? to : benchmarkEnd,
      expectedDirection: type === 'TROUGH' ? marketDirection(marketRows, from, to) : type === 'PEAK' ? -1 : 1,
      factor, manualRelationship: pivot.isManual ? pivot.relationship : null,
      startingPivot: pivot})
      : {relationship: 'unclear', score: 0};
    const continuityStart = type === 'TROUGH' ? from : pivot?.pivotDate;
    const next = pivot ? intermediates(pivots, continuityStart, benchmarkEnd)[0] : null;
    const total = continuityStart ? days(continuityStart, benchmarkEnd) : 0;
    const active = pivot ? Math.max(0, days(continuityStart, next?.pivotDate || benchmarkEnd)) : 0;
    const continuity = pivot && total > 0 ? Math.floor(100 * Math.min(1, active / total) * (pivot.markerStatus === 'confirmed' ? 1 : .5)) : 0;
    const timing = timeliness(from, pivot);
    return {referenceDate: from, pivotDate: pivot?.pivotDate || null, pivotValue: pivot?.pivotValue ?? null,
      markerStatus: pivot?.markerStatus || null, offsetDays: pivot ? days(from, pivot.pivotDate) : null,
      relationship: relation.relationship, timelinessScore: timing, relationshipSuitabilityScore: relation.score,
      continuityScore: continuity, score: Math.floor(timing * .4 + relation.score * .3 + continuity * .3),
      pivotReason: pivot?.pivotReason || ''};
  }
  for (const type of ['START', 'PEAK', 'TROUGH']) result[type] = scorePivot(type, selected[type]);
  const weights = {START: 150, PEAK: 200, TROUGH: 150};
  const darkPivots = pivots.filter(pivot => ['near_miss', 'overridden_key', 'manual_standard'].includes(pivot.markerStatus)
    && pivot.nearReferenceType && timeliness(dates[pivot.nearReferenceType], pivot) > 0)
    .map(pivot => ({referenceType: pivot.nearReferenceType, ...scorePivot(pivot.nearReferenceType, pivot)}));
  const placementScore = Object.keys(weights).reduce((total, type) => total + (result[type].markerStatus === 'confirmed'
    ? weights[type] : result[type].pivotDate ? weights[type] / 2 : 0), 0);
  const pivotScore = Object.keys(weights).reduce((total, type) => total + result[type].score, 0);
  const extraDarkTieBreak = darkPivots.reduce((total, pivot) => total + (selected[pivot.referenceType]?.pivotDate === pivot.pivotDate
    ? 0 : weights[pivot.referenceType] / 2 + pivot.score), 0);
  const rawScore = placementScore + pivotScore;
  result.LIST = {score: Math.round(rawScore / 800 * 100), rawScore, placementScore, pivotScore,
    extraDarkTieBreak, darkPivots};
  return result;
}
