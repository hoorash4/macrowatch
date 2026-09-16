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

  function sampleIndexForOriginal(sampled,originalIndex){
    let lo=0,hi=sampled.length-1;
    while(lo<hi){const mid=Math.floor((lo+hi)/2);if(sampled[mid].originalIndex<originalIndex)lo=mid+1;else hi=mid;}
    if(lo>0&&Math.abs(sampled[lo-1].originalIndex-originalIndex)<=Math.abs(sampled[lo].originalIndex-originalIndex))return lo-1;
    return lo;
  }

  function snapToRaw(rows,sampled,sampleIndex,type,rawRadius){
    const center=sampled[sampleIndex].originalIndex;
    const from=Math.max(0,center-rawRadius),to=Math.min(rows.length-1,center+rawRadius);
    let pick=from;
    for(let i=from+1;i<=to;i++){
      if(type==='high'&&rows[i].value>rows[pick].value)pick=i;
      if(type==='low'&&rows[i].value<rows[pick].value)pick=i;
    }
    return pick;
  }

  function makePivot(rows,sampled,sampleIndex,type,source,strength,reason,ctx,extra={}){
    const index=snapToRaw(rows,sampled,sampleIndex,type,ctx.rawSnapRadius);
    return {type,index,date:rows[index].time,value:rows[index].value,source,strength,reason,...extra};
  }

  function majorSkeleton(rows,sampled,values,ctx){
    const raw=[];
    for(let i=ctx.majorRadius;i<ctx.count-ctx.majorRadius;i++){
      let lmin=Infinity,lmax=-Infinity,rmin=Infinity,rmax=-Infinity;
      for(let k=i-ctx.majorRadius;k<i;k++){lmin=Math.min(lmin,values[k]);lmax=Math.max(lmax,values[k]);}
      for(let k=i+1;k<=i+ctx.majorRadius;k++){rmin=Math.min(rmin,values[k]);rmax=Math.max(rmax,values[k]);}
      const v=values[i],hp=Math.min(v-lmin,v-rmin),lp=Math.min(lmax-v,rmax-v);
      if(v>=lmax&&v>=rmax&&hp>=ctx.majorFloor)raw.push(makePivot(rows,sampled,i,'high','major',hp/ctx.majorFloor,'major-swing',ctx,{protected:true}));
      if(v<=lmin&&v<=rmin&&lp>=ctx.majorFloor)raw.push(makePivot(rows,sampled,i,'low','major',lp/ctx.majorFloor,'major-swing',ctx,{protected:true}));
    }
    raw.sort((a,b)=>a.index-b.index);
    const out=[];
    for(const p of raw){
      const last=out.at(-1);
      if(!last||last.type!==p.type){out.push(p);continue;}
      const better=p.type==='high'?p.value>last.value:p.value<last.value;
      if(better)out[out.length-1]=p;
    }
    return out;
  }

  function regressionSlope(values){
    const n=values.length;if(n<2)return 0;
    const mx=(n-1)/2,my=values.reduce((s,v)=>s+v,0)/n;
    let cov=0,varx=0;
    for(let i=0;i<n;i++){const dx=i-mx;cov+=dx*(values[i]-my);varx+=dx*dx;}
    return varx?cov/varx:0;
  }

  function efficiency(values){
    if(values.length<2)return 0;
    let travel=0;for(let i=1;i<values.length;i++)travel+=Math.abs(values[i]-values[i-1]);
    return travel?Math.abs(values.at(-1)-values[0])/travel:0;
  }

  function classifySidewaysWindow(w,ctx){
    const q10=quantile(w,.10),q90=quantile(w,.90),range=Math.max(q90-q10,1e-12);
    const net=Math.abs(w.at(-1)-w[0]);
    const slope=Math.abs(regressionSlope(w))*(w.length-1);

    const flatRange=Math.max(ctx.robustRange*.026,ctx.noise*4.0);
    const flatDrift=Math.max(ctx.robustRange*.012,ctx.noise*2.8);
    if(range<=flatRange&&net<=flatDrift&&slope<=flatDrift*1.15)return 'flat';

    const eff=efficiency(w);
    const oscillatingRange=Math.max(ctx.robustRange*.30,ctx.noise*11);
    if(range<=oscillatingRange&&eff<=.44&&net/range<=.62)return 'oscillating';
    return null;
  }

  function detectSidewaysZones(values,ctx){
    const half=clamp(Math.round(ctx.count*.026),3,Math.max(3,Math.round(ctx.count*.065)));
    const minRun=Math.max(3,Math.round(ctx.count*.013));
    const states=new Array(ctx.count).fill(null);
    for(let i=half;i<ctx.count-half;i++)states[i]=classifySidewaysWindow(values.slice(i-half,i+half+1),ctx);

    const zones=[];let start=null,type=null;
    for(let i=0;i<=ctx.count;i++){
      const state=i<ctx.count?states[i]:null;
      if(state&&start===null){start=i;type=state;continue;}
      if(state&&start!==null){if(state==='flat')type='flat';continue;}
      if(start!==null){
        const end=i-1;
        if(end-start+1>=minRun)zones.push({start:Math.max(0,start-half),end:Math.min(ctx.count-1,end+half),type});
        start=null;type=null;
      }
    }

    const merged=[];
    for(const z of zones){
      const last=merged.at(-1);
      if(last&&z.start<=last.end+Math.max(2,Math.round(half*.4))){last.end=Math.max(last.end,z.end);if(z.type==='flat')last.type='flat';}
      else merged.push({...z});
    }
    return merged;
  }

  function sidewaysBoundaries(rows,sampled,values,zones,ctx){
    const out=[];
    const look=Math.max(3,Math.round(ctx.count*.02));
    const moveFloor=Math.max(ctx.noise*1.5,ctx.robustRange*.0055);
    for(const z of zones){
      const before=values.slice(Math.max(0,z.start-look),z.start+1);
      const after=values.slice(z.end,Math.min(ctx.count,z.end+look+1));
      const beforeMove=before.length>1?before.at(-1)-before[0]:0;
      const afterMove=after.length>1?after.at(-1)-after[0]:0;
      let entryType=null,exitType=null;
      if(beforeMove<=-moveFloor)entryType='low';else if(beforeMove>=moveFloor)entryType='high';
      if(afterMove>=moveFloor)exitType='low';else if(afterMove<=-moveFloor)exitType='high';
      if(entryType)out.push(makePivot(rows,sampled,z.start,entryType,'sideways-boundary',1.2,`${z.type}-sideways-entry`,ctx,{zoneType:z.type}));
      if(exitType)out.push(makePivot(rows,sampled,z.end,exitType,'sideways-boundary',1.2,`${z.type}-sideways-exit`,ctx,{zoneType:z.type}));
    }
    return out;
  }

  function lineResiduals(values,a,b){
    const len=b-a,out=[];
    for(let o=0;o<=len;o++){
      const expected=values[a]+(values[b]-values[a])*(o/Math.max(1,len));
      out.push(values[a+o]-expected);
    }
    return out;
  }

  function lineError(values,a,b,split=null){
    const part=(from,to)=>{
      const len=to-from;if(len<=0)return 0;
      let total=0;
      for(let i=from;i<=to;i++){
        const expected=values[from]+(values[to]-values[from])*((i-from)/len);
        total+=Math.abs(values[i]-expected);
      }
      return total;
    };
    return split==null?part(a,b):part(a,split)+part(split,b);
  }

  function excursionRuns(residuals,band,edge){
    const runs=[];let start=null,sign=0;
    const close=end=>{
      if(start==null||end<start)return;
      let extreme=start;
      for(let i=start+1;i<=end;i++){
        if(sign>0&&residuals[i]>residuals[extreme])extreme=i;
        if(sign<0&&residuals[i]<residuals[extreme])extreme=i;
      }
      if(extreme>=edge&&extreme<=residuals.length-1-edge)runs.push({start,end,sign,extreme});
      start=null;sign=0;
    };
    for(let i=1;i<residuals.length-1;i++){
      const r=residuals[i],s=r>0?1:r<0?-1:0;
      if(Math.abs(r)<band){close(i-1);continue;}
      if(start==null){start=i;sign=s;continue;}
      if(s!==sign){close(i-1);start=i;sign=s;}
    }
    close(residuals.length-2);
    return runs;
  }

  function evaluateExcursion(values,a,b,run,ctx,depth){
    const len=b-a,residuals=lineResiduals(values,a,b),local=run.extreme;
    const magnitude=Math.abs(residuals[local]);
    const unit=Math.max(ctx.deviationUnit*(1+depth*.08),Math.abs(values[b]-values[a])*.028,1e-12);
    const peak=magnitude/unit;
    const duration=(run.end-run.start+1)/len;
    const returnBand=unit*.20;
    let returnIndex=null;
    for(let k=local+1;k<residuals.length;k++){
      if(Math.abs(residuals[k])<=returnBand){returnIndex=k;break;}
    }
    const recovery=((returnIndex==null?len:returnIndex)-local)/len;
    const unrecovered=returnIndex==null;
    const persistence=clamp(duration*.45+recovery*.55,0,1);

    const baseError=lineError(values,a,b);
    const split=a+local;
    const splitError=lineError(values,a,b,split);
    const improvement=baseError>1e-12?clamp((baseError-splitError)/baseError,0,1):0;

    const significance=Math.sqrt(Math.max(peak,0))*Math.sqrt(Math.max(persistence,0))*Math.sqrt(Math.max(improvement,0));
    return {sampleIndex:split,type:residuals[local]>0?'high':'low',peak,duration,recovery,unrecovered,persistence,improvement,significance};
  }

  function strongestDeviation(rows,sampled,values,leftOriginal,rightOriginal,depth,ctx){
    const a=sampleIndexForOriginal(sampled,leftOriginal),b=sampleIndexForOriginal(sampled,rightOriginal),len=b-a;
    const minLen=Math.max(10,Math.round(ctx.count*.024),Math.round(ctx.radius*1.05));
    if(len<minLen*2)return null;

    const residuals=lineResiduals(values,a,b);
    const unit=Math.max(ctx.deviationUnit*(1+depth*.08),Math.abs(values[b]-values[a])*.028,1e-12);
    const band=unit*.28;
    const edge=Math.max(3,Math.round(len*.055));
    const runs=excursionRuns(residuals,band,edge);
    let best=null;
    for(const run of runs){
      const e=evaluateExcursion(values,a,b,run,ctx,depth);
      if(e.peak<.62||e.improvement<.11||e.persistence<.025)continue;
      const threshold=.30*(1+depth*.06);
      if(e.significance<threshold)continue;
      if(!best||e.significance>best.significance)best=e;
    }
    if(!best)return null;

    const pivot=makePivot(rows,sampled,best.sampleIndex,best.type,'deviation',best.significance,'structural-deviation',ctx,{
      deviationPeak:best.peak,deviationDuration:best.duration,deviationRecovery:best.recovery,
      unrecovered:best.unrecovered,persistence:best.persistence,improvement:best.improvement,
      priority:best.significance,depth,
    });
    if(pivot.index<=leftOriginal||pivot.index>=rightOriginal)return null;
    return pivot;
  }

  function buildDeviationTree(rows,sampled,values,left,right,depth,ctx,out){
    if(depth>ctx.maxDeviationDepth)return;
    const pivot=strongestDeviation(rows,sampled,values,left,right,depth,ctx);
    if(!pivot)return;
    const gap=Math.max(2,Math.round(rows.length*.0025));
    if(pivot.index-left<gap||right-pivot.index<gap)return;

    out.push(pivot);
    buildDeviationTree(rows,sampled,values,left,pivot.index,depth+1,ctx,out);
    buildDeviationTree(rows,sampled,values,pivot.index,right,depth+1,ctx,out);
  }

  function revalidateDeviation(rows,sampled,values,majors,deviations,ctx){
    let kept=[...majors,...deviations].sort((a,b)=>a.index-b.index);
    let changed=true,guard=0;
    while(changed&&guard++<30){
      changed=false;
      for(let i=1;i<kept.length-1;i++){
        const p=kept[i];if(p.source!=='deviation')continue;
        const left=kept[i-1],right=kept[i+1];
        const a=sampleIndexForOriginal(sampled,left.index),b=sampleIndexForOriginal(sampled,right.index),s=sampleIndexForOriginal(sampled,p.index);
        if(!(a<s&&s<b)){kept.splice(i,1);changed=true;break;}
        const residuals=lineResiduals(values,a,b),local=s-a,sign=residuals[local]>0?1:-1;
        const unit=Math.max(ctx.deviationUnit*(1+(p.depth||0)*.08),Math.abs(values[b]-values[a])*.028,1e-12);
        const band=unit*.28;
        let runStart=local,runEnd=local;
        while(runStart>1&&sign*residuals[runStart-1]>=band)runStart--;
        while(runEnd<residuals.length-2&&sign*residuals[runEnd+1]>=band)runEnd++;
        const e=evaluateExcursion(values,a,b,{start:runStart,end:runEnd,sign,extreme:local},ctx,p.depth||0);
        const threshold=.30*(1+(p.depth||0)*.06);
        if(e.peak<.62||e.improvement<.11||e.persistence<.025||e.significance<threshold){kept.splice(i,1);changed=true;break;}
      }
    }
    return kept.filter(p=>p.source==='deviation');
  }

  function mergeSideways(majors,deviations,sideways,ctx){
    const base=[...majors,...deviations].sort((a,b)=>a.index-b.index);
    const near=Math.max(2,ctx.rawSnapRadius);
    for(const s of sideways){
      if(base.some(p=>Math.abs(p.index-s.index)<=near))continue;
      base.push(s);
    }
    return base.sort((a,b)=>a.index-b.index);
  }

  function finalDeviationRevalidation(rows,sampled,values,pivots,ctx){
    let kept=[...pivots].sort((a,b)=>a.index-b.index),changed=true,guard=0;
    while(changed&&guard++<30){
      changed=false;
      for(let i=1;i<kept.length-1;i++){
        const p=kept[i];if(p.source!=='deviation')continue;
        const left=kept[i-1],right=kept[i+1];
        const a=sampleIndexForOriginal(sampled,left.index),b=sampleIndexForOriginal(sampled,right.index),s=sampleIndexForOriginal(sampled,p.index);
        if(!(a<s&&s<b)){kept.splice(i,1);changed=true;break;}
        const residuals=lineResiduals(values,a,b),local=s-a,sign=residuals[local]>0?1:-1;
        const unit=Math.max(ctx.deviationUnit*(1+(p.depth||0)*.08),Math.abs(values[b]-values[a])*.028,1e-12),band=unit*.28;
        let start=local,end=local;
        while(start>1&&sign*residuals[start-1]>=band)start--;
        while(end<residuals.length-2&&sign*residuals[end+1]>=band)end++;
        const e=evaluateExcursion(values,a,b,{start,end,sign,extreme:local},ctx,p.depth||0);
        const threshold=.30*(1+(p.depth||0)*.06);
        if(e.peak<.62||e.improvement<.11||e.persistence<.025||e.significance<threshold){kept.splice(i,1);changed=true;break;}
      }
    }
    return kept;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<5)return {pivots:[],path:rows||[],diagnostics:{engineVersion:'frontend-structure-v4',major:0,sidewaysZones:0,deviation:0}};

    const sampled=minMaxSample(rows,1200),count=sampled.length;
    const smoothingWidth=odd(clamp(Math.round(count*.012),1,21));
    const values=smooth(sampled.map(r=>r.value),smoothingWidth);
    const robustRange=Math.max(quantile(values,.95)-quantile(values,.05),1e-12),diffs=[];
    for(let i=1;i<count;i++)diffs.push(Math.abs(values[i]-values[i-1]));
    const noise=Math.max(median(diffs),1e-12);
    const radius=clamp(Math.round(count*.03),2,Math.max(2,Math.round(count*.10)));
    const ctx={
      count,robustRange,noise,radius,
      majorRadius:clamp(Math.round(count*.105),4,Math.max(4,Math.round(count*.19))),
      majorFloor:Math.max(robustRange*.085,noise*4.5),
      deviationUnit:Math.max(robustRange*.040,noise*3.2,1e-12),
      rawSnapRadius:Math.max(1,Math.round(rows.length*.005)),
      maxDeviationDepth:5,
    };

    const majors=majorSkeleton(rows,sampled,values,ctx);
    const anchors=[{index:0,source:'virtual'},...majors,{index:rows.length-1,source:'virtual'}].sort((a,b)=>a.index-b.index);
    const deviationRaw=[];
    for(let i=0;i<anchors.length-1;i++)buildDeviationTree(rows,sampled,values,anchors[i].index,anchors[i+1].index,0,ctx,deviationRaw);
    const deviations=revalidateDeviation(rows,sampled,values,majors,deviationRaw,ctx);

    const zones=detectSidewaysZones(values,ctx);
    const sideways=sidewaysBoundaries(rows,sampled,values,zones,ctx);
    let pivots=mergeSideways(majors,deviations,sideways,ctx);
    pivots=finalDeviationRevalidation(rows,sampled,values,pivots,ctx);

    const path=[{date:rows[0].time,value:rows[0].value,virtual:true},...pivots.map(p=>({date:p.date,value:p.value,virtual:false})),{date:rows.at(-1).time,value:rows.at(-1).value,virtual:true}];
    return {
      pivots,path,
      diagnostics:{engineVersion:'frontend-structure-v4',major:majors.length,deviation:pivots.filter(p=>p.source==='deviation').length,sidewaysZones:zones.length,sidewaysBoundaries:pivots.filter(p=>p.source==='sideways-boundary').length,sampled:count,raw:rows.length}
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();