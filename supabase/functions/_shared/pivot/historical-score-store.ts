import { mergedPivots, scoreReferences, shiftMonths, SCORE_VERSION } from './historical-score.mjs';

function canonicalJson(value: any): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort()
    .map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  return JSON.stringify(value);
}

async function allRows(query: any) {
  const result: any[] = [];
  for (let from = 0;; from += 1000) {
    const { data, error } = await query.range(from, from + 999);
    if (error) throw error;
    result.push(...(data || []));
    if (!data || data.length < 1000) return result;
  }
}

export async function recomputeHistoricalScore(admin: any, caseCode: string, indexCode: string, seriesCode: string) {
  const [{data: caseRow, error: caseError}, {data: cycleRow, error: cycleError},
    {data: scoreRow, error: scoreError}] = await Promise.all([
    admin.from('historical_cases').select('primary_index_code,pivot_source_index_code').eq('case_code', caseCode).single(),
    admin.from('historical_case_market_cycles').select('start_date,peak_date,trough_date')
      .eq('case_code', caseCode).eq('index_code', indexCode).single(),
    admin.from('historical_indicator_ai_scores').select('by_reference,scoring_version,auto_pivots,ai_regimes,ai_anomalies,source_analyzed_at')
      .eq('case_code', caseCode).eq('index_code', indexCode).eq('series_code', seriesCode).maybeSingle(),
  ]);
  if (caseError || cycleError || scoreError) throw caseError || cycleError || scoreError;
  const pivotSourceIndex = caseRow.pivot_source_index_code || caseRow.primary_index_code;
  const cycle = {startDate: String(cycleRow.start_date || '').slice(0, 10),
    peakDate: String(cycleRow.peak_date || '').slice(0, 10), troughDate: String(cycleRow.trough_date || '').slice(0, 10)};
  if (!cycle.startDate || !cycle.peakDate || !cycle.troughDate) return {skipped: true, reason: 'incomplete_cycle'};
  const [automatic, manual, points, marketRows, analysis] = await Promise.all([
    allRows(admin.from('historical_indicator_pivots').select('pivot_order,pivot_date,pivot_value,pivot_type,selection_reason')
      .eq('case_code', caseCode).eq('index_code', pivotSourceIndex).eq('series_code', seriesCode).order('pivot_order')),
    allRows(admin.from('historical_indicator_manual_pivots')
      .select('source_date,pivot_date,pivot_value,relationship,reason,comment,key_references')
      .eq('case_code', caseCode).eq('series_code', seriesCode).order('source_date')),
    allRows(admin.from('economic_chart_series_points').select('observation_date,value')
      .eq('series_code', seriesCode).gte('observation_date', shiftMonths(cycle.startDate, -6))
      .lte('observation_date', shiftMonths(cycle.troughDate, 24)).order('observation_date')),
    allRows(admin.from('market_index_prices').select('market_date,close')
      .eq('index_code', indexCode).gte('market_date', cycle.troughDate)
      .lte('market_date', shiftMonths(cycle.troughDate, 24)).order('market_date')),
    admin.from('historical_indicator_ai_analysis').select('pivots,regimes,anomalies,analyzed_at')
      .eq('case_code', caseCode).eq('index_code', pivotSourceIndex).eq('series_code', seriesCode).maybeSingle(),
  ]);
  if (analysis.error) throw analysis.error;
  if (!points.length) return {skipped: true, reason: 'no_observations'};
  const blockedDates = new Set(manual.map(row => String(row.source_date).slice(0, 10)));
  const fallbackAutoPivots = (analysis.data?.pivots || scoreRow?.auto_pivots || [])
    .filter((row: any) => !blockedDates.has(String(row.date).slice(0, 10)));
  const pivots = mergedPivots({automatic, manual, fallbackAutomatic: fallbackAutoPivots, cycle, indexCode, observations: points, marketRows});
  const byReference = scoreReferences({pivots, rows: points, cycle, marketRows});
  const identical = scoreRow?.scoring_version === SCORE_VERSION
    && canonicalJson(scoreRow.by_reference) === canonicalJson(byReference)
    && canonicalJson(scoreRow.auto_pivots) === canonicalJson(fallbackAutoPivots);
  if (identical) return {skipped: true, reason: 'unchanged', byReference};
  const now = new Date().toISOString();
  const payload: any = {case_code: caseCode, index_code: indexCode, series_code: seriesCode,
    by_reference: byReference, scoring_version: SCORE_VERSION, auto_pivots: fallbackAutoPivots,
    ai_regimes: analysis.data?.regimes || scoreRow?.ai_regimes || [],
    ai_anomalies: analysis.data?.anomalies || scoreRow?.ai_anomalies || [],
    source_analyzed_at: analysis.data?.analyzed_at || scoreRow?.source_analyzed_at || now,
    updated_at: now, scored_at: now};
  const {error} = await admin.from('historical_indicator_ai_scores')
    .upsert(payload, {onConflict: 'case_code,index_code,series_code'});
  if (error) throw error;
  return {saved: true, byReference};
}
