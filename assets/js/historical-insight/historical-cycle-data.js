(() => {
  'use strict';
  const caseFields = 'case_code,display_order,case_name,primary_index_code,comparison_index_codes,search_start,search_end,cycle_summary';
  const marketFields = 'case_code,index_code,start_date,peak_date,trough_date,cycle_status';
  const currentSettingsFields = 'setting_key,current_name';
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  const dateValue = value => value == null || value === '' ? null : String(value).slice(0, 10);

  function validDate(value) {
    if (!value || !iso.test(value)) return false;
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }

  function normalizeMarket(row) {
    const market = {
      caseCode: String(row?.case_code || ''), indexCode: String(row?.index_code || ''),
      startDate: dateValue(row?.start_date), peakDate: dateValue(row?.peak_date), troughDate: dateValue(row?.trough_date),
      status: String(row?.cycle_status || ''),
    };
    const indices = window.MacroWatchHistoricalData?.indices || {};
    const ordered = [market.startDate, market.peakDate, market.troughDate].filter(Boolean);
    if (!market.caseCode || !Object.hasOwn(indices, market.indexCode)
        || [market.startDate, market.peakDate, market.troughDate].some(value => value && !validDate(value))
        || ordered.some((value, index) => index && value < ordered[index - 1])
        || !['draft', 'confirmed', 'in_progress'].includes(market.status)
        || (market.status === 'confirmed' && (!market.startDate || !market.peakDate || !market.troughDate))) {
      throw new Error('시장별 사이클 정의를 확인해 주세요.');
    }
    return Object.freeze(market);
  }

  function normalizeCase(row, marketRows) {
    const item = {
      code: String(row?.case_code || ''), order: Number(row?.display_order), name: String(row?.case_name || ''),
      primaryIndex: String(row?.primary_index_code || ''), comparisons: Array.isArray(row?.comparison_index_codes) ? [...row.comparison_index_codes] : [],
      searchStart: dateValue(row?.search_start), searchEnd: dateValue(row?.search_end), summary: String(row?.cycle_summary || ''),
    };
    const indices = window.MacroWatchHistoricalData?.indices || {};
    if (!item.code || !item.name || !Number.isInteger(item.order) || item.order < 1 || !Object.hasOwn(indices, item.primaryIndex)
        || !validDate(item.searchStart) || (item.searchEnd && !validDate(item.searchEnd))
        || (item.searchEnd && item.searchStart > item.searchEnd)) {
      throw new Error('Historical Case 정의를 확인해 주세요.');
    }
    const markets = Object.fromEntries(marketRows.filter(market => market.caseCode === item.code).map(market => [market.indexCode, market]));
    if (!markets[item.primaryIndex]) throw new Error(`${item.name}의 대표 시장 사이클이 없습니다.`);
    return Object.freeze({ ...item, comparisons: Object.freeze(item.comparisons), markets: Object.freeze(markets) });
  }

  function createRepository(client) {
    let cache, settingsCache;
    return Object.freeze({
      async load() {
        if (!cache) cache = Promise.all([
          window.MacroWatchFrontend.queryAll(client, 'historical_cases', caseFields, 'display_order'),
          window.MacroWatchFrontend.queryAll(client, 'historical_case_market_cycles', marketFields, 'case_code'),
        ]).then(([caseRows, rawMarkets]) => {
          const markets = rawMarkets.map(normalizeMarket);
          return caseRows.map(row => normalizeCase(row, markets)).sort((left, right) => right.order - left.order);
        }).catch(error => { cache = null; throw error; });
        return cache;
      },
      async loadCurrentSettings() {
        if (!settingsCache) settingsCache = client.from('historical_current_settings').select(currentSettingsFields)
          .eq('setting_key', 'default').single().then(({ data, error }) => {
            if (error) throw error;
            const currentName = String(data?.current_name || '').trim();
            if (!currentName) throw new Error('현재 국면 설정을 확인해 주세요.');
            return Object.freeze({ currentName });
          }).catch(error => { settingsCache = null; throw error; });
        return settingsCache;
      },
      async saveCurrentName(code, value, userId) {
        const currentName = String(value || '').trim();
        if (!currentName || currentName.length > 60) throw new Error('국면명은 1~60자로 입력해 주세요.');
        if (code) {
          const { data, error } = await client.from('historical_cases').update({ case_name: currentName, updated_at: new Date().toISOString(), updated_by: userId })
            .eq('case_code', code).select(caseFields).single();
          if (error) throw error;
          const current = await this.load();
          cache = Promise.resolve(current.map(item => item.code === code ? normalizeCase(data, Object.values(item.markets)) : item));
        } else {
          const { error } = await client.from('historical_current_settings').update({ current_name: currentName, updated_at: new Date().toISOString(), updated_by: userId })
            .eq('setting_key', 'default');
          if (error) throw error;
          settingsCache = Promise.resolve(Object.freeze({ currentName }));
        }
        return currentName;
      },
      async save(code, indexCode, values, userId) {
        const payload = {
          start_date: values.startDate || null, peak_date: values.peakDate || null, trough_date: values.troughDate || null,
          cycle_status: values.troughDate ? 'confirmed' : values.startDate ? 'in_progress' : 'draft',
          updated_at: new Date().toISOString(), updated_by: userId,
        };
        const { data, error } = await client.from('historical_case_market_cycles').update(payload)
          .eq('case_code', code).eq('index_code', indexCode).select(marketFields).single();
        if (error) throw error;
        const saved = normalizeMarket(data), current = await this.load();
        cache = Promise.resolve(current.map(item => item.code !== code ? item : Object.freeze({
          ...item, markets: Object.freeze({ ...item.markets, [indexCode]: saved }),
        })));
        return saved;
      },
    });
  }

  function marketCycle(item, indexCode) {
    const cycle = item?.markets?.[indexCode];
    if (!cycle) throw new Error(`${window.MacroWatchHistoricalData?.indices?.[indexCode] || indexCode} 기준점이 없습니다.`);
    return cycle;
  }

  const point = (rows, date, required) => {
    if (!date) return null;
    const found = rows.find(row => row.time === date);
    if (!found && required) throw new Error(`기준일 ${date}의 종가가 없습니다.`);
    return found || null;
  };
  const days = (from, to) => from && to ? Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000) : null;

  function calculate(item, cycle, rows) {
    const ordered = [item.searchStart, cycle.startDate, cycle.peakDate, cycle.troughDate, item.searchEnd].filter(Boolean);
    if (ordered.some((value, index) => index && value < ordered[index - 1])
        || (cycle.peakDate && !cycle.startDate) || (cycle.troughDate && !cycle.peakDate)) {
      throw new Error('기준점은 관찰 범위 안에서 START → PEAK → TROUGH 순서여야 합니다.');
    }
    const start = point(rows, cycle.startDate, true), peak = point(rows, cycle.peakDate, true), trough = point(rows, cycle.troughDate, true);
    const rise = start && peak ? (peak.value / start.value - 1) * 100 : null;
    const fall = peak && trough ? (trough.value / peak.value - 1) * 100 : null;
    return Object.freeze({ start, peak, trough, rise, fall, drawdown: fall == null ? null : Math.abs(fall),
      riseDays: days(cycle.startDate, cycle.peakDate), fallDays: days(cycle.peakDate, cycle.troughDate) });
  }

  function chartPoints(cycle, rows) {
    return [['START', cycle.startDate], ['PEAK', cycle.peakDate], ['TROUGH', cycle.troughDate]]
      .map(([type, date]) => ({ type, date, row: point(rows, date, false) })).filter(item => item.row);
  }

  window.MacroWatchHistoricalCycles = Object.freeze({ caseFields, marketFields, currentSettingsFields, normalizeCase, normalizeMarket, createRepository, marketCycle, calculate, chartPoints });
})();
