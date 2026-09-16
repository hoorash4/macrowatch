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

  function direction(a,b){return b>a?1:b<a?-1:0;}

  // Every swing can update the two most recent highs/lows. A directional observation
  // exists only when BOTH high direction and low direction agree.
  function structuralObservations(swings){
    const highs=[],lows=[],out=[];
    for(const event of swings){
      if(event.type==='high'){
        highs.push(event);
        if(highs.length>2)highs.shift();
      }else{
        lows.push(event);
        if(lows.length>2)lows.shift();
      }
      if(highs.length<2||lows.length<2)continue;

      const [h1,h2]=highs,[l1,l2]=lows;
      const hd=direction(h1.value,h2.value),ld=direction(l1.value,l2.value);
      let state=null;
      if(hd===1&&ld===1)state='up';
      else if(hd===-1&&ld===-1)state='down';

      out.push({
        state,
        at:event.point.index,
        highPair:[h1,h2],
        lowPair:[l1,l2],
        start:state==='up'?l1:state==='down'?h1:null,
        terminal:state==='up'?h2:state==='down'?l2:null,
      });
    }
    return out;
  }

  function extendTerminal(trend,observation){
    if(!observation||observation.state!==trend.state)return;
    if(trend.state==='up'){
      if(observation.terminal.value>=trend.terminal.value)trend.terminal=observation.terminal;
    }else if(observation.terminal.value<=trend.terminal.value){
      trend.terminal=observation.terminal;
    }
    trend.confirmations++;
    trend.confirmedAt=observation.at;
  }

  // Moderate hierarchy:
  // 1 directional observation = provisional only.
  // 2 same-direction observations = confirmed trend.
  // A confirmed trend is not reversed by one opposite observation; the opposite side
  // must also confirm twice. Mixed high/low direction remains unresolved and creates no pivot.
  function confirmTrends(observations){
    const trends=[];
    let active=null;
    let candidate=null;

    for(const o of observations){
      if(!o.state)continue;

      if(!active){
        if(!candidate||candidate.state!==o.state){
          candidate={state:o.state,count:1,start:o.start,terminal:o.terminal,firstAt:o.at,lastAt:o.at};
          continue;
        }
        candidate.count++;
        candidate.lastAt=o.at;
        if(candidate.state==='up'){
          if(o.terminal.value>=candidate.terminal.value)candidate.terminal=o.terminal;
        }else if(o.terminal.value<=candidate.terminal.value){
          candidate.terminal=o.terminal;
        }
        if(candidate.count>=2){
          active={
            state:candidate.state,
            start:candidate.start,
            terminal:candidate.terminal,
            confirmations:candidate.count,
            confirmedAt:candidate.lastAt,
          };
          candidate=null;
        }
        continue;
      }

      if(o.state===active.state){
        candidate=null;
        extendTerminal(active,o);
        continue;
      }

      // Opposite direction: keep it provisional until it confirms twice.
      if(!candidate||candidate.state!==o.state){
        candidate={state:o.state,count:1,start:o.start,terminal:o.terminal,firstAt:o.at,lastAt:o.at};
        continue;
      }

      candidate.count++;
      candidate.lastAt=o.at;
      if(candidate.state==='up'){
        if(o.terminal.value>=candidate.terminal.value)candidate.terminal=o.terminal;
      }else if(o.terminal.value<=candidate.terminal.value){
        candidate.terminal=o.terminal;
      }

      if(candidate.count>=2){
        trends.push(active);
        active={
          state:candidate.state,
          start:candidate.start,
          terminal:candidate.terminal,
          confirmations:candidate.count,
          confirmedAt:candidate.lastAt,
        };
        candidate=null;
      }
    }

    if(active)trends.push(active);
    return trends;
  }

  function resumedBeyond(parent,later){
    if(parent.state!==later.state)return false;
    return parent.state==='down'
      ? later.terminal.value<parent.terminal.value
      : later.terminal.value>parent.terminal.value;
  }

  // A minimal two-confirmation countertrend can still turn out to be an internal wave.
  // If the original larger direction resumes and exceeds its old terminal, erase ONLY that
  // minimum-strength middle trend. Stronger/longer countertrends survive as real regimes.
  function absorbWeakCountertrends(trends){
    let out=trends.map(t=>({...t}));
    let changed=true,guard=0;
    while(changed&&guard++<100){
      changed=false;
      for(let i=0;i<out.length-2;i++){
        const a=out[i],b=out[i+1],c=out[i+2];
        if(a.state!==c.state||b.state===a.state)continue;
        if(b.confirmations!==2)continue;
        if(!resumedBeyond(a,c))continue;

        const merged={
          state:a.state,
          start:a.start,
          terminal:a.state==='down'
            ? (c.terminal.value<a.terminal.value?c.terminal:a.terminal)
            : (c.terminal.value>a.terminal.value?c.terminal:a.terminal),
          confirmations:a.confirmations+c.confirmations,
          confirmedAt:Math.max(a.confirmedAt,c.confirmedAt),
        };
        out.splice(i,3,merged);
        changed=true;
        break;
      }
    }
    return out;
  }

  function pivotFrom(event,type,reason){
    const p=event.point;
    return {type,index:p.index,date:p.time,value:p.value,source:'monthly-balanced-trend',reason};
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
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-balanced-v5',major:0,sidewaysZones:0,deviation:0}};
    }

    const months=buildMonthlyExtremes(rows);
    if(months.length<4){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-balanced-v5',major:0,sidewaysZones:0,deviation:0,monthly:months.length,raw:rows.length}};
    }

    const swings=alternatingSwings(months);
    const observations=structuralObservations(swings);
    let trends=confirmTrends(observations);
    trends=absorbWeakCountertrends(trends);
    const pivots=buildPivots(trends);

    let sidewaysZones=0;
    for(let i=0;i<trends.length-1;i++){
      if(trends[i].terminal.point.index<trends[i+1].start.point.index)sidewaysZones++;
    }

    return {
      pivots,
      path:pivots.map(p=>({date:p.date,value:p.value,virtual:false})),
      diagnostics:{
        engineVersion:'monthly-balanced-v5',
        major:pivots.length,
        sidewaysZones,
        deviation:0,
        monthly:months.length,
        raw:rows.length,
        swings:swings.length,
        observations:observations.length,
        trends:trends.length,
      }
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();