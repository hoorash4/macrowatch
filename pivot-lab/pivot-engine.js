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

  // ---- 1) Immutable major structure -------------------------------------------------

  function majorSkeleton(rows,sampled,values,ctx){
    const candidates=[];
    for(let i=ctx.majorRadius;i<ctx.count-ctx.majorRadius;i++){
      let lmin=Infinity,lmax=-Infinity,rmin=Infinity,rmax=-Infinity;
      for(let k=i-ctx.majorRadius;k<i;k++){lmin=Math.min(lmin,values[k]);lmax=Math.max(lmax,values[k]);}
      for(let k=i+1;k<=i+ctx.majorRadius;k++){rmin=Math.min(rmin,values[k]);rmax=Math.max(rmax,values[k]);}
      const v=values[i],highProm=Math.min(v-lmin,v-rmin),lowProm=Math.min(lmax-v,rmax-v);
      if(v>=lmax&&v>=rmax&&highProm>=ctx.majorFloor){
        candidates.push(makePivot(rows,sampled,i,'high','major',highProm/ctx.totalRange,'major-swing',ctx,{protected:true}));
      }
      if(v<=lmin&&v<=rmin&&lowProm>=ctx.majorFloor){
        candidates.push(makePivot(rows,sampled,i,'low','major',lowProm/ctx.totalRange,'major-swing',ctx,{protected:true}));
      }
    }

    candidates.sort((a,b)=>a.index-b.index);
    const majors=[];
    for(const p of candidates){
      const last=majors.at(-1);
      if(!last||last.type!==p.type){majors.push(p);continue;}
      const better=p.type==='high'?p.value>last.value:p.value<last.value;
      if(better)majors[majors.length-1]=p;
    }
    return majors;
  }

  // ---- 2) Recursive deviation: exactly one extreme per segment ---------------------

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

  function excursionRuns(residuals,activation,edge){
    const runs=[];let start=null,sign=0;
    const close=end=>{
      if(start==null||end<start){start=null;sign=0;return;}
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
      if(Math.abs(r)<activation){close(i-1);continue;}
      if(start==null){start=i;sign=s;continue;}
      if(s!==sign){close(i-1);start=i;sign=s;}
    }
    close(residuals.length-2);
    return runs;
  }

  function evaluateRun(values,a,b,residuals,run,depth,ctx){
    const len=b-a,local=run.extreme,magnitude=Math.abs(residuals[local]);
    const distanceRatio=magnitude/ctx.totalRange;
    const durationRatio=(run.end-run.start+1)/len;

    // Return is measured against the whole chart scale, not day-to-day noise.
    const returnBand=ctx.totalRange*.012;
    let returnIndex=null;
    for(let k=local+1;k<residuals.length;k++){
      if(Math.abs(residuals[k])<=returnBand){returnIndex=k;break;}
    }
    const recoveryRatio=((returnIndex==null?len:returnIndex)-local)/len;
    const unrecovered=returnIndex==null;

    // Short-lived excursions are deliberately penalized even when visually tall.
    const timeAway=clamp(Math.max(durationRatio,recoveryRatio),0,1);
    if(timeAway<ctx.minTimeAway)return null;

    const split=a+local;
    const beforeError=lineError(values,a,b);
    const afterError=lineError(values,a,b,split);
    const improvement=beforeError>1e-12?clamp((beforeError-afterError)/beforeError,0,1):0;
    if(improvement<ctx.minImprovement)return null;

    // One continuous significance measure: relative distance x persistence x explanatory gain.
    const persistenceWeight=Math.sqrt(timeAway);
    const improvementWeight=Math.sqrt(improvement);
    const significance=distanceRatio*persistenceWeight*improvementWeight;
    const threshold=ctx.minDeviationSignificance*(1+depth*.12);
    if(significance<threshold)return null;

    return {
      sampleIndex:split,
      type:residuals[local]>0?'high':'low',
      distanceRatio,
      durationRatio,
      recoveryRatio,
      unrecovered,
      improvement,
      significance,
    };
  }

  function strongestDeviation(rows,sampled,values,leftOriginal,rightOriginal,depth,ctx){
    const a=sampleIndexForOriginal(sampled,leftOriginal),b=sampleIndexForOriginal(sampled,rightOriginal),len=b-a;
    const minSegment=Math.max(10,Math.round(ctx.count*.022),Math.round(ctx.radius));
    if(len<minSegment*2)return null;

    const residuals=lineResiduals(values,a,b);
    const activation=Math.max(ctx.totalRange*.010,1e-12);
    const edge=Math.max(3,Math.round(len*.05));
    const runs=excursionRuns(residuals,activation,edge);
    let best=null;

    // Every excursion contributes only its single farthest point.
    // This segment accepts only the strongest surviving excursion, then the line is rebuilt.
    for(const run of runs){
      const candidate=evaluateRun(values,a,b,residuals,run,depth,ctx);
      if(!candidate)continue;
      if(!best||candidate.significance>best.significance)best=candidate;
    }
    if(!best)return null;

    const pivot=makePivot(rows,sampled,best.sampleIndex,best.type,'deviation',best.significance,'structural-deviation',ctx,{
      distanceRatio:best.distanceRatio,
      deviationDuration:best.durationRatio,
      deviationRecovery:best.recoveryRatio,
      unrecovered:best.unrecovered,
      improvement:best.improvement,
      priority:best.significance,
      depth,
    });
    if(pivot.index<=leftOriginal||pivot.index>=rightOriginal)return null;
    return pivot;
  }

  function recursiveDeviation(rows,sampled,values,left,right,depth,ctx,out){
    if(depth>ctx.maxDeviationDepth)return;
    const pivot=strongestDeviation(rows,sampled,values,left,right,depth,ctx);
    if(!pivot)return;

    const minRawGap=Math.max(2,Math.round(rows.length*.0025));
    if(pivot.index-left<minRawGap||right-pivot.index<minRawGap)return;

    out.push(pivot);
    recursiveDeviation(rows,sampled,values,left,pivot.index,depth+1,ctx,out);
    recursiveDeviation(rows,sampled,values,pivot.index,right,depth+1,ctx,out);
  }

  function deviationStructure(rows,sampled,values,majors,ctx){
    const deviations=[];
    for(let i=0;i<majors.length-1;i++){
      recursiveDeviation(rows,sampled,values,majors[i].index,majors[i+1].index,0,ctx,deviations);
    }
    return deviations.sort((a,b)=>a.index-b.index);
  }

  function revalidateDeviation(rows,sampled,values,majors,deviations,ctx){
    let structure=[...majors,...deviations].sort((a,b)=>a.index-b.index);
    let changed=true,guard=0;

    while(changed&&guard++<40){
      changed=false;
      for(let i=1;i<structure.length-1;i++){
        const current=structure[i];
        if(current.source!=='deviation')continue;
        const left=structure[i-1],right=structure[i+1];

        // Re-evaluate the current secondary against the NEW line formed by its neighbours.
        const strongest=strongestDeviation(rows,sampled,values,left.index,right.index,current.depth||0,ctx);
        if(!strongest){structure.splice(i,1);changed=true;break;}

        const tolerance=Math.max(ctx.rawSnapRadius*2,Math.round(rows.length*.004));
        if(Math.abs(strongest.index-current.index)>tolerance){
          // A different extreme now explains this interval better: replace the old secondary.
          structure[i]=strongest;
          structure.sort((a,b)=>a.index-b.index);
          changed=true;
          break;
        }
      }
    }

    return structure.filter(p=>p.source==='deviation').sort((a,b)=>a.index-b.index);
  }

  // ---- 3) Sideways after the deviation skeleton ------------------------------------

  function classifySidewaysWindow(w,ctx){
    const q10=quantile(w,.10),q90=quantile(w,.90);
    const range=Math.max(q90-q10,0);
    const net=Math.abs(w.at(-1)-w[0]);
    const slopeSpan=Math.abs(regressionSlope(w))*(w.length-1);
    const rangeRatio=range/ctx.totalRange;
    const driftRatio=net/ctx.totalRange;
    const slopeRatio=slopeSpan/ctx.totalRange;

    // Flat: amplitude is tiny on the FULL chart scale. No noisy local ratio can veto it.
    if(rangeRatio<=ctx.flatRangeRatio&&driftRatio<=ctx.flatDriftRatio&&slopeRatio<=ctx.flatSlopeRatio)return 'flat';

    // Box: some internal oscillation is fine if it remains small relative to the full chart.
    const eff=efficiency(w);
    if(rangeRatio<=ctx.boxRangeRatio&&driftRatio<=ctx.boxDriftRatio&&eff<=ctx.boxEfficiency)return 'box';
    return null;
  }

  function detectSidewaysZones(values,ctx,firstSample,lastSample){
    const span=Math.max(1,lastSample-firstSample+1);
    const half=clamp(Math.round(span*.025),3,Math.max(3,Math.round(span*.065)));
    const minRun=Math.max(3,Math.round(span*.014));
    const states=new Array(values.length).fill(null);

    for(let i=Math.max(firstSample+half,half);i<=Math.min(lastSample-half,values.length-half-1);i++){
      states[i]=classifySidewaysWindow(values.slice(i-half,i+half+1),ctx);
    }

    const zones=[];let start=null,type=null;
    for(let i=firstSample;i<=lastSample+1;i++){
      const state=i<=lastSample?states[i]:null;
      if(state&&start==null){start=i;type=state;continue;}
      if(state&&start!=null){if(state==='flat')type='flat';continue;}
      if(start!=null){
        const end=i-1;
        if(end-start+1>=minRun)zones.push({start:Math.max(firstSample,start-half),end:Math.min(lastSample,end+half),type});
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

  function sidewaysBoundaries(rows,sampled,values,zones,ctx,structure){
    const out=[];
    const look=Math.max(3,Math.round(ctx.count*.018));
    const slopeFloor=ctx.totalRange*.006;

    for(const z of zones){
      const before=values.slice(Math.max(0,z.start-look),z.start+1);
      const after=values.slice(z.end,Math.min(values.length,z.end+look+1));
      const beforeMove=before.length>1?before.at(-1)-before[0]:0;
      const afterMove=after.length>1?after.at(-1)-after[0]:0;
      let entryType=null,exitType=null;
      if(beforeMove<=-slopeFloor)entryType='low';else if(beforeMove>=slopeFloor)entryType='high';
      if(afterMove>=slopeFloor)exitType='low';else if(afterMove<=-slopeFloor)exitType='high';

      const add=(sampleIndex,type,reason)=>{
        if(!type)return;
        const p=makePivot(rows,sampled,sampleIndex,type,'sideways-boundary',1,reason,ctx,{zoneType:z.type});
        const near=Math.max(ctx.rawSnapRadius*2,Math.round(rows.length*.004));
        if(structure.some(s=>Math.abs(s.index-p.index)<=near))return;
        if(out.some(s=>Math.abs(s.index-p.index)<=near))return;
        out.push(p);
      };
      add(z.start,entryType,`${z.type}-sideways-entry`);
      add(z.end,exitType,`${z.type}-sideways-exit`);
    }
    return out;
  }

  function finalSecondaryValidation(rows,sampled,values,majors,deviations,sideways,ctx){
    // Major pivots are immutable. Deviation points have already been re-evaluated.
    // Sideways boundaries are kept only when they do not create same-type clutter next to a stronger structure point.
    const all=[...majors,...deviations,...sideways].sort((a,b)=>a.index-b.index);
    const out=[];
    for(const p of all){
      const last=out.at(-1);
      if(!last||last.type!==p.type){out.push(p);continue;}
      if(last.source==='major'){continue;}
      if(p.source==='major'){out[out.length-1]=p;continue;}
      if(last.source==='deviation'&&p.source==='sideways-boundary')continue;
      if(last.source==='sideways-boundary'&&p.source==='deviation'){out[out.length-1]=p;continue;}
      // Same-class secondary collision: keep the more structural one.
      const lastStrength=last.priority||last.strength||0;
      const nextStrength=p.priority||p.strength||0;
      if(nextStrength>lastStrength)out[out.length-1]=p;
    }
    return out;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<5)return {pivots:[],path:[],diagnostics:{engineVersion:'frontend-structure-v5',major:0,sidewaysZones:0,deviation:0}};

    const sampled=minMaxSample(rows,1200);
    const count=sampled.length;
    const smoothingWidth=odd(clamp(Math.round(count*.012),1,21));
    const values=smooth(sampled.map(r=>r.value),smoothingWidth);
    const totalRange=Math.max(quantile(values,.95)-quantile(values,.05),1e-12);
    const diffs=[];for(let i=1;i<count;i++)diffs.push(Math.abs(values[i]-values[i-1]));
    const noise=Math.max(median(diffs),1e-12);
    const radius=clamp(Math.round(count*.03),2,Math.max(2,Math.round(count*.10)));
    const majorRadius=clamp(Math.round(count*.105),4,Math.max(4,Math.round(count*.19)));

    const ctx={
      count,totalRange,noise,radius,majorRadius,
      majorFloor:Math.max(totalRange*.080,noise*4.2),
      rawSnapRadius:Math.max(1,Math.round(rows.length*.005)),
      maxDeviationDepth:5,
      minTimeAway:.035,
      minImprovement:.12,
      minDeviationSignificance:.022,
      flatRangeRatio:.045,
      flatDriftRatio:.018,
      flatSlopeRatio:.018,
      boxRangeRatio:.12,
      boxDriftRatio:.035,
      boxEfficiency:.36,
    };

    // Start/end are display data only; they are never anchors or pivots.
    const majors=majorSkeleton(rows,sampled,values,ctx);
    if(majors.length<2){
      return {pivots:majors,path:majors.map(p=>({date:p.date,value:p.value,virtual:false})),diagnostics:{engineVersion:'frontend-structure-v5',major:majors.length,sidewaysZones:0,deviation:0,sampled:count,raw:rows.length}};
    }

    let deviations=deviationStructure(rows,sampled,values,majors,ctx);
    deviations=revalidateDeviation(rows,sampled,values,majors,deviations,ctx);

    const core=[...majors,...deviations].sort((a,b)=>a.index-b.index);
    const firstSample=sampleIndexForOriginal(sampled,majors[0].index);
    const lastSample=sampleIndexForOriginal(sampled,majors.at(-1).index);
    const zones=detectSidewaysZones(values,ctx,firstSample,lastSample);
    const sideways=sidewaysBoundaries(rows,sampled,values,zones,ctx,core);

    const pivots=finalSecondaryValidation(rows,sampled,values,majors,deviations,sideways,ctx).sort((a,b)=>a.index-b.index);
    const path=pivots.map(p=>({date:p.date,value:p.value,virtual:false}));

    return {
      pivots,path,
      diagnostics:{
        engineVersion:'frontend-structure-v5',
        major:majors.length,
        deviation:pivots.filter(p=>p.source==='deviation').length,
        sidewaysZones:zones.length,
        sidewaysBoundaries:pivots.filter(p=>p.source==='sideways-boundary').length,
        sampled:count,raw:rows.length,totalRange,
      },
    };
  }

  window.PivotLabEngine=Object.freeze({detect});
})();