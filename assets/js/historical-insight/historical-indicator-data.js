(() => {
  'use strict';
  const INDEX_CODES=new Set(['SP500','NASDAQ_COMPOSITE','KOSPI']);
  const INDEX_MARKET_SCOPES=Object.freeze({KOSPI:new Set(['KR','US','GLOBAL']),SP500:new Set(['US','GLOBAL']),NASDAQ_COMPOSITE:new Set(['US','GLOBAL'])});
  function normalize(rows){
    const out=(rows||[]).map(row=>({time:String(row.observation_date||'').slice(0,10),value:Number(row.value)}));
    if(out.some((row,index)=>!/^\d{4}-\d{2}-\d{2}$/.test(row.time)||!Number.isFinite(row.value)||(index&&out[index-1].time>=row.time)))throw new Error('경제지표 원천 데이터의 날짜 또는 값을 확인해 주세요.');
    return Object.freeze(out.map(Object.freeze));
  }
  function createRepository(client){
    const seriesCache=new Map();let coverageCache=null;
    const catalog=indexCode=>{const allowed=INDEX_MARKET_SCOPES[indexCode]||INDEX_MARKET_SCOPES.SP500;return Object.freeze((window.MacroWatchEconomicSeriesRegistry?.allSeries||[]).filter(item=>!INDEX_CODES.has(item.code)&&allowed.has(item.marketScope)));};
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
  window.MacroWatchHistoricalIndicators=Object.freeze({INDEX_MARKET_SCOPES,createRepository,normalize,clearAiScores});

  const aiScoreCache=new Map();
  function clearAiScores(){aiScoreCache.clear();}
  function loadAiScores(caseCode,indexCode){
    const key=`${caseCode}:${indexCode}`;
    if(aiScoreCache.has(key))return aiScoreCache.get(key);
    const config=window.MACROWATCH_CONFIG,map=new Map();
    try{
      if(!config?.supabaseUrl||!config?.supabasePublishableKey){aiScoreCache.set(key,map);return map;}
      const params=new URLSearchParams({
        select:'series_code,overall_score,meaningful_reference_count,max_reference_score,reference_coverage_count,coverage_bonus,cycle_relationship,by_reference,results,near_miss_pivots,ai_pivots,ai_regimes,ai_anomalies,scoring_version',
        case_code:`eq.${caseCode}`,index_code:`eq.${indexCode}`,order:'overall_score.desc'
      });
      const xhr=new XMLHttpRequest();
      xhr.open('GET',`${config.supabaseUrl}/rest/v1/historical_indicator_ai_scores?${params}`,false);
      xhr.setRequestHeader('apikey',config.supabasePublishableKey);
      xhr.setRequestHeader('Authorization',`Bearer ${config.supabasePublishableKey}`);
      xhr.setRequestHeader('Accept','application/json');xhr.send();
      if(xhr.status>=200&&xhr.status<300){for(const row of JSON.parse(xhr.responseText||'[]'))map.set(String(row.series_code),row);}
    }catch(error){console.warn('AI pivot score load failed; legacy pivot engine remains available.',error);}
    aiScoreCache.set(key,map);return map;
  }
  const regimeType=value=>({uptrend:'rising',downtrend:'falling',sideways:'sideways'}[value]||value);
  const pivotDate=value=>String(value||'').slice(0,10);
  function displayOnlyPivot(pivot){
    return Object.freeze({
      pivotDate:pivotDate(pivot.date),pivotValue:Number(pivot.value),pivotType:String(pivot.type||''),pivotGrade:'C',sourcePivotGrade:String(pivot.grade||''),
      pivotReason:String(pivot.reason||''),pivotConfidence:Number(pivot.confidence||0),regimeBoundaryDate:pivotDate(pivot.date),confirmationDate:pivotDate(pivot.date),
      referenceType:null,referenceDate:null,offsetDays:null,markerStatus:'reference_only',pivotRole:'reference-only',score:null,baseScore:null
    });
  }
  const shiftMonths=(value,amount)=>{const [year,month,day]=String(value).slice(0,10).split('-').map(Number),target=new Date(Date.UTC(year,month-1+amount,1)),last=new Date(Date.UTC(target.getUTCFullYear(),target.getUTCMonth()+1,0)).getUTCDate();target.setUTCDate(Math.min(day,last));return target.toISOString().slice(0,10);};
  function manualDisplayPivot(pivot,cycle){
    const date=pivotDate(pivot.date),grade=String(pivot.grade||'').toUpperCase(),base={pivotDate:date,pivotValue:Number(pivot.value),pivotType:String(pivot.type||''),pivotGrade:grade,sourcePivotGrade:grade,pivotReason:String(pivot.reason||''),pivotConfidence:Number(pivot.confidence||0),regimeBoundaryDate:date,confirmationDate:date,score:null,baseScore:null,manualResolution:true};
    if(grade==='C')return Object.freeze({...base,referenceType:null,referenceDate:null,offsetDays:null,markerStatus:'reference_only',pivotRole:'reference-only'});
    const refs=[['START',cycle?.startDate],['PEAK',cycle?.peakDate],['TROUGH',cycle?.troughDate]].filter(([,d])=>d);
    for(const [type,ref] of refs){if(date>=shiftMonths(ref,-3)&&date<=shiftMonths(ref,1))return Object.freeze({...base,referenceType:type,referenceDate:ref,offsetDays:Math.round((Date.parse(date)-Date.parse(ref))/86400000),markerStatus:'confirmed',pivotRole:'manual-confirmed'});}
    for(const [type,ref] of refs){if(date>=shiftMonths(ref,-6)&&date<=shiftMonths(ref,2))return Object.freeze({...base,referenceType:type,referenceDate:ref,offsetDays:Math.round((Date.parse(date)-Date.parse(ref))/86400000),markerStatus:'near_miss',pivotRole:'manual-near-miss'});}
    return Object.freeze({...base,pivotGrade:'C',referenceType:null,referenceDate:null,offsetDays:null,markerStatus:'reference_only',pivotRole:'reference-only'});
  }
  function aiAnalysis(row,meta,rows,cycle){
    const results=Object.freeze([...(row.results||[])]),scoredNearMisses=[...(row.near_miss_pivots||[])],byReference=Object.freeze(row.by_reference||{}),aiPivots=[...(row.ai_pivots||[])];
    const occupied=new Set([...results,...scoredNearMisses].map(item=>pivotDate(item.pivotDate)));
    const manualDisplayPivots=Object.freeze(aiPivots.filter(item=>item?.manual_resolution&&['A','B','C'].includes(String(item?.grade||'').toUpperCase())&&!occupied.has(pivotDate(item.date))).map(item=>manualDisplayPivot(item,cycle)));
    const manualDates=new Set(manualDisplayPivots.map(item=>pivotDate(item.pivotDate)));
    const referenceOnly=aiPivots.filter(item=>['A','B'].includes(String(item?.grade||'').toUpperCase())&&!occupied.has(pivotDate(item.date))&&!manualDates.has(pivotDate(item.date))).map(displayOnlyPivot);
    const reviewPivots=Object.freeze(aiPivots.filter(item=>String(item?.grade||'').toUpperCase()==='D').map(item=>Object.freeze({...item,reason:String(item?.reason||'')})));
    const nearMissPivots=Object.freeze([...scoredNearMisses,...referenceOnly]);
    const regimes=Object.freeze((row.ai_regimes||[]).map(item=>Object.freeze({type:regimeType(item.type),startDate:item.start_date,endDate:item.end_date,confidence:item.confidence})));
    return Object.freeze({
      meta,rows,regimes,pivots:Object.freeze(aiPivots),technicalPivots:Object.freeze(aiPivots),marketRelevantPivots:results,nearMissPivots,
      cycleRelationship:row.cycle_relationship||'unresolved',byReference,results,diagnostics:Object.freeze([]),
      overallScore:row.overall_score==null?null:Number(row.overall_score),referenceCoverageCount:Number(row.reference_coverage_count||0),coverageBonus:Number(row.coverage_bonus||0),
      meaningfulReferenceCount:Number(row.meaningful_reference_count||results.length),maxReferenceScore:Number(row.max_reference_score||0),
      visible:results.length>0||scoredNearMisses.length>0||referenceOnly.length>0||reviewPivots.length>0,aiSourced:true,scoringVersion:row.scoring_version||null,
      reviewPivots,manualDisplayPivots,anomalies:Object.freeze([...(row.ai_anomalies||[])])
    });
  }
  let analysisApi=null;
  Object.defineProperty(window,'MacroWatchHistoricalIndicatorAnalysis',{
    configurable:true,get(){return analysisApi;},
    set(value){
      const legacyAnalyze=value.analyzeHistorical;
      const wrapped=function(meta,rows,item,cycle,marketRows=[]){
        const caseCode=item?.code||cycle?.caseCode,indexCode=cycle?.indexCode;
        if(caseCode&&indexCode){const score=loadAiScores(caseCode,indexCode).get(meta.code);if(score)return aiAnalysis(score,meta,rows,cycle);}
        return legacyAnalyze(meta,rows,item,cycle,marketRows);
      };
      analysisApi=Object.freeze({...value,analyzeHistorical:wrapped});
      Object.defineProperty(window,'MacroWatchHistoricalIndicatorAnalysis',{value:analysisApi,writable:false,configurable:false,enumerable:true});
    }
  });
})();
