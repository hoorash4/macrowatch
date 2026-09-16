(() => {
  'use strict';

  function buildMonthlyExtremes(rows){
    const months=[];let bucket=null;
    for(let index=0;index<rows.length;index++){
      const row=rows[index],month=String(row.time).slice(0,7);
      if(!bucket||bucket.month!==month){if(bucket)months.push(bucket);bucket={month,high:{...row,index},low:{...row,index}};continue;}
      if(row.value>bucket.high.value)bucket.high={...row,index};
      if(row.value<bucket.low.value)bucket.low={...row,index};
    }
    if(bucket)months.push(bucket);return months;
  }

  function localHighs(months){const out=[];for(let i=1;i<months.length-1;i++){const a=months[i-1].high.value,b=months[i].high.value,c=months[i+1].high.value;if((b>=a&&b>c)||(b>a&&b>=c))out.push({type:'high',monthIndex:i,point:months[i].high,value:b});}return out;}
  function localLows(months){const out=[];for(let i=1;i<months.length-1;i++){const a=months[i-1].low.value,b=months[i].low.value,c=months[i+1].low.value;if((b<=a&&b<c)||(b<a&&b<=c))out.push({type:'low',monthIndex:i,point:months[i].low,value:b});}return out;}
  function alternatingSwings(months){
    const raw=[...localHighs(months),...localLows(months)].sort((a,b)=>a.point.index-b.point.index),out=[];
    for(const e of raw){const last=out.at(-1);if(!last||last.type!==e.type){out.push(e);continue;}const better=e.type==='high'?e.value>=last.value:e.value<=last.value;if(better)out[out.length-1]=e;}
    return out;
  }
  const direction=(a,b)=>b>a?1:b<a?-1:0;

  function structuralObservations(swings){
    const highs=[],lows=[],out=[];
    for(const event of swings){
      if(event.type==='high'){highs.push(event);if(highs.length>2)highs.shift();}else{lows.push(event);if(lows.length>2)lows.shift();}
      if(highs.length<2||lows.length<2)continue;
      const [h1,h2]=highs,[l1,l2]=lows,hd=direction(h1.value,h2.value),ld=direction(l1.value,l2.value);
      let state=null;if(hd===1&&ld===1)state='up';else if(hd===-1&&ld===-1)state='down';
      out.push({state,at:event.point.index,start:state==='up'?l1:state==='down'?h1:null,terminal:state==='up'?h2:state==='down'?l2:null});
    }
    return out;
  }

  function extendTerminal(trend,o){
    if(!o||o.state!==trend.state)return;
    if(trend.state==='up'){if(o.terminal.value>=trend.terminal.value)trend.terminal=o.terminal;}else if(o.terminal.value<=trend.terminal.value)trend.terminal=o.terminal;
    trend.confirmations++;trend.confirmedAt=o.at;
  }

  function confirmTrends(observations){
    const trends=[];let active=null,candidate=null;
    for(const o of observations){
      if(!o.state)continue;
      if(!active){
        if(!candidate||candidate.state!==o.state){candidate={state:o.state,count:1,start:o.start,terminal:o.terminal,lastAt:o.at};continue;}
        candidate.count++;candidate.lastAt=o.at;
        if(candidate.state==='up'){if(o.terminal.value>=candidate.terminal.value)candidate.terminal=o.terminal;}else if(o.terminal.value<=candidate.terminal.value)candidate.terminal=o.terminal;
        if(candidate.count>=2){active={state:candidate.state,start:candidate.start,terminal:candidate.terminal,confirmations:candidate.count,confirmedAt:candidate.lastAt};candidate=null;}
        continue;
      }
      if(o.state===active.state){candidate=null;extendTerminal(active,o);continue;}
      if(!candidate||candidate.state!==o.state){candidate={state:o.state,count:1,start:o.start,terminal:o.terminal,lastAt:o.at};continue;}
      candidate.count++;candidate.lastAt=o.at;
      if(candidate.state==='up'){if(o.terminal.value>=candidate.terminal.value)candidate.terminal=o.terminal;}else if(o.terminal.value<=candidate.terminal.value)candidate.terminal=o.terminal;
      if(candidate.count>=2){trends.push(active);active={state:candidate.state,start:candidate.start,terminal:candidate.terminal,confirmations:candidate.count,confirmedAt:candidate.lastAt};candidate=null;}
    }
    if(active)trends.push(active);return trends;
  }

  function resumedBeyond(a,c){return a.state===c.state&&(a.state==='down'?c.terminal.value<a.terminal.value:c.terminal.value>a.terminal.value);}
  function absorbWeakCountertrends(trends){
    let out=trends.map(t=>({...t})),changed=true,guard=0;
    while(changed&&guard++<100){changed=false;for(let i=0;i<out.length-2;i++){const a=out[i],b=out[i+1],c=out[i+2];if(a.state!==c.state||b.state===a.state||b.confirmations!==2||!resumedBeyond(a,c))continue;out.splice(i,3,{state:a.state,start:a.start,terminal:a.state==='down'?(c.terminal.value<a.terminal.value?c.terminal:a.terminal):(c.terminal.value>a.terminal.value?c.terminal:a.terminal),confirmations:a.confirmations+c.confirmations,confirmedAt:Math.max(a.confirmedAt,c.confirmedAt)});changed=true;break;}}
    return out;
  }

  function pivotFrom(event,type,reason,source='monthly-balanced-trend'){
    const p=event.point||event;return {type,index:p.index,date:p.time,value:p.value,source,reason};
  }
  function buildMajorPivots(trends){
    const candidates=[];
    for(const t of trends){if(t.state==='down'){candidates.push(pivotFrom(t.start,'high','downtrend-start'));candidates.push(pivotFrom(t.terminal,'low','downtrend-end'));}else{candidates.push(pivotFrom(t.start,'low','uptrend-start'));candidates.push(pivotFrom(t.terminal,'high','uptrend-end'));}}
    candidates.sort((a,b)=>a.index-b.index);const out=[];
    for(const p of candidates){const last=out.at(-1);if(last&&last.index===p.index)continue;if(last&&last.type===p.type){const better=p.type==='high'?p.value>=last.value:p.value<=last.value;if(better)out[out.length-1]=p;continue;}out.push(p);}return out;
  }

  function monthIndexForRaw(months,rawIndex){
    let best=0,bestGap=Infinity;
    for(let i=0;i<months.length;i++){const gap=Math.min(Math.abs(months[i].high.index-rawIndex),Math.abs(months[i].low.index-rawIndex));if(gap<bestGap){bestGap=gap;best=i;}}
    return best;
  }

  function lineValue(a,b,t){if(b.monthIndex===a.monthIndex)return a.value;return a.value+(b.value-a.value)*((t-a.monthIndex)/(b.monthIndex-a.monthIndex));}

  function segmentError(months,a,b,split=null){
    const calc=(left,right)=>{
      if(right.monthIndex-left.monthIndex<1)return 0;
      let total=0;
      for(let i=left.monthIndex;i<=right.monthIndex;i++){
        const y=lineValue(left,right,i),m=months[i];
        total+=Math.abs(m.high.value-y)+Math.abs(m.low.value-y);
      }
      return total;
    };
    return split?calc(a,split)+calc(split,b):calc(a,b);
  }

  function strongestDeviation(months,a,b,fullRange){
    if(b.monthIndex-a.monthIndex<2)return null;
    let best=null;
    for(let i=a.monthIndex+1;i<b.monthIndex;i++){
      const y=lineValue(a,b,i),m=months[i];
      const highResidual=m.high.value-y,lowResidual=m.low.value-y;
      const choice=Math.abs(highResidual)>=Math.abs(lowResidual)?{type:'high',point:m.high,residual:highResidual}:{type:'low',point:m.low,residual:lowResidual};
      const candidate={monthIndex:i,value:choice.point.value,type:choice.type,point:choice.point,residual:choice.residual};
      if(!best||Math.abs(candidate.residual)>Math.abs(best.residual))best=candidate;
    }
    if(!best)return null;

    const before=segmentError(months,a,b),after=segmentError(months,a,b,best);
    if(before<=1e-12||after>=before)return null;
    const improvement=(before-after)/before;
    const distance=Math.abs(best.residual)/Math.max(fullRange,1e-12);
    const span=b.monthIndex-a.monthIndex;
    const complexityPenalty=(2*Math.log(Math.max(3,span+1)))/Math.max(3,span+1);
    const score=improvement-complexityPenalty;
    return {...best,before,after,improvement,distance,complexityPenalty,score};
  }

  function recursiveDeviation(months,a,b,fullRange,out,depth=0){
    if(depth>12||b.monthIndex-a.monthIndex<2)return;
    const c=strongestDeviation(months,a,b,fullRange);if(!c)return;
    // The split must materially improve the segment and also be visibly non-trivial on the whole chart.
    // Both tests are scale-aware; there is no fixed month-count stopping rule.
    if(c.score<=0||c.distance<=0.015)return;
    const pivot=pivotFrom(c.point,c.type,'structural-deviation','monthly-deviation');
    pivot.monthIndex=c.monthIndex;pivot.improvement=c.improvement;pivot.distance=c.distance;
    out.push(pivot);
    recursiveDeviation(months,a,c,fullRange,out,depth+1);
    recursiveDeviation(months,c,b,fullRange,out,depth+1);
  }

  function deviationCorrections(months,majors){
    if(majors.length<2)return [];
    let hi=-Infinity,lo=Infinity;for(const m of months){hi=Math.max(hi,m.high.value);lo=Math.min(lo,m.low.value);}const fullRange=Math.max(hi-lo,1e-12),out=[];
    for(let i=0;i<majors.length-1;i++){
      const p1=majors[i],p2=majors[i+1];
      const a={...p1,monthIndex:monthIndexForRaw(months,p1.index)},b={...p2,monthIndex:monthIndexForRaw(months,p2.index)};
      if(a.monthIndex>=b.monthIndex)continue;
      recursiveDeviation(months,a,b,fullRange,out,0);
    }
    return out.sort((x,y)=>x.index-y.index);
  }

  function mergePivots(majors,secondary){
    const all=[...majors,...secondary].sort((a,b)=>a.index-b.index),out=[];
    for(const p of all){const last=out.at(-1);if(last&&last.index===p.index){if(last.source==='monthly-deviation'&&p.source!=='monthly-deviation')out[out.length-1]=p;continue;}out.push(p);}return out;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<8)return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-balanced-v6-deviation',major:0,deviation:0,sidewaysZones:0}};
    const months=buildMonthlyExtremes(rows);if(months.length<4)return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-balanced-v6-deviation',major:0,deviation:0,monthly:months.length,raw:rows.length}};
    const swings=alternatingSwings(months),observations=structuralObservations(swings);let trends=confirmTrends(observations);trends=absorbWeakCountertrends(trends);
    const majors=buildMajorPivots(trends),secondary=deviationCorrections(months,majors),pivots=mergePivots(majors,secondary);
    let sidewaysZones=0;for(let i=0;i<trends.length-1;i++)if(trends[i].terminal.point.index<trends[i+1].start.point.index)sidewaysZones++;
    return {pivots,path:pivots.map(p=>({date:p.date,value:p.value,virtual:false})),diagnostics:{engineVersion:'monthly-balanced-v6-deviation',major:majors.length,deviation:secondary.length,sidewaysZones,monthly:months.length,raw:rows.length,swings:swings.length,observations:observations.length,trends:trends.length}};
  }

  window.PivotLabEngine=Object.freeze({detect});
})();