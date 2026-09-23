(() => {
  'use strict';
  const INDEX_CODES=new Set(['SP500','NASDAQ_COMPOSITE','KOSPI']);
  const SCORE_VERSION='historical-pivot-4-3-3-v4';
  const INDEX_MARKET_SCOPES=Object.freeze({KOSPI:new Set(['KR','US','GLOBAL']),SP500:new Set(['US','GLOBAL']),NASDAQ_COMPOSITE:new Set(['US','GLOBAL'])});
  function normalize(rows){
    const out=(rows||[]).map(row=>({time:String(row.observation_date||'').slice(0,10),value:Number(row.value)}));
    if(out.some((row,index)=>!/^\d{4}-\d{2}-\d{2}$/.test(row.time)||!Number.isFinite(row.value)||(index&&out[index-1].time>=row.time)))throw new Error('경제지표 원천 데이터의 날짜 또는 값을 확인해 주세요.');
    return Object.freeze(out.map(Object.freeze));
  }
  function createRepository(client){
    const seriesCache=new Map(),pivotCache=new Map(),manualPivotCache=new Map(),scoreCache=new Map();let coverageCache=null,visibilityCache=null;
    const catalog=indexCode=>{const allowed=INDEX_MARKET_SCOPES[indexCode]||INDEX_MARKET_SCOPES.SP500;return Object.freeze((window.MacroWatchEconomicSeriesRegistry?.allSeries||[]).filter(item=>!INDEX_CODES.has(item.code)&&allowed.has(item.marketScope)));};
    async function loadVisibility(){
      if(visibilityCache)return visibilityCache;
      const {data,error}=await client.from('economic_chart_catalog_settings')
        .select('historical_hidden_series_codes').eq('id',true).single();
      if(error)throw error;
      visibilityCache=Object.freeze([...(data?.historical_hidden_series_codes||[])].map(String));
      return visibilityCache;
    }
    async function setHidden(code,hidden,userId){
      const current=new Set(await loadVisibility());
      if(hidden)current.add(String(code));else current.delete(String(code));
      const values=[...current].sort();
      const {data,error}=await client.from('economic_chart_catalog_settings').update({
        historical_hidden_series_codes:values,
        updated_at:new Date().toISOString(),
        updated_by:userId
      }).eq('id',true).select('historical_hidden_series_codes').single();
      if(error)throw error;
      visibilityCache=Object.freeze([...(data?.historical_hidden_series_codes||[])].map(String));
      return visibilityCache;
    }
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
    async function loadStoredPivots(caseCode,indexCode,seriesCode){
      const key=`${caseCode}:${indexCode}:${seriesCode}`;if(pivotCache.has(key))return pivotCache.get(key);
      const promise=window.MacroWatchFrontend.queryAll(client,'historical_indicator_pivots',
        'pivot_order,pivot_date,pivot_value,pivot_type,next_pivot_order,segment_to_next,selection_reason_codes,selection_reason,selection_meta,frequency,buffer_start,buffer_end,source_point_count,input_sha256,algorithm_version',
        'pivot_order',[['case_code',caseCode],['index_code',indexCode],['series_code',seriesCode]])
        .then(rows=>Object.freeze((rows||[]).map(row=>Object.freeze({
          pivotOrder:Number(row.pivot_order),pivotDate:String(row.pivot_date).slice(0,10),pivotValue:Number(row.pivot_value),pivotType:String(row.pivot_type||''),
          nextPivotOrder:row.next_pivot_order==null?null:Number(row.next_pivot_order),segmentToNext:row.segment_to_next||null,
          selectionReasonCodes:Object.freeze([...(row.selection_reason_codes||[])]),selectionReason:String(row.selection_reason||''),selectionMeta:Object.freeze(row.selection_meta||{}),
          frequency:String(row.frequency||''),bufferStart:String(row.buffer_start||'').slice(0,10),bufferEnd:String(row.buffer_end||'').slice(0,10),
          sourcePointCount:Number(row.source_point_count||0),inputSha256:String(row.input_sha256||''),algorithmVersion:String(row.algorithm_version||'')
        })).filter(row=>{
          if(!row.bufferStart||!row.bufferEnd||!row.pivotDate)return true;
          const visibleStart=shiftMonths(row.bufferStart,1);
          const visibleEnd=shiftMonths(row.bufferEnd,-1);
          return row.pivotDate>visibleStart&&row.pivotDate<visibleEnd;
        })));
      pivotCache.set(key,promise);promise.catch(()=>pivotCache.delete(key));return promise;
    }
    async function loadManualPivots(caseCode,indexCode,seriesCode){
      const key=`${caseCode}:${indexCode}:${seriesCode}`;
      if(manualPivotCache.has(key))return manualPivotCache.get(key);
      const promise=window.MacroWatchFrontend.queryAll(client,'historical_indicator_manual_pivots',
        'source_date,pivot_date,pivot_value,relationship,reason,comment,key_references,is_deleted',
        'source_date',[['case_code',caseCode],['series_code',seriesCode]])
        .then(rows=>Object.freeze((rows||[]).map(row=>Object.freeze({
          sourceDate:String(row.source_date).slice(0,10),pivotDate:row.pivot_date?String(row.pivot_date).slice(0,10):null,
          pivotValue:row.pivot_value==null?null:Number(row.pivot_value),relationship:row.relationship||null,
          reason:row.reason||'',comment:row.comment||'',keyReference:row.key_references?.[indexCode]||null,isDeleted:row.is_deleted===true
        }))));
      manualPivotCache.set(key,promise);promise.catch(()=>manualPivotCache.delete(key));return promise;
    }
    function clearManualPivots(caseCode,indexCode,seriesCode){for(const code of INDEX_CODES)manualPivotCache.delete(`${caseCode}:${code}:${seriesCode}`);}
    async function loadScoreRows(caseCode,indexCode){
      const key=`${caseCode}:${indexCode}`;
      if(scoreCache.has(key))return scoreCache.get(key);
      const promise=window.MacroWatchFrontend.queryAll(client,'historical_indicator_ai_scores',
        'series_code,by_reference,ai_pivots,ai_regimes,ai_anomalies,scoring_version','series_code',
        [['case_code',caseCode],['index_code',indexCode]])
        .then(rows=>new Map(rows.map(row=>[row.series_code,row])));
      scoreCache.set(key,promise);promise.catch(()=>scoreCache.delete(key));return promise;
    }
    function clearScoreRows(caseCode,indexCode){scoreCache.delete(`${caseCode}:${indexCode}`);}
    return Object.freeze({catalog,loadVisibility,setHidden,loadCoverage,load,loadStoredPivots,loadManualPivots,
      loadScoreRows,clearScoreRows,clearManualPivots,clearAnalysisData(){coverageCache=null;seriesCache.clear();pivotCache.clear();manualPivotCache.clear();scoreCache.clear();}});
  }
  window.MacroWatchHistoricalIndicators=Object.freeze({INDEX_MARKET_SCOPES,createRepository,normalize,aiAnalysis,SCORE_VERSION});

  const regimeType=value=>({uptrend:'rising',downtrend:'falling',sideways:'sideways'}[value]||value);
  const shiftMonths=(value,amount)=>{const [year,month,day]=String(value).slice(0,10).split('-').map(Number),target=new Date(Date.UTC(year,month-1+amount,1)),last=new Date(Date.UTC(target.getUTCFullYear(),target.getUTCMonth()+1,0)).getUTCDate();target.setUTCDate(Math.min(day,last));return target.toISOString().slice(0,10);};
  function aiAnalysis(row,meta,rows){
    const aiPivots=Object.freeze([...(row?.ai_pivots||[])]);
    const reviewPivots=Object.freeze(aiPivots.filter(item=>String(item?.grade||'').toUpperCase()==='D').map(item=>Object.freeze({...item,reason:String(item?.reason||'')})));
    const regimes=Object.freeze((row?.ai_regimes||[]).map(item=>Object.freeze({type:regimeType(item.type),startDate:item.start_date,endDate:item.end_date,confidence:item.confidence})));
    return Object.freeze({meta,rows,regimes,pivots:aiPivots,technicalPivots:aiPivots,marketRelevantPivots:[],nearMissPivots:[],
      byReference:Object.freeze(row?.scoring_version===SCORE_VERSION?row.by_reference||{}:{}),results:[],diagnostics:Object.freeze([]),
      visible:aiPivots.length>0,aiSourced:true,scoringVersion:row?.scoring_version||null,
      reviewPivots,manualDisplayPivots:Object.freeze([]),anomalies:Object.freeze([...(row?.ai_anomalies||[])])});
  }
})();

