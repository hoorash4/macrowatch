(() => {
  'use strict';
  const INDEX_CODES=new Set(['SP500','NASDAQ_COMPOSITE','KOSPI']);
  const isKoreaOnly=code=>code==='USDKRW'||code.startsWith('KR_')||code.startsWith('KR3')||code.startsWith('KR10')||code.startsWith('KOSPI_');
  function normalize(rows){
    const out=(rows||[]).map(row=>({time:String(row.observation_date||'').slice(0,10),value:Number(row.value)}));
    if(out.some((row,index)=>!/^\d{4}-\d{2}-\d{2}$/.test(row.time)||!Number.isFinite(row.value)||(index&&out[index-1].time>=row.time)))throw new Error('경제지표 원천 데이터의 날짜 또는 값을 확인해 주세요.');
    return Object.freeze(out.map(Object.freeze));
  }
  function createRepository(client){
    const seriesCache=new Map();let coverageCache=null;
    const catalog=indexCode=>Object.freeze((window.MacroWatchEconomicSeriesRegistry?.allSeries||[]).filter(item=>!INDEX_CODES.has(item.code)&&(indexCode==='KOSPI'||!isKoreaOnly(item.code))));
    async function loadCoverage(){
      if(coverageCache)return coverageCache;
      const {data,error}=await client.from('economic_chart_series_coverage').select('series_code,first_date,last_date,point_count');if(error)throw error;
      coverageCache=new Map((data||[]).map(row=>[row.series_code,Object.freeze({firstDate:String(row.first_date).slice(0,10),lastDate:String(row.last_date).slice(0,10),pointCount:Number(row.point_count)})]));return coverageCache;
    }
    async function load(code,from=null,to=null){
      const key=`${code}:${from||''}:${to||''}`;if(seriesCache.has(key))return seriesCache.get(key);
      const configure=query=>{if(from)query=query.gte('observation_date',from);if(to)query=query.lte('observation_date',to);return query;};
      const promise=window.MacroWatchFrontend.queryAll(client,'economic_chart_series_points','observation_date,value','observation_date',[['series_code',code]],configure).then(normalize);
      seriesCache.set(key,promise);promise.catch(()=>seriesCache.delete(key));return promise;
    }
    return Object.freeze({catalog,loadCoverage,load,clearAnalysisData(){coverageCache=null;seriesCache.clear();}});
  }
  window.MacroWatchHistoricalIndicators=Object.freeze({createRepository,normalize,isKoreaOnly});
})();
