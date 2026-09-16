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
      const a=months[i-1].high.value,b=months[i].high.value,c=months[i+1].high.value;
      if((b>=a&&b>c)||(b>a&&b>=c))out.push({type:'high',monthIndex:i,point:months[i].high,value:b});
    }
    return out;
  }

  function localLows(months){
    const out=[];
    for(let i=1;i<months.length-1;i++){
      const a=months[i-1].low.value,b=months[i].low.value,c=months[i+1].low.value;
      if((b<=a&&b<c)||(b<a&&b<=c))out.push({type:'low',monthIndex:i,point:months[i].low,value:b});
    }
    return out;
  }

  function alternatingSwings(months){
    const raw=[...localHighs(months),...localLows(months)].sort((a,b)=>a.point.index-b.point.index);
    const out=[];
    for(const e of raw){
      const last=out.at(-1);
      if(!last||last.type!==e.type){out.push(e);continue;}
      const better=e.type==='high'?e.value>=last.value:e.value<=last.value;
      if(better)out[out.length-1]=e;
    }
    return out;
  }

  function dir(a,b){return b>a?1:b<a?-1:0;}

  // A directional trend is not created by one move. It needs two highs and two lows.
  // Higher high + higher low => up. Lower high + lower low => down.
  function buildEvidence(swings){
    const highs=[],lows=[],out=[];
    for(const e of swings){
      if(e.type==='high'){highs.push(e);if(highs.length>2)highs.shift();}
      else{lows.push(e);if(lows.length>2)lows.shift();}
      if(highs.length<2||lows.length<2)continue;

      const [h1,h2]=highs,[l1,l2]=lows;
      const hd=dir(h1.value,h2.value),ld=dir(l1.value,l2.value);
      let state=null;
      if(hd===1&&ld===1)state='up';
      else if(hd===-1&&ld===-1)state='down';
      if(!state)continue; // mixed high/low directions stay unresolved/sideways

      const start=state==='up'?l1:h1;
      const terminal=state==='up'?h2:l2;
      if(start.point.index>=terminal.point.index)continue;

      const last=out.at(-1);
      if(last&&last.state===state&&last.start.point.index===start.point.index&&last.terminal.point.index===terminal.point.index)continue;
      out.push({state,start,terminal,confirmedAt:e.point.index});
    }
    return out;
  }

  function mergeSameDirection(evidence){
    const out=[];
    for(const e of evidence){
      const last=out.at(-1);
      if(!last||last.state!==e.state){out.push({...e});continue;}

      // Same directional evidence belongs to one trend. Keep the earliest structural start
      // and extend only to a more extreme terminal.
      if(e.state==='up'){
        if(e.terminal.value>=last.terminal.value)last.terminal=e.terminal;
      }else if(e.terminal.value<=last.terminal.value){
        last.terminal=e.terminal;
      }
      last.confirmedAt=Math.max(last.confirmedAt,e.confirmedAt);
    }
    return out;
  }

  function dominatesLaterSameDirection(a,c){
    if(a.state!==c.state)return false;
    if(a.state==='down')return c.terminal.value<a.terminal.value;
    return c.terminal.value>a.terminal.value;
  }

  // Retrospective hierarchy:
  // down -> short up -> down that makes a NEW lower low is still one larger downtrend.
  // up -> short down -> up that makes a NEW higher high is still one larger uptrend.
  // The middle countertrend is cancelled, not turned into pivots.
  function absorbCountertrends(segments){
    let out=segments.map(s=>({...s}));
    let changed=true,guard=0;
    while(changed&&guard++<200){
      changed=false;
      for(let i=0;i<out.length-2;i++){
        const a=out[i],b=out[i+1],c=out[i+2];
        if(a.state!==c.state||b.state===a.state)continue;
        if(!dominatesLaterSameDirection(a,c))continue;

        const merged={
          state:a.state,
          start:a.start,
          terminal:a.state==='down'
            ? (c.terminal.value<a.terminal.value?c.terminal:a.terminal)
            : (c.terminal.value>a.terminal.value?c.terminal:a.terminal),
          confirmedAt:Math.max(a.confirmedAt,c.confirmedAt),
        };
        out.splice(i,3,merged);
        changed=true;
        break;
      }
    }
    return out;
  }

  function coalesceOverlaps(segments){
    const out=[];
    for(const s of segments){
      const last=out.at(-1);
      if(!last){out.push({...s});continue;}

      if(last.state===s.state){
        if(s.state==='up'&&s.terminal.value>=last.terminal.value)last.terminal=s.terminal;
        if(s.state==='down'&&s.terminal.value<=last.terminal.value)last.terminal=s.terminal;
        last.confirmedAt=Math.max(last.confirmedAt,s.confirmedAt);
        continue;
      }

      // Opposite trend can start only from its own structural start. If that start lies
      // inside the previous trend, keep it provisional until hierarchy absorption decides.
      out.push({...s});
    }
    return out;
  }

  function buildHierarchicalTrends(swings){
    const evidence=buildEvidence(swings);
    let trends=mergeSameDirection(evidence);
    trends=absorbCountertrends(trends);
    trends=coalesceOverlaps(trends);
    trends=absorbCountertrends(trends);
    return {evidence,trends};
  }

  function pivotFrom(event,type,reason){
    const p=event.point;
    return {type,index:p.index,date:p.time,value:p.value,source:'monthly-hierarchical-trend',reason};
  }

  function buildPivots(trends){
    const candidates=[];
    for(const t of trends){
      if(t.state==='down'){
        candidates.push(pivotFrom(t.start,'high','downtrend-start'));
        candidates.push(pivotFrom(t.terminal,'low','downtrend-end'));
      }else{
        candidates.push(pivotFrom(t.start,'low','uptrend-start'));
        candidates.push(pivotFrom(t.terminal,'high','uptrend-end'));
      }
    }
    candidates.sort((a,b)=>a.index-b.index);

    const out=[];
    for(const p of candidates){
      const last=out.at(-1);
      if(last&&last.index===p.index)continue;
      if(last&&last.type===p.type){
        const better=p.type==='high'?p.value>=last.value:p.value<=last.value;
        if(better)out[out.length-1]=p;
        continue;
      }
      out.push(p);
    }
    return out;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<8){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-hierarchy-v4',major:0,sidewaysZones:0,deviation:0}};
    }

    const months=buildMonthlyExtremes(rows);
    if(months.length<4){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-hierarchy-v4',major:0,sidewaysZones:0,deviation:0,monthly:months.length,raw:rows.length}};
    }

    const swings=alternatingSwings(months);
    const {evidence,trends}=buildHierarchicalTrends(swings);
    const pivots=buildPivots(trends);

    let sidewaysZones=0;
    for(let i=0;i<trends.length-1;i++){
      if(trends[i].terminal.point.index<trends[i+1].start.point.index)sidewaysZones++;
    }

    return {
      pivots,
      path:pivots.map(p=>({date:p.date,value:p.value,virtual:false})),
      diagnostics:{
        engineVersion:'monthly-hierarchy-v4',
        major:pivots.length,
        sidewaysZones,
        deviation:0,
        monthly:months.length,
        raw:rows.length,
        swings:swings.length,
        confirmations:evidence.length,
        trends:trends.length,
      }
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();