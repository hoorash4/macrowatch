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

  function classifyMonthlyMoves(months){
    const moves=[];
    for(let i=1;i<months.length;i++){
      const prev=months[i-1],cur=months[i];
      const highUp=cur.high.value>prev.high.value;
      const highDown=cur.high.value<prev.high.value;
      const lowUp=cur.low.value>prev.low.value;
      const lowDown=cur.low.value<prev.low.value;

      let state='sideways';
      if(highUp&&lowUp)state='up';
      else if(highDown&&lowDown)state='down';
      else if(lowDown&&!highUp)state='down';
      else if(highUp&&!lowDown)state='up';

      moves.push({monthIndex:i,state,highUp,highDown,lowUp,lowDown});
    }
    return moves;
  }

  function smoothStates(moves){
    if(!moves.length)return [];
    const states=moves.map(m=>m.state);

    // One isolated contradictory month inside a sustained trend is treated as an internal wave.
    for(let i=1;i<states.length-1;i++){
      if(states[i-1]===states[i+1]&&states[i]!==states[i-1])states[i]=states[i-1];
    }

    // Require two consecutive directional months to start a fresh directional regime.
    // Until then, the interval belongs to sideways/transition rather than creating a tiny trend.
    const out=[states[0]];
    for(let i=1;i<states.length;i++){
      const s=states[i];
      const prev=out[i-1];
      if(s===prev){out.push(s);continue;}
      if(s==='sideways'){out.push('sideways');continue;}
      const next=states[i+1];
      out.push(next===s?s:'sideways');
    }
    return out;
  }

  function buildRegimes(months,moves,states){
    if(!states.length)return [];
    const regimes=[];
    let startMove=0,state=states[0];
    for(let i=1;i<=states.length;i++){
      if(i<states.length&&states[i]===state)continue;
      regimes.push({
        state,
        startMonth:startMove,
        endMonth:i,
      });
      if(i<states.length){startMove=i;state=states[i];}
    }

    // Merge very short sideways gaps sandwiched by the same directional regime.
    let changed=true;
    while(changed){
      changed=false;
      for(let i=1;i<regimes.length-1;i++){
        const a=regimes[i-1],b=regimes[i],c=regimes[i+1];
        const span=b.endMonth-b.startMonth;
        if(b.state==='sideways'&&span<=1&&a.state===c.state&&a.state!=='sideways'){
          a.endMonth=c.endMonth;
          regimes.splice(i,2);
          changed=true;
          break;
        }
      }
    }
    return regimes;
  }

  function extremeInRegime(months,regime){
    const from=Math.max(0,regime.startMonth);
    const to=Math.min(months.length-1,regime.endMonth);
    if(regime.state==='down'){
      let best=months[from].low;
      for(let i=from+1;i<=to;i++)if(months[i].low.value<best.value)best=months[i].low;
      return {type:'low',point:best};
    }
    if(regime.state==='up'){
      let best=months[from].high;
      for(let i=from+1;i<=to;i++)if(months[i].high.value>best.value)best=months[i].high;
      return {type:'high',point:best};
    }
    return null;
  }

  function boundaryPivot(months,left,right){
    // A directional regime ending into sideways/opposite regime contributes its terminal extreme.
    if(left.state==='down')return extremeInRegime(months,left);
    if(left.state==='up')return extremeInRegime(months,left);

    // Sideways ending into a new trend: use the first breakout month's relevant monthly extreme.
    if(left.state==='sideways'&&right.state==='down'){
      const m=months[Math.min(months.length-1,right.startMonth+1)];
      return {type:'low',point:m.low};
    }
    if(left.state==='sideways'&&right.state==='up'){
      const m=months[Math.min(months.length-1,right.startMonth+1)];
      return {type:'high',point:m.high};
    }
    return null;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<8){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-regime-v1',major:0,sidewaysZones:0,deviation:0}};
    }

    const months=buildMonthlyExtremes(rows);
    if(months.length<4){
      return {pivots:[],path:[],diagnostics:{engineVersion:'monthly-regime-v1',major:0,sidewaysZones:0,deviation:0,monthly:months.length,raw:rows.length}};
    }

    const moves=classifyMonthlyMoves(months);
    const states=smoothStates(moves);
    const regimes=buildRegimes(months,moves,states);

    const candidates=[];
    for(let i=0;i<regimes.length-1;i++){
      const left=regimes[i],right=regimes[i+1];
      const pivot=boundaryPivot(months,left,right);
      if(!pivot)continue;
      candidates.push({
        type:pivot.type,
        index:pivot.point.index,
        date:pivot.point.time,
        value:pivot.point.value,
        source:'monthly-regime-boundary',
        reason:`${left.state}-to-${right.state}`,
      });
    }

    candidates.sort((a,b)=>a.index-b.index);
    const pivots=[];
    for(const p of candidates){
      const last=pivots.at(-1);
      if(last&&last.index===p.index)continue;
      if(last&&last.type===p.type){
        const better=p.type==='high'?p.value>last.value:p.value<last.value;
        if(better)pivots[pivots.length-1]=p;
        continue;
      }
      pivots.push(p);
    }

    const sidewaysZones=regimes.filter(r=>r.state==='sideways').length;
    return {
      pivots,
      path:pivots.map(p=>({date:p.date,value:p.value,virtual:false})),
      diagnostics:{
        engineVersion:'monthly-regime-v1',
        major:pivots.length,
        sidewaysZones,
        deviation:0,
        monthly:months.length,
        raw:rows.length,
        regimes:regimes.length,
      }
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();