(() => {
  'use strict';
  const fields = 'case_code,index_code,anchor_type,anchor_order,anchor_date,series_code,fact_label,category,unit,decimals,display_order,observation_date,available_date,value,observation_age_days';
  const anchors = new Set(['start', 'peak', 'trough']);
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  const dateValue = value => value == null ? null : String(value).slice(0, 10);

  function normalize(row) {
    const value = row?.value == null ? null : Number(row.value);
    const fact = {
      caseCode: String(row?.case_code || ''), indexCode: String(row?.index_code || ''),
      anchorType: String(row?.anchor_type || ''), anchorOrder: Number(row?.anchor_order), anchorDate: dateValue(row?.anchor_date),
      seriesCode: String(row?.series_code || ''), label: String(row?.fact_label || ''), category: String(row?.category || ''),
      unit: String(row?.unit || ''), decimals: Number(row?.decimals), order: Number(row?.display_order),
      observationDate: dateValue(row?.observation_date), availableDate: dateValue(row?.available_date), value,
      ageDays: row?.observation_age_days == null ? null : Number(row.observation_age_days),
    };
    if (!fact.caseCode || !fact.indexCode || !anchors.has(fact.anchorType) || !iso.test(fact.anchorDate || '')
        || !fact.seriesCode || !fact.label || !fact.category || !Number.isInteger(fact.anchorOrder)
        || !Number.isInteger(fact.order) || !Number.isInteger(fact.decimals) || fact.decimals < 0
        || (value != null && (!Number.isFinite(value) || !iso.test(fact.observationDate || '') || !iso.test(fact.availableDate || '')
          || fact.availableDate > fact.anchorDate || !Number.isInteger(fact.ageDays) || fact.ageDays < 0))) {
      throw new Error('기준점 팩트 데이터를 확인해 주세요.');
    }
    return Object.freeze(fact);
  }

  function createRepository(client) {
    return Object.freeze({
      async load(caseCode, indexCode) {
        const { data, error } = await client.from('historical_case_anchor_facts').select(fields)
          .eq('case_code', caseCode).eq('index_code', indexCode)
          .order('anchor_order', { ascending: true }).order('display_order', { ascending: true });
        if (error) throw error;
        return (data || []).map(normalize);
      },
    });
  }

  window.MacroWatchHistoricalFacts = Object.freeze({ fields, normalize, createRepository });
})();
