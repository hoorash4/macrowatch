(() => {
  'use strict';
  const FILTER_THRESHOLDS=Object.freeze({strong:80,standard:65,weak:40});
  const CURRENT_SIGNAL_POLICY=Object.freeze({historicalThreshold:FILTER_THRESHOLDS.weak,activeThreshold:FILTER_THRESHOLDS.standard,invalidThreshold:FILTER_THRESHOLDS.weak,priorWeight:4,
    horizonDays:Object.freeze({D:120,W:210,T:210,M:450,Q:730,E:450})});
  const FREQUENCY_RULES=Object.freeze({
    D:{window:10,lookbackDays:730,minPoints:12},W:{window:4,lookbackDays:1095,minPoints:8},T:{window:3,lookbackDays:1095,minPoints:6},
    M:{window:2,lookbackDays:1460,minPoints:5},Q:{window:1,lookbackDays:2190,minPoints:4},E:{window:2,lookbackDays:1460,minPoints:4}
  });
  const dayNumber=value=>Math.floor(Date.parse(`${value}T00:00:00Z`)/86400000);
  const daysBetween=(a,b)=>dayNumber(b)-dayNumber(a);
  const validRows=rows=>rows.filter(row=>/^\d{4}-\d{2}-\d{2}$/.test(row.time)&&Number.isFinite(row.value)).sort((a,b)=>a.time.localeCompare(b.time));
  function detectPivots(rows,frequency){
    const data=validRows(rows),rule=FREQUENCY_RULES[frequency]||FREQUENCY_RULES.M,out=[];
    for(let i=rule.window;i<data.length-rule.window;i++){
      const value=data[i].value,left=data.slice(i-rule.window,i).map(x=>x.value),right=data.slice(i+1,i+rule.window+1).map(x=>x.value);
      const localMin=value<Math.min(...left)&&value<=Math.min(...right),localMax=value>Math.max(...left)&&value>=Math.max(...right);
      if(localMin||localMax)out.push(Object.freeze({index:i,date:data[i].time,value,pivotType:localMin?'local_low':'local_high'}));
    }
    return Object.freeze(out);
  }
  function sameObservation(a,b,frequency){
    if(a===b)return true;
    if(frequency==='M')return a.slice(0,7)===b.slice(0,7);
    if(frequency==='Q'){const da=new Date(`${a}T00:00:00Z`),db=new Date(`${b}T00:00:00Z`);return da.getUTCFullYear()===db.getUTCFullYear()&&Math.floor(da.getUTCMonth()/3)===Math.floor(db.getUTCMonth()/3);}
    if(frequency==='W'||frequency==='T'){return Math.abs(daysBetween(a,b))<=(frequency==='W'?6:9);}
    return false;
  }
  function regression(rows){
    if(rows.length<2)return {slope:0,consistency:0};
    const first=dayNumber(rows[0].time),xs=rows.map(row=>dayNumber(row.time)-first),ys=rows.map(row=>row.value),xm=xs.reduce((a,b)=>a+b,0)/xs.length,ym=ys.reduce((a,b)=>a+b,0)/ys.length;
    let covariance=0,varianceX=0,varianceY=0;for(let i=0;i<xs.length;i++){const dx=xs[i]-xm,dy=ys[i]-ym;covariance+=dx*dy;varianceX+=dx*dx;varianceY+=dy*dy;}
    const slope=varianceX?covariance/varianceX:0,r2=varianceX&&varianceY?(covariance*covariance)/(varianceX*varianceY):0;
    return {slope,consistency:Math.max(0,Math.min(100,r2*100))};
  }
  function resultForReference(data,pivots,frequency,referenceType,referenceDate,endDate){
    if(!referenceDate||!endDate)return null;
    const rule=FREQUENCY_RULES[frequency]||FREQUENCY_RULES.M;
    const eligible=pivots.filter(pivot=>daysBetween(pivot.date,referenceDate)>=0&&daysBetween(pivot.date,referenceDate)<=rule.lookbackDays);
    const pivot=eligible.sort((a,b)=>b.date.localeCompare(a.date))[0];
    if(!pivot)return null;
    const trendRows=data.filter(row=>row.time>=pivot.date&&row.time<=endDate),trend=regression(trendRows),direction=pivot.pivotType==='local_low'?'rising':'falling';
    if(trendRows.length<rule.minPoints||(direction==='rising'?trend.slope<=0:trend.slope>=0))return null;
    const coincident=sameObservation(pivot.date,referenceDate,frequency);
    return Object.freeze({referenceType,referenceDate,pivotDate:pivot.date,pivotValue:pivot.value,pivotType:pivot.pivotType,timingType:coincident?'coincident':'leading',leadDays:coincident?0:daysBetween(pivot.date,referenceDate),trendDirection:direction,trendConsistency:trend.consistency});
  }
  function analysisEnd(item,cycle){return item.searchEnd||cycle.troughDate||cycle.peakDate||cycle.startDate;}
  function analyzeHistorical(meta,rows,item,cycle){
    const end=analysisEnd(item,cycle),data=validRows(rows).filter(row=>row.time>=item.searchStart&&row.time<=end),pivots=detectPivots(data,meta.frequency);
    const references=[['START',cycle.startDate,cycle.peakDate||end],['PEAK',cycle.peakDate,cycle.troughDate||end],['TROUGH',cycle.troughDate,end]];
    const results=references.map(args=>resultForReference(data,pivots,meta.frequency,...args)).filter(Boolean);
    return Object.freeze({meta,rows:data,results:Object.freeze(results)});
  }
  function analyzeCurrent(meta,rows,startDate,endDate){
    const data=validRows(rows).filter(row=>row.time>=startDate&&row.time<=endDate),pivots=detectPivots(data,meta.frequency),rule=FREQUENCY_RULES[meta.frequency]||FREQUENCY_RULES.M;
    const horizon=CURRENT_SIGNAL_POLICY.horizonDays[meta.frequency]||CURRENT_SIGNAL_POLICY.horizonDays.M,pivot=pivots.filter(item=>daysBetween(item.date,endDate)>=0&&daysBetween(item.date,endDate)<=horizon).sort((a,b)=>b.date.localeCompare(a.date))[0];
    if(!pivot)return Object.freeze({meta,rows:data,results:Object.freeze([]),evidence:Object.freeze({status:'watching'})});
    const trendRows=data.filter(row=>row.time>=pivot.date),trend=regression(trendRows),direction=pivot.pivotType==='local_low'?'rising':'falling',directionMatches=direction==='rising'?trend.slope>0:trend.slope<0;
    const result=Object.freeze({referenceType:'CURRENT',referenceDate:endDate,pivotDate:pivot.date,pivotValue:pivot.value,pivotType:pivot.pivotType,timingType:'leading',leadDays:daysBetween(pivot.date,endDate),trendDirection:direction,trendConsistency:trend.consistency});
    const status=trendRows.length<rule.minPoints?'forming':directionMatches&&trend.consistency>=CURRENT_SIGNAL_POLICY.activeThreshold?'active':!directionMatches||trend.consistency<CURRENT_SIGNAL_POLICY.invalidThreshold?'invalidated':'watching';
    const evidence=Object.freeze({status,result});
    return Object.freeze({meta,rows:data,results:Object.freeze(status==='active'?[result]:[]),evidence});
  }
  function currentPivotProbability(analyses){
    let positive=0,negative=0,activeCount=0,invalidatedCount=0;
    for(const item of analyses){const evidence=item.evidence;if(!evidence?.result)continue;const historical=Math.max(0,Math.min(1,(item.pastConsistency||0)/100)),age=evidence.result.leadDays||0,horizon=CURRENT_SIGNAL_POLICY.horizonDays[item.meta.frequency]||CURRENT_SIGNAL_POLICY.horizonDays.M,recency=Math.max(.25,1-age/horizon);
      if(evidence.status==='active'){positive+=historical*(evidence.result.trendConsistency/100)*recency;activeCount++;}
      if(evidence.status==='invalidated'){negative+=historical*Math.max(.4,evidence.result.trendConsistency/100)*recency;invalidatedCount++;}
    }
    const probability=positive?Math.round(100*positive/(positive+negative+CURRENT_SIGNAL_POLICY.priorWeight)):0;
    return Object.freeze({probability,activeCount,invalidatedCount,positiveWeight:positive,negativeWeight:negative});
  }
  function normalizeForDisplay(rows,from,to){
    const data=validRows(rows).filter(row=>row.time>=from&&row.time<=to);if(!data.length)return [];
    const values=data.map(row=>row.value),min=Math.min(...values),max=Math.max(...values),span=max-min;
    return data.map(row=>Object.freeze({time:row.time,value:span?(row.value-min)/span*100:50,rawValue:row.value}));
  }
  const qualifies=(analysis,strength)=>analysis.results.some(result=>result.timingType!=='lagging'&&result.trendConsistency>=FILTER_THRESHOLDS[strength]);
  window.MacroWatchHistoricalIndicatorAnalysis=Object.freeze({FILTER_THRESHOLDS,CURRENT_SIGNAL_POLICY,FREQUENCY_RULES,detectPivots,regression,analyzeHistorical,analyzeCurrent,currentPivotProbability,normalizeForDisplay,qualifies,analysisEnd});
})();
