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

  function localHighs(months){
    const out=[];
    for(let i=1;i<months.length-1;i++){
      const prev=months[i-1].high.value;
      const value=months[i].high.value;
      const next=months[i+1].high.value;
      if((value>=prev&&value>next)||(value>prev&&value>=next)){
        out.push({type:'high',monthIndex:i,point:months[i].high,value});
      }
    }
    return out;
  }

  function localLows(months){
    const out=[];
    for(let i=1;i<months.length-1;i++){
      const prev=months[i-1].low.value;
      const value=months[i].low.value;
      const next=months[i+1].low.value;
      if((value<=prev&&value<next)||(value<prev&&value<=next)){
        out.push({type:'low',monthIndex:i,point:months[i].low,value});
      }
    }
    return out;
  }

  function buildSwingEvents(months){
    const raw=[...localHighs(months),...localLows(months)].sort((a,b)=>a.point.index-b.point.index);
    const events=[];

    for(const event of raw){
      const last=events.at(-1);
      if(!last||last.type!==event.type){
        events.push(event);
        continue;
      }

      const moreExtreme=event.type==='high'
        ? event.value>=last.value
        : event.value<=last.value;
      if(moreExtreme)events[events.length-1]=event;
    }
    return events;
  }

  function compare(a,b){
    if(b>a)return 1;
    if(b<a)return -1;
    return 0;
  }

  function buildTrendEvidence(events){
    const highs=[];
    const lows=[];
    const evidence=[];

    for(const event of events){
      if(event.type==='high'){
        highs.push(event);
        if(highs.length>2)highs.shift();
      }else{
        lows.push(event);
        if(lows.length>2)lows.shift();
      }

      if(highs.length<2||lows.length<2)continue;

      const [h1,h2]=highs;
      const [l1,l2]=lows;
      const highDirection=compare(h1.value,h2.value);
      const lowDirection=compare(l1.value,l2.value);

      let state=null;
      if(highDirection===1&&lowDirection===1)state='up';
      else if(highDirection===-1&&lowDirection===-1)state='down';
      if(!state)continue;

      const start=state==='up'?l1:h1;
      const terminal=state==='up'?h2:l2;
      if(start.point.index>=terminal.point.index)continue;

      const last=evidence.at(-1);
      if(last&&last.state===state&&last.start.point.index===start.point.index&&last.terminal.point.index===terminal.point.index)continue;

      evidence.push({
        state,
        start,
        terminal,
        confirmedAt:event.point.index,
        highPair:[h1,h2],
        lowPair:[l1,l2],
      });
    }
    return evidence;
  }

  function buildConfirmedTrends(evidence){
    const trends=[];
    let active=null;

    for(const item of evidence){
      if(!active){
        active={state:item.state,start:item.start,terminal:item.terminal,confirmedAt:item.confirmedAt};
        continue;
      }

      if(item.state===active.state){
        // Ambiguous swings between two confirmations do not end a trend.
        // If the same structural direction is confirmed again, the later, more meaningful
        // terminal replaces the earlier provisional endpoint.
        if(active.state==='up'){
          if(item.terminal.value>=active.terminal.value)active.terminal=item.terminal;
        }else if(item.terminal.value<=active.terminal.value){
          active.terminal=item.terminal;
        }
        active.confirmedAt=Math.max(active.confirmedAt,item.confirmedAt);
        continue;
      }

      trends.push(active);
      active={state:item.state,start:item.start,terminal:item.terminal,confirmedAt:item.confirmedAt};
    }

    if(active)trends.push(active);
    return trends;
  }

  function pivotFrom(event,type,reason){
    const point=event.point;
    return {
      type,
      index:point.index,
      date:point.time,
      value:point.value,
      source:'monthly-confirmed-trend',
      reason,
    };
  }

  function buildPivots(trends){
    const candidates=[];
    for(const trend of trends){
      if(trend.state==='down'){
        // Downtrend: confirmed only after highs AND lows are both lower.
        // Start is on the high-line, end is the last low that the confirmed decline extends to.
        candidates.push(pivotFrom(trend.start,'high','downtrend-start'));
        candidates.push(pivotFrom(trend.terminal,'low','downtrend-end'));
      }else{
        // Uptrend: confirmed only after highs AND lows are both higher.
        // Start is on the low-line, end is the last high that the confirmed rise extends to.
        candidates.push(pivotFrom(trend.start,'low','uptrend-start'));
        candidates.push(pivotFrom(trend.terminal,'high','uptrend-end'));
      }
    }

    candidates.sort((a,b)=>a.index-b.index);
    const pivots=[];
    for(const p of candidates){
      const last=pivots.at(-1);
      if(last&&last.index===p.index){
        // A reversal can legitimately share one structural point.
        // Keep one copy; the date/value is identical.
        continue;
      }
      if(last&&last.type===p.type){
        // Two same-type anchors without a confirmed opposite trend are one unresolved structure.
        // Retrospectively keep only the later/more extreme structural endpoint.
        const better=p.type==='high'?p.value>=last.value:p.value<=last.value;
        if(better)pivots[pivots.length-1]=p;
        continue;
      }
      pivots.push(p);
    }
    return pivots;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<8){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-structure-v3',major:0,sidewaysZones:0,deviation:0}};
    }

    const months=buildMonthlyExtremes(rows);
    if(months.length<4){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-structure-v3',major:0,sidewaysZones:0,deviation:0,monthly:months.length,raw:rows.length}};
    }

    const events=buildSwingEvents(months);
    const evidence=buildTrendEvidence(events);
    const trends=buildConfirmedTrends(evidence);
    const pivots=buildPivots(trends);

    let sidewaysZones=0;
    for(let i=0;i<trends.length-1;i++){
      if(trends[i].terminal.point.index<trends[i+1].start.point.index)sidewaysZones++;
    }

    return {
      pivots,
      path:pivots.map(p=>({date:p.date,value:p.value,virtual:false})),
      diagnostics:{
        engineVersion:'monthly-structure-v3',
        major:pivots.length,
        sidewaysZones,
        deviation:0,
        monthly:months.length,
        raw:rows.length,
        swings:events.length,
        confirmations:evidence.length,
        trends:trends.length,
      }
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();