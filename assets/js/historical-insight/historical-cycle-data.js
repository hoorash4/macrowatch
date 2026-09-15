(() => {
  'use strict';
  const fields = 'case_code,display_order,case_name,primary_index_code,comparison_index_codes,search_start,search_end,start_date,peak_date,trough_date,cycle_status,cycle_summary';
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  const dateValue = value => value == null || value === '' ? null : String(value).slice(0, 10);
  function validDate(value) {
    if (!value || !iso.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }
  function normalize(row) {
    const item = {
      code: String(row?.case_code || ''), order: Number(row?.display_order), name: String(row?.case_name || ''),
      primaryIndex: String(row?.primary_index_code || ''), comparisons: Array.isArray(row?.comparison_index_codes) ? [...row.comparison_index_codes] : [],
      searchStart: dateValue(row?.search_start), searchEnd: dateValue(row?.search_end),
      startDate: dateValue(row?.start_date), peakDate: dateValue(row?.peak_date), troughDate: dateValue(row?.trough_date),
      status: String(row?.cycle_status || ''), summary: String(row?.cycle_summary || ''),
    };
    const indices = window.MacroWatchHistoricalData?.indices || {};
    const ordered = [item.searchStart, item.startDate, item.peakDate, item.troughDate, item.searchEnd].filter(Boolean);
    if (!item.code || !item.name || !Number.isInteger(item.order) || item.order < 1 || !Object.hasOwn(indices, item.primaryIndex)
        || !validDate(item.searchStart) || (item.searchEnd && !validDate(item.searchEnd))
        || [item.startDate, item.peakDate, item.troughDate].some(value => value && !validDate(value))
        || ordered.some((value, index) => index && value < ordered[index - 1])
        || !['draft', 'confirmed', 'in_progress'].includes(item.status)
        || (item.status === 'confirmed' && (!item.startDate || !item.peakDate || !item.troughDate))) {
      throw new Error('Historical Case 정의를 확인해 주세요.');
    }
    return Object.freeze(item);
  }
  function createRepository(client) {
    let cache;
    return Object.freeze({
      async load() {
        if (!cache) cache = window.MacroWatchFrontend.queryAll(client, 'historical_cases', fields, 'display_order')
          .then(rows => rows.map(normalize)).catch(error => { cache = null; throw error; });
        return cache;
      },
      async save(code, values, userId) {
        const payload = {
          start_date: values.startDate || null, peak_date: values.peakDate || null, trough_date: values.troughDate || null,
          cycle_status: values.startDate ? values.peakDate && values.troughDate ? 'confirmed' : 'in_progress' : 'draft',
          updated_at: new Date().toISOString(), updated_by: userId,
        };
        const { data, error } = await client.from('historical_cases').update(payload).eq('case_code', code).select(fields).single();
        if (error) throw error;
        const saved = normalize(data), current = await this.load();
        cache = Promise.resolve(current.map(item => item.code === code ? saved : item));
        return saved;
      },
    });
  }
  const point = (rows, date, required) => {
    if (!date) return null;
    const found = rows.find(row => row.time === date);
    if (!found && required) throw new Error(`기준일 ${date}의 종가가 없습니다.`);
    return found || null;
  };
  const days = (from, to) => from && to ? Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000) : null;
  function calculate(item, primaryRows) {
    const ordered = [item.searchStart, item.startDate, item.peakDate, item.troughDate, item.searchEnd].filter(Boolean);
    if (ordered.some((value, index) => index && value < ordered[index - 1])
        || (item.peakDate && !item.startDate) || (item.troughDate && !item.peakDate)) {
      throw new Error('기준점은 관찰 범위 안에서 START → PEAK → TROUGH 순서여야 합니다.');
    }
    const start = point(primaryRows, item.startDate, true), peak = point(primaryRows, item.peakDate, true), trough = point(primaryRows, item.troughDate, true);
    const rise = start && peak ? (peak.value / start.value - 1) * 100 : null;
    const fall = peak && trough ? (trough.value / peak.value - 1) * 100 : null;
    return Object.freeze({ start, peak, trough, rise, fall, drawdown: fall == null ? null : Math.abs(fall),
      riseDays: days(item.startDate, item.peakDate), fallDays: days(item.peakDate, item.troughDate) });
  }
  function nearest(rows, date) {
    if (!date || !rows.length) return null;
    const target = Date.parse(`${date}T00:00:00Z`);
    let best = null, distance = Infinity;
    for (const row of rows) {
      const next = Math.abs(Date.parse(`${row.time}T00:00:00Z`) - target);
      if (next < distance) { best = row; distance = next; }
    }
    return distance <= 7 * 86400000 ? best : null;
  }
  function chartPoints(item, rows) {
    return [['START', item.startDate], ['PEAK', item.peakDate], ['TROUGH', item.troughDate]]
      .map(([type, date]) => ({ type, date, row: nearest(rows, date) })).filter(item => item.row);
  }
  window.MacroWatchHistoricalCycles = Object.freeze({ fields, normalize, createRepository, calculate, chartPoints });
})();
