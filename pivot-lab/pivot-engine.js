(() => {
  'use strict';

  const clamp=(v,min,max)=>Math.max(min,Math.min(max,v));
  const median=values=>{if(!values.length)return 0;const a=[...values].sort((x,y)=>x-y),m=Math.floor(a.length/2);return a.length%2?a[m]:(a[m-1]+a[m])/2;};
  const quantile=(values,q)=>{if(!values.length)return 0;const a=[...values].sort((x,y)=>x-y),p=(a.length-1)*q,b=Math.floor(p),r=p-b;return a[b+1]===undefined?a[b]:a[b]+r*(a[b+1]-a[b]);};
  const odd=v=>v%2?v:v+1;

  function smooth(values,width){
    if(width<=1)return [...values];
    const h=Math.floor(width/2);
    return values.map((_,i)=>median(values.slice(Math.max(0,i-h),Math.min(values.length,i+h+1))));
  }

  function minMaxSample(rows,maxPoints=1200){
    if(rows.length<=maxPoints)return rows.map((row,index)=>({...row,originalIndex:index}));
    const bucket=Math.max(2,Math.ceil(rows.length/(maxPoints/2)));
    const out=[];
    for(let start=0;start<rows.length;start+=bucket){
      const end=Math.min(rows.length,start+bucket);
      let minI=start,maxI=start;
      for(let i=start+1;i<end;i++){
        if(rows[i].value<rows[minI].value)minI=i;
        if(rows[i].value>rows[maxI].value)maxI=i;
      }
      for(const i of [...new Set([minI,maxI])].sort((a,b)=>a-b))out.push({...rows[i],originalIndex:i});
    }
    if(out[0]?.originalIndex!==0)out.unshift({...rows[0],originalIndex:0});
    if(out.at(-1)?.originalIndex!==rows.length-1)out.push({...rows.at(-1),originalIndex:rows.length-1});
    return out;
  }

  function normalizeScreen(sampled,values){
    const ymin=Math.min(...values),ymax=Math.max(...values),yrange=Math.max(ymax-ymin,1e-12);
    const aspect=2.15;
    const last=Math.max(1,values.length-1);
    return values.map((value,i)=>({x:(i/last)*aspect,y:(value-ymin)/yrange,sampleIndex:i,originalIndex:sampled[i].originalIndex}));
  }

  function pointLineDistance(p,a,b){
    const dx=b.x-a.x,dy=b.y-a.y,den=Math.hypot(dx,dy);
    if(den<=1e-12)return Math.hypot(p.x-a.x,p.y-a.y);
    return Math.abs(dy*p.x-dx*p.y+b.x*a.y-b.y*a.x)/den;
  }

  function rdpIndices(points,epsilon){
    const keep=new Set([0,points.length-1]),stack=[[0,points.length-1]];
    while(stack.length){
      const [a,b]=stack.pop();
      if(b-a<2)continue;
      let best=-1,bestD=0;
      for(let i=a+1;i<b;i++){
        const d=pointLineDistance(points[i],points[a],points[b]);
        if(d>bestD){bestD=d;best=i;}
      }
      if(best>=0&&bestD>epsilon){keep.add(best);stack.push([a,best],[best,b]);}
    }
    return [...keep].sort((a,b)=>a-b);
  }

  function regressionSlope(points,from,to){
    const n=to-from+1;if(n<2)return 0;
    let mx=0,my=0;
    for(let i=from;i<=to;i++){mx+=points[i].x;my+=points[i].y;}
    mx/=n;my/=n;
    let cov=0,varx=0;
    for(let i=from;i<=to;i++){const dx=points[i].x-mx;cov+=dx*(points[i].y-my);varx+=dx*dx;}
    return varx?cov/varx:0;
  }

  function detectPlateaus(points,values){
    const n=points.length;
    const half=clamp(Math.round(n*.018),3,Math.max(3,Math.round(n*.045)));
    const minRun=Math.max(4,Math.round(n*.028));
    const totalRange=Math.max(Math.max(...values)-Math.min(...values),1e-12);
    const mask=new Array(n).fill(false);

    for(let i=half;i<n-half;i++){
      const slice=values.slice(i-half,i+half+1);
      const range=(quantile(slice,.90)-quantile(slice,.10))/totalRange;
      const drift=Math.abs(slice.at(-1)-slice[0])/totalRange;
      const slope=Math.abs(regressionSlope(points,i-half,i+half));
      mask[i]=range<=.060&&drift<=.035&&slope<=.055;
    }

    const zones=[];let start=null;
    for(let i=0;i<=n;i++){
      const active=i<n&&mask[i];
      if(active&&start==null)start=i;
      if(!active&&start!=null){
        const end=i-1;
        if(end-start+1>=minRun)zones.push({start:Math.max(1,start-half),end:Math.min(n-2,end+half)});
        start=null;
      }
    }

    const merged=[];
    for(const z of zones){
      const last=merged.at(-1);
      if(last&&z.start<=last.end+Math.max(2,Math.round(half*.5)))last.end=Math.max(last.end,z.end);
      else merged.push({...z});
    }
    return merged;
  }

  function slopeChangeCandidates(points){
    const n=points.length;
    const w=clamp(Math.round(n*.022),3,Math.max(3,Math.round(n*.055)));
    const candidates=[];
    for(let i=w;i<n-w;i++){
      const left=regressionSlope(points,i-w,i),right=regressionSlope(points,i,i+w);
      const delta=Math.abs(Math.atan(right)-Math.atan(left));
      if(delta<.48)continue;
      const localSpan=Math.abs(points[i+w].y-points[i-w].y);
      if(delta>=.75||localSpan>=.055)candidates.push({index:i,score:delta+localSpan});
    }
    candidates.sort((a,b)=>b.score-a.score);
    const chosen=[];
    const spacing=Math.max(3,Math.round(n*.025));
    for(const c of candidates){
      if(chosen.some(x=>Math.abs(x.index-c.index)<spacing))continue;
      chosen.push(c);
    }
    return chosen.map(c=>c.index).sort((a,b)=>a-b);
  }

  function classifyAnchor(points,index,prevIndex,nextIndex){
    const left=(points[index].y-points[prevIndex].y)/Math.max(points[index].x-points[prevIndex].x,1e-12);
    const right=(points[nextIndex].y-points[index].y)/Math.max(points[nextIndex].x-points[index].x,1e-12);
    if(left>=0&&right<=0)return 'high';
    if(left<=0&&right>=0)return 'low';
    return right-left<0?'high':'low';
  }

  function snapToRaw(rows,sampled,sampleIndex,type,rawRadius){
    const center=sampled[sampleIndex].originalIndex;
    const from=Math.max(0,center-rawRadius),to=Math.min(rows.length-1,center+rawRadius);
    let pick=center;
    for(let i=from;i<=to;i++){
      if(type==='high'&&rows[i].value>rows[pick].value)pick=i;
      if(type==='low'&&rows[i].value<rows[pick].value)pick=i;
    }
    return pick;
  }

  function maxDeviationBetween(points,a,b){
    let best=0;
    for(let i=a+1;i<b;i++)best=Math.max(best,pointLineDistance(points[i],points[a],points[b]));
    return best;
  }

  function pruneAnchors(points,indices,protectedSet,epsilon){
    let out=[...new Set(indices)].sort((a,b)=>a-b),changed=true,guard=0;
    while(changed&&guard++<100){
      changed=false;
      for(let i=1;i<out.length-1;i++){
        const idx=out[i];
        if(protectedSet.has(idx))continue;
        const a=out[i-1],b=out[i+1];
        const deviation=maxDeviationBetween(points,a,b);
        const slope1=(points[idx].y-points[a].y)/Math.max(points[idx].x-points[a].x,1e-12);
        const slope2=(points[b].y-points[idx].y)/Math.max(points[b].x-points[idx].x,1e-12);
        const angleDelta=Math.abs(Math.atan(slope2)-Math.atan(slope1));
        if(deviation<=epsilon*1.12&&angleDelta<.52){out.splice(i,1);changed=true;break;}
      }
    }
    return out;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<6)return {pivots:[],path:[],diagnostics:{engineVersion:'screen-shape-v1',major:0,sidewaysZones:0,deviation:0}};

    const sampled=minMaxSample(rows,1200);
    const count=sampled.length;
    const smoothingWidth=odd(clamp(Math.round(count*.008),1,15));
    const values=smooth(sampled.map(r=>r.value),smoothingWidth);
    const points=normalizeScreen(sampled,values);

    const epsilon=.047;
    const rdp=rdpIndices(points,epsilon);
    const plateaus=detectPlateaus(points,values);
    const slopeChanges=slopeChangeCandidates(points);

    const protectedSet=new Set();
    for(const z of plateaus){protectedSet.add(z.start);protectedSet.add(z.end);}

    let anchors=pruneAnchors(points,[...rdp,...slopeChanges,...protectedSet],protectedSet,epsilon);
    anchors=anchors.filter(i=>i>0&&i<count-1);

    const rawSnapRadius=Math.max(1,Math.round(rows.length*.004));
    const pivots=[];
    for(let pos=0;pos<anchors.length;pos++){
      const idx=anchors[pos];
      const prev=pos===0?0:anchors[pos-1];
      const next=pos===anchors.length-1?count-1:anchors[pos+1];
      const type=classifyAnchor(points,idx,prev,next);
      const originalIndex=snapToRaw(rows,sampled,idx,type,rawSnapRadius);
      if(originalIndex<=0||originalIndex>=rows.length-1)continue;
      const source=protectedSet.has(idx)?'sideways-boundary':'structure';
      pivots.push({type,index:originalIndex,date:rows[originalIndex].time,value:rows[originalIndex].value,source,reason:source==='sideways-boundary'?'screen-relative-sideways':'screen-shape-turn'});
    }

    pivots.sort((a,b)=>a.index-b.index);
    const deduped=[];
    for(const p of pivots){
      const last=deduped.at(-1);
      if(last&&Math.abs(last.index-p.index)<=rawSnapRadius){
        if(last.source!=='sideways-boundary'&&p.source==='sideways-boundary')deduped[deduped.length-1]=p;
        continue;
      }
      deduped.push(p);
    }

    return {
      pivots:deduped,
      path:deduped.map(p=>({date:p.date,value:p.value,virtual:false})),
      diagnostics:{engineVersion:'screen-shape-v1',major:deduped.filter(p=>p.source==='structure').length,sidewaysZones:plateaus.length,deviation:0,sampled:count,raw:rows.length,epsilon}
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();