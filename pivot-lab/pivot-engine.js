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

  function efficiency(values){
    if(values.length<2)return 0;
    let travel=0;
    for(let i=1;i<values.length;i++)travel+=Math.abs(values[i]-values[i-1]);
    return travel?Math.abs(values.at(-1)-values[0])/travel:0;
  }

  function localExtremeIndex(values,center,radius,type){
    const from=Math.max(0,center-radius),to=Math.min(values.length-1,center+radius);
    let pick=from;
    for(let i=from+1;i<=to;i++){
      if(type==='high'&&values[i]>values[pick])pick=i;
      if(type==='low'&&values[i]<values[pick])pick=i;
    }
    return pick;
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
    const originalIndex=snapToRaw(rows,sampled,sampleIndex,type,ctx.rawSnapRadius);
    return {type,index:originalIndex,date:rows[originalIndex].time,value:rows[originalIndex].value,source,strength,reason,...extra};
  }

  function sampleIndexForOriginal(sampled,originalIndex){
    let lo=0,hi=sampled.length-1;
    while(lo<hi){
      const mid=Math.floor((lo+hi)/2);
      if(sampled[mid].originalIndex<originalIndex)lo=mid+1;else hi=mid;
    }
    if(lo>0&&Math.abs(sampled[lo-1].originalIndex-originalIndex)<=Math.abs(sampled[lo].originalIndex-originalIndex))return lo-1;
    return lo;
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

  function classifySidewaysWindow(w,ctx){
    const q10=quantile(w,.10),q90=quantile(w,.90),range=Math.max(q90-q10,1e-12);
    const net=Math.abs(w.at(-1)-w[0]);
    const eff=efficiency(w);
    const flatRange=Math.max(ctx.robustRange*.018,ctx.noise*3.2);
    const flatDrift=Math.max(ctx.robustRange*.008,ctx.noise*2.2);
    if(range<=flatRange&&net<=flatDrift)return 'flat';

    const oscillatingRange=Math.max(ctx.robustRange*.28,ctx.noise*10);
    if(range<=oscillatingRange&&eff<=.42&&net/range<=.58)return 'oscillating';
    return null;
  }

  function detectSidewaysZones(values,ctx){
    const half=clamp(Math.round(ctx.count*.028),3,Math.max(3,Math.round(ctx.count*.07)));
    const minRun=Math.max(3,Math.round(ctx.count*.015));
    const states=new Array(ctx.count).fill(null);
    for(let i=half;i<ctx.count-half;i++)states[i]=classifySidewaysWindow(values.slice(i-half,i+half+1),ctx);

    const zones=[];
    let start=null,type=null;
    for(let i=0;i<=ctx.count;i++){
      const state=i<ctx.count?states[i]:null;
      if(state&&start===null){start=i;type=state;continue;}
      if(state&&start!==null){
        if(type==='oscillating'&&state==='flat')type='flat';
        continue;
      }
      if(start!==null){
        const end=i-1;
        if(end-start+1>=minRun)zones.push({start:Math.max(0,start-half),end:Math.min(ctx.count-1,end+half),type});
        start=null;type=null;
      }
    }

    const merged=[];
    for(const zone of zones){
      const last=merged.at(-1);
      if(last&&zone.start<=last.end+Math.max(2,Math.round(half*.45))){
        last.end=Math.max(last.end,zone.end);
        if(zone.type==='flat')last.type='flat';
      }else merged.push({...zone});
    }
    return merged;
  }

  function sidewaysCandidates(rows,sampled,values,zones,ctx){
    const out=[];
    const look=Math.max(3,Math.round(ctx.count*.022));
    const slopeFloor=Math.max(ctx.noise*1.6,ctx.robustRange*.006);
    for(const z of zones){
      const before=values.slice(Math.max(0,z.start-look),z.start+1);
      const after=values.slice(z.end,Math.min(ctx.count,z.end+look+1));
      const beforeMove=before.length>1?before.at(-1)-before[0]:0;
      const afterMove=after.length>1?after.at(-1)-after[0]:0;

      let entryType=null,exitType=null;
      if(beforeMove<=-slopeFloor)entryType='low';
      else if(beforeMove>=slopeFloor)entryType='high';
      if(afterMove>=slopeFloor)exitType='low';
      else if(afterMove<=-slopeFloor)exitType='high';

      if(entryType)out.push(makePivot(rows,sampled,z.start,entryType,'sideways-boundary',1.3,`${z.type}-sideways-entry`,ctx,{zoneType:z.type}));
      if(exitType)out.push(makePivot(rows,sampled,z.end,exitType,'sideways-boundary',1.3,`${z.type}-sideways-exit`,ctx,{zoneType:z.type}));
    }
    return out;
  }

  function lineError(values,a,b,split=null){
    const absError=(from,to)=>{
      const len=to-from;
      if(len<=0)return 0;
      let total=0;
      for(let i=from;i<=to;i++){
        const expected=values[from]+(values[to]-values[from])*((i-from)/len);
        total+=Math.abs(values[i]-expected);
      }
      return total;
    };
    if(split==null)return absError(a,b);
    return absError(a,split)+absError(split,b);
  }

  function strongestDeviation(rows,sampled,values,leftOriginal,rightOriginal,depth,ctx){
    const a=sampleIndexForOriginal(sampled,leftOriginal),b=sampleIndexForOriginal(sampled,rightOriginal),len=b-a;
    const minLen=Math.max(10,Math.round(ctx.count*.025),Math.round(ctx.radius*1.1));
    if(len<minLen*2)return null;

    const residuals=[];
    for(let o=0;o<=len;o++){
      const expected=values[a]+(values[b]-values[a])*(o/len);
      residuals.push(values[a+o]-expected);
    }

    const depthPenalty=1+depth*.12;
    const unit=Math.max(ctx.deviationUnit*depthPenalty,Math.abs(values[b]-values[a])*.035);
    const activation=unit*.34;
    const returnBand=unit*.24;
    const edge=Math.max(3,Math.round(len*.065));
    const baseError=lineError(values,a,b);
    if(baseError<=1e-12)return null;

    const excursions=[];
    let runStart=null,runSign=0;
    const closeRun=(runEnd)=>{
      if(runStart==null||runEnd<runStart)return;
      let extreme=runStart;
      for(let local=runStart+1;local<=runEnd;local++){
        if(runSign>0&&residuals[local]>residuals[extreme])extreme=local;
        if(runSign<0&&residuals[local]<residuals[extreme])extreme=local;
      }
      if(extreme>=edge&&extreme<=len-edge)excursions.push({start:runStart,end:runEnd,sign:runSign,extreme});
      runStart=null;runSign=0;
    };

    for(let local=1;local<len;local++){
      const r=residuals[local],sign=r>0?1:r<0?-1:0;
      const active=Math.abs(r)>=activation;
      if(!active){closeRun(local-1);continue;}
      if(runStart==null){runStart=local;runSign=sign;continue;}
      if(sign!==runSign){closeRun(local-1);runStart=local;runSign=sign;}
    }
    closeRun(len-1);

    let best=null;
    for(const ex of excursions){
      const local=ex.extreme,mag=Math.abs(residuals[local]),peak=mag/unit;
      const duration=(ex.end-ex.start+1)/len;
      let recovery=0;
      for(let k=local+1;k<=len;k++){
        if(Math.abs(residuals[k])<=returnBand){recovery=(k-local)/len;break;}
      }
      if(recovery===0)recovery=(len-local)/len;

      const split=a+local;
      const improvedError=lineError(values,a,b,split);
      const improvement=clamp((baseError-improvedError)/baseError,0,1);

      const huge=peak>=1.65&&improvement>=.16;
      const normal=peak>=1.0&&improvement>=.26&&(duration>=.055||recovery>=.05);
      if(!huge&&!normal)continue;

      const type=residuals[local]>0?'high':'low';
      const score=peak+improvement*.45+Math.max(duration,recovery)*.35;
      if(!best||score>best.score)best={sampleIndex:split,type,peak,duration,recovery,improvement,score,huge};
    }

    if(!best)return null;
    const pivot=makePivot(rows,sampled,best.sampleIndex,best.type,'deviation',best.peak,best.huge?'extreme-deviation':'persistent-deviation',ctx,{
      deviationDuration:best.duration,
      deviationRecovery:best.recovery,
      improvement:best.improvement,
      priority:best.score,
      depth,
    });
    if(pivot.index<=leftOriginal||pivot.index>=rightOriginal)return null;
    return pivot;
  }

  function recursiveDeviation(rows,sampled,values,leftOriginal,rightOriginal,depth,ctx,out){
    if(depth>ctx.maxDeviationDepth)return;
    const candidate=strongestDeviation(rows,sampled,values,leftOriginal,rightOriginal,depth,ctx);
    if(!candidate)return;
    const minRawGap=Math.max(2,Math.round(rows.length*.0025));
    if(candidate.index-leftOriginal<minRawGap||rightOriginal-candidate.index<minRawGap)return;

    out.push(candidate);
    recursiveDeviation(rows,sampled,values,leftOriginal,candidate.index,depth+1,ctx,out);
    recursiveDeviation(rows,sampled,values,candidate.index,rightOriginal,depth+1,ctx,out);
  }

  function dedupeStructural(majors,sideways,ctx){
    const all=[...majors,...sideways].sort((a,b)=>a.index-b.index);
    const out=[];
    const near=Math.max(2,ctx.rawSnapRadius);
    for(const p of all){
      const last=out.at(-1);
      if(!last||Math.abs(last.index-p.index)>near){out.push(p);continue;}
      if(last.source==='major')continue;
      if(p.source==='major'){out[out.length-1]=p;continue;}
      out[out.length-1]=p;
    }
    return out;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<5)return {pivots:[],path:rows||[],diagnostics:{engineVersion:'frontend-structure-v2',major:0,sidewaysZones:0,deviation:0}};

    const sampled=minMaxSample(rows,1200);
    const count=sampled.length;
    const smoothingWidth=odd(clamp(Math.round(count*.012),1,21));
    const values=smooth(sampled.map(r=>r.value),smoothingWidth);
    const robustRange=Math.max(quantile(values,.95)-quantile(values,.05),1e-12);
    const diffs=[];
    for(let i=1;i<count;i++)diffs.push(Math.abs(values[i]-values[i-1]));
    const noise=Math.max(median(diffs),1e-12);
    const radius=clamp(Math.round(count*.03),2,Math.max(2,Math.round(count*.10)));
    const majorRadius=clamp(Math.round(count*.105),4,Math.max(4,Math.round(count*.19)));
    const ctx={
      count,
      robustRange,
      noise,
      radius,
      majorRadius,
      majorFloor:Math.max(robustRange*.082,noise*4.3),
      deviationUnit:Math.max(robustRange*.04,noise*3.2,1e-12),
      rawSnapRadius:Math.max(1,Math.round(rows.length*.005)),
      maxDeviationDepth:6,
    };

    const majors=majorSkeleton(rows,sampled,values,ctx);
    const zones=detectSidewaysZones(values,ctx);
    const sideways=sidewaysCandidates(rows,sampled,values,zones,ctx);
    const structural=dedupeStructural(majors,sideways,ctx);

    const anchors=[0,...structural.map(p=>p.index),rows.length-1].sort((a,b)=>a-b);
    const deviations=[];
    for(let i=0;i<anchors.length-1;i++){
      if(anchors[i+1]-anchors[i]<3)continue;
      recursiveDeviation(rows,sampled,values,anchors[i],anchors[i+1],0,ctx,deviations);
    }

    const pivots=[...structural,...deviations].sort((a,b)=>a.index-b.index);
    const path=[{date:rows[0].time,value:rows[0].value,virtual:true},...pivots.map(p=>({date:p.date,value:p.value,virtual:false})),{date:rows.at(-1).time,value:rows.at(-1).value,virtual:true}];
    const flatZones=zones.filter(z=>z.type==='flat').length;
    const oscillatingZones=zones.filter(z=>z.type==='oscillating').length;

    return {
      pivots,
      path,
      diagnostics:{
        engineVersion:'frontend-structure-v2',
        major:majors.length,
        sidewaysZones:zones.length,
        flatSidewaysZones:flatZones,
        oscillatingSidewaysZones:oscillatingZones,
        sidewaysBoundaries:sideways.length,
        deviation:deviations.length,
        sampled:count,
        raw:rows.length,
      },
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();