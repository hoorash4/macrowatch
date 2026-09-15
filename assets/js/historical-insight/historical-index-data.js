(() => {
  'use strict';
  // 지수 식별과 원천 조회의 단일 기준. 사례와 파생 계산은 여기 넣지 않습니다.
  const indices = Object.freeze({ SP500: 'S&P 500', NASDAQ_COMPOSITE: 'NASDAQ Composite', KOSPI: 'KOSPI' });
  const startDate = '1990-01-01';
  function normalize(rows) {
    let previous = '';
    return rows.map(row => {
      const time = row.market_date;
      const value = row.close === null || row.close === undefined || row.close === '' ? NaN : Number(row.close);
      const parsed = typeof time === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(time) ? new Date(time) : null;
      if (!parsed || !Number.isFinite(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== time
          || time < startDate || time <= previous || !Number.isFinite(value) || value <= 0) {
        throw new Error('시장지수 원천 데이터의 날짜 또는 종가를 확인해 주세요.');
      }
      previous = time;
      return { time, value };
    });
  }
  function createRepository(client) {
    const cache = new Map();
    return Object.freeze({
      async load(code) {
        if (!Object.hasOwn(indices, code)) throw new Error('지원하지 않는 시장지수입니다.');
        if (!cache.has(code)) {
          const pending = window.MacroWatchFrontend.queryAll(client, 'market_index_prices',
            'market_date,close', 'market_date', [['index_code', code]], query => query.gte('market_date', startDate))
            .then(normalize).catch(error => { cache.delete(code); throw error; });
          cache.set(code, pending);
        }
        return cache.get(code);
      },
    });
  }
  window.MacroWatchHistoricalData = Object.freeze({ indices, startDate, normalize, createRepository });
})();
