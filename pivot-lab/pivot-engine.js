(() => {
  'use strict';

  function buildMonthlyExtremes(rows){
    const months=[];
    let bucket=null;
    for(let index=0;index<rows.length;index++){
      const row=rows[index];
      const month=String(row.time).slice(0,7);
      if(!bucket||bucket.month!==month){
        if(bucket)months.push(bucket);
        bucket={month,high:{...row,index},low:{...row,index}};
        continue;
      }
      if(row.value>bucket.high.value)bucket.high={...row,index};
      if(row.value<bucket.low.value)bucket.low={...row,index};
    }
    if(bucket)months.push(bucket);
    return months;
  }

  function chartRange(months){
    let hi=-Infinity,lo=Infinity;
    for(const m of months){hi=Math.max(hi,m.high.value);lo=Math.min(lo,m.low.value);}
    return Math.max(hi-lo,1e-12);
  }

  function regressionSlope(values){
    const n=values.length;
    if(n<2)return 0;
    const mx=(n-1)/2;
    const my=values.reduce((s,v)=>s+v,0)/n;
    let cov=0,varx=0;
    for(let i=0;i<n;i++){
      const dx=i-mx;
      cov+=dx*(values[i]-my);
      varx+=dx*dx;
    }
    return varx?cov/varx:0;
  }

  function trendEvidence(months,from,to,range){
    if(to-from<2)return null;
    const highs=months.slice(from,to+1).map(m=>m.high.value);
    const lows=months.slice(from,to+1).map(m=>m.low.value);
    const highNet=(highs.at(-1)-highs[0])/range;
    const lowNet=(lows.at(-1)-lows[0])/range;
    const highSlope=regressionSlope(highs)/range;
    const lowSlope=regressionSlope(lows)/range;

    const up=highNet>.025&&lowNet>.025&&highSlope>.004&&lowSlope>.004;
    const down=highNet<-.025&&lowNet<-.025&&highSlope<-.004&&lowSlope<-.004;
    if(up)return 'up';
    if(down)return 'down';
    return null;
  }

  function findTrendStart(months,from,range){
    const maxLook=Math.min(months.length-1,from+8);
    for(let end=from+2;end<=maxLook;end++){
      const state=trendEvidence(months,from,end,range);
      if(state)return {state,confirmMonth:end};
    }
    return null;
  }

  function highestHigh(months,from,to){
    let best=months[from].high;
    let monthIndex=from;
    for(let i=from+1;i<=to;i++)if(months[i].high.value>best.value){best=months[i].high;monthIndex=i;}
    return {point:best,monthIndex};
  }

  function lowestLow(months,from,to){
    let best=months[from].low;
    let monthIndex=from;
    for(let i=from+1;i<=to;i++)if(months[i].low.value<best.value){best=months[i].low;monthIndex=i;}
    return {point:best,monthIndex};
  }

  function extendTrend(months,startMonth,confirmMonth,state,range){
    const meaningful=range*.018;
    const maxPause=4;

    if(state==='down'){
      const start=highestHigh(months,startMonth,confirmMonth);
      let terminal=lowestLow(months,startMonth,confirmMonth);
      let lastBreakMonth=terminal.monthIndex;

      for(let i=confirmMonth+1;i<months.length;i++){
        if(months[i].low.value<terminal.point.value-meaningful){
          terminal={point:months[i].low,monthIndex:i};
          lastBreakMonth=i;
          continue;
        }

        if(i-lastBreakMonth<=maxPause)continue;

        const lookTo=Math.min(months.length-1,i+2);
        const opposite=trendEvidence(months,Math.max(terminal.monthIndex,i-2),lookTo,range)==='up';
        const reboundHigh=highestHigh(months,terminal.monthIndex,lookTo).point.value;
        const reboundMeaningful=reboundHigh>months[terminal.monthIndex].high.value+meaningful;
        if(opposite||reboundMeaningful)break;
      }
      return {state,start,terminal,endMonth:terminal.monthIndex};
    }

    const start=lowestLow(months,startMonth,confirmMonth);
    let terminal=highestHigh(months,startMonth,confirmMonth);
    let lastBreakMonth=terminal.monthIndex;

    for(let i=confirmMonth+1;i<months.length;i++){
      if(months[i].high.value>terminal.point.value+meaningful){
        terminal={point:months[i].high,monthIndex:i};
        lastBreakMonth=i;
        continue;
      }

      if(i-lastBreakMonth<=maxPause)continue;

      const lookTo=Math.min(months.length-1,i+2);
      const opposite=trendEvidence(months,Math.max(terminal.monthIndex,i-2),lookTo,range)==='down';
      const pullbackLow=lowestLow(months,terminal.monthIndex,lookTo).point.value;
      const pullbackMeaningful=pullbackLow<months[terminal.monthIndex].low.value-meaningful;
      if(opposite||pullbackMeaningful)break;
    }
    return {state,start,terminal,endMonth:terminal.monthIndex};
  }

  function buildStructuralTrends(months){
    const range=chartRange(months);
    const trends=[];
    let cursor=0;
    let guard=0;

    while(cursor<months.length-3&&guard++<200){
      const found=findTrendStart(months,cursor,range);
      if(!found){cursor++;continue;}

      const trend=extendTrend(months,cursor,found.confirmMonth,found.state,range);
      trends.push({...trend,confirmMonth:found.confirmMonth});

      const nextCursor=Math.max(cursor+1,trend.endMonth);
      if(nextCursor<=cursor)cursor++;
      else cursor=nextCursor;
    }

    // If two neighbouring detected trends have the same direction, the later extension wins.
    // This is the retrospective cancellation rule: an earlier provisional endpoint is removed.
    const merged=[];
    for(const t of trends){
      const last=merged.at(-1);
      if(last&&last.state===t.state&&t.start.monthIndex<=last.endMonth+4){
        if(t.state==='down'&&t.terminal.point.value<last.terminal.point.value){
          last.terminal=t.terminal;
          last.endMonth=t.endMonth;
          last.confirmMonth=Math.max(last.confirmMonth,t.confirmMonth);
        }else if(t.state==='up'&&t.terminal.point.value>last.terminal.point.value){
          last.terminal=t.terminal;
          last.endMonth=t.endMonth;
          last.confirmMonth=Math.max(last.confirmMonth,t.confirmMonth);
        }
        continue;
      }
      merged.push(t);
    }
    return {trends:merged,range};
  }

  function pivotFrom(point,type,reason){
    return {type,index:point.index,date:point.time,value:point.value,source:'monthly-structural-reference',reason};
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<8){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-reference-v2',major:0,sidewaysZones:0,deviation:0}};
    }

    const months=buildMonthlyExtremes(rows);
    if(months.length<4){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-reference-v2',major:0,sidewaysZones:0,deviation:0,monthly:months.length,raw:rows.length}};
    }

    const {trends}=buildStructuralTrends(months);
    const candidates=[];

    for(const trend of trends){
      if(trend.state==='down'){
        // Downtrend starts from the high-line and ends on the low-line.
        candidates.push(pivotFrom(trend.start.point,'high','downtrend-start'));
        candidates.push(pivotFrom(trend.terminal.point,'low','downtrend-end'));
      }else{
        // Uptrend starts from the low-line and ends on the high-line.
        candidates.push(pivotFrom(trend.start.point,'low','uptrend-start'));
        candidates.push(pivotFrom(trend.terminal.point,'high','uptrend-end'));
      }
    }

    candidates.sort((a,b)=>a.index-b.index);
    const pivots=[];
    for(const p of candidates){
      const last=pivots.at(-1);
      if(last&&last.index===p.index){
        if(last.reason.endsWith('-end')&&p.reason.endsWith('-start'))continue;
        pivots[pivots.length-1]=p;
        continue;
      }
      if(last&&last.type===p.type){
        // Same-type neighbouring anchors represent one unresolved structure.
        // Keep only the structurally more extreme point; the earlier provisional point disappears.
        const better=p.type==='high'?p.value>last.value:p.value<last.value;
        if(better)pivots[pivots.length-1]=p;
        continue;
      }
      pivots.push(p);
    }

    // Path uses only confirmed structural anchors; no synthetic endpoints and no monthly wiggles.
    return {
      pivots,
      path:pivots.map(p=>({date:p.date,value:p.value,virtual:false})),
      diagnostics:{
        engineVersion:'monthly-reference-v2',
        major:pivots.length,
        sidewaysZones:Math.max(0,trends.length-1),
        deviation:0,
        monthly:months.length,
        raw:rows.length,
        trends:trends.length,
      }
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();