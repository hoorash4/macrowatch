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
    const bucket=Math.ceil(rows.length/(maxPoints/2));
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
    let travel=0;for(let i=1;i<values.length;i++)travel+=Math.abs(values[i]-values[i-1]);
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

  function isLocalExtreme(values,index,radius,type){
    const from=Math.max(0,index-radius),to=Math.min(values.length-1,index+radius),v=values[index];
    for(let i=from;i<=to;i++){
      if(i===index)continue;
      if(type==='high'&&values[i]>v)return false;
      if(type==='low'&&values[i]<v)return false;
    }
    return true;
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

  function makePivot(rows,sampled,values,sampleIndex,type,source,strength,reason,ctx){
    const originalIndex=snapToRaw(rows,sampled,sampleIndex,type,ctx.rawSnapRadius);
    return {type,index:originalIndex,date:rows[originalIndex].time,value:rows[originalIndex].value,source,strength,reason};
  }

  function majorSkeleton(rows,sampled,values,ctx){
    const raw=[];
    for(let i=ctx.majorRadius;i<ctx.count-ctx.majorRadius;i++){
      let lmin=Infinity,lmax=-Infinity,rmin=Infinity,rmax=-Infinity;
      for(let k=i-ctx.majorRadius;k<i;k++){lmin=Math.min(lmin,values[k]);lmax=Math.max(lmax,values[k]);}
      for(let k=i+1;k<=i+ctx.majorRadius;k++){rmin=Math.min(rmin,values[k]);rmax=Math.max(rmax,values[k]);}
      const v=values[i],hp=Math.min(v-lmin,v-rmin),lp=Math.min(lmax-v,rmax-v);
      if(v>=lmax&&v>=rmax&&hp>=ctx.majorFloor)raw.push(makePivot(rows,sampled,values,i,'high','major',hp/ctx.majorFloor,'major-swing',ctx));
      if(v<=lmin&&v<=rmin&&lp>=ctx.majorFloor)raw.push(makePivot(rows,sampled,values,i,'low','major',lp/ctx.majorFloor,'major-swing',ctx));
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

  function detectSidewaysZones(values,ctx){
    const half=clamp(Math.round(ctx.count*.045),4,Math.max(4,Math.round(ctx.count*.10)));
    const minRun=Math.max(4,Math.round(ctx.count*.018));
    const mask=new Array(ctx.count).fill(false);
    for(let i=half;i<ctx.count-half;i++){
      const w=values.slice(i-half,i+half+1);
      const range=Math.max(quantile(w,.9)-quantile(w,.1),1e-12),eff=efficiency(w),net=Math.abs(w.at(-1)-w[0]);
      mask[i]=range<=Math.max(ctx.robustRange*.32,ctx.noise*12)&&eff<=.48&&net/range<=.70;
    }
    const zones=[];let start=null;
    for(let i=0;i<=ctx.count;i++){
      const active=i<ctx.count&&mask[i];
      if(active&&start===null)start=i;
      if(!active&&start!==null){const end=i-1;if(end-start+1>=minRun)zones.push({start:Math.max(0,start-half),end:Math.min(ctx.count-1,end+half)});start=null;}
    }
    const merged=[];
    for(const z of zones){const last=merged.at(-1);if(last&&z.start<=last.end+Math.round(half*.6))last.end=Math.max(last.end,z.end);else merged.push({...z});}
    return merged;
  }

  function sidewaysCandidates(rows,sampled,values,zones,ctx){
    const out=[],look=Math.max(3,Math.round(ctx.count*.025)),slopeFloor=Math.max(ctx.noise*1.8,ctx.robustRange*.007);
    for(const z of zones){
      const before=values.slice(Math.max(0,z.start-look),z.start+1),after=values.slice(z.end,Math.min(ctx.count,z.end+look+1));
      const beforeMove=before.length>1?before.at(-1)-before[0]:0,afterMove=after.length>1?after.at(-1)-after[0]:0;
      if(beforeMove<=-slopeFloor)out.push(makePivot(rows,sampled,values,z.start,'low','sideways-boundary',1.25,'sideways-entry',ctx));
      else if(beforeMove>=slopeFloor)out.push(makePivot(rows,sampled,values,z.start,'high','sideways-boundary',1.25,'sideways-entry',ctx));
      if(afterMove>=slopeFloor)out.push(makePivot(rows,sampled,values,z.end,'low','sideways-boundary',1.25,'sideways-exit',ctx));
      else if(afterMove<=-slopeFloor)out.push(makePivot(rows,sampled,values,z.end,'high','sideways-boundary',1.25,'sideways-exit',ctx));
    }
    return out;
  }

  function sampleIndexForOriginal(sampled,originalIndex){
    let best=0,bestGap=Infinity;
    for(let i=0;i<sampled.length;i++){
      const gap=Math.abs(sampled[i].originalIndex-originalIndex);
      if(gap<bestGap){best=i;bestGap=gap;}
    }
    return best;
  }

  function deviationCandidates(rows,sampled,values,leftOriginal,rightOriginal,ctx){
    const a=sampleIndexForOriginal(sampled,leftOriginal),b=sampleIndexForOriginal(sampled,rightOriginal),len=b-a;
    const minLen=Math.max(10,Math.round(ctx.count*.03),Math.round(ctx.radius*1.25));
    if(len<minLen*2)return [];
    const residuals=[];
    for(let o=0;o<=len;o++){const expected=values[a]+(values[b]-values[a])*(o/len);residuals.push(values[a+o]-expected);}
    const unit=ctx.deviationUnit,edge=Math.max(3,Math.round(len*.07)),localRadius=Math.max(2,Math.min(Math.round(len*.05),Math.max(2,ctx.radius))),raw=[];
    for(let local=edge;local<=len-edge;local++){
      const r=residuals[local],mag=Math.abs(r);if(mag<unit*1.05)continue;
      const type=r>0?'high':'low',idx=a+local;if(!isLocalExtreme(values,idx,localRadius,type))continue;
      const sign=r>0?1:-1;let l=local,rr=local;
      while(l>0&&sign*residuals[l-1]>=unit*.45)l--;
      while(rr<residuals.length-1&&sign*residuals[rr+1]>=unit*.45)rr++;
      const width=rr-l+1,duration=width/len;let area=0;
      for(let k=l;k<=rr;k++)area+=Math.max(0,sign*residuals[k]-unit*.45);
      const areaNorm=area/(Math.max(1,len)*unit),peak=mag/unit;
      const passes=[peak>=1.12,duration>=.11,areaNorm>=.15].filter(Boolean).length>=2||(peak>=1.9&&duration>=.04);
      if(!passes)continue;
      raw.push({idx,type,priority:peak+Math.min(1.5,duration/.11)+Math.min(1.5,areaNorm/.15),peak});
    }
    raw.sort((x,y)=>y.priority-x.priority);
    const out=[],spacing=Math.max(3,Math.round(ctx.radius*.55));
    for(const c of raw){
      if(out.some(p=>Math.abs(p.sampleIndex-c.idx)<spacing))continue;
      const p=makePivot(rows,sampled,values,c.idx,c.type,'deviation',c.peak,'persistent-deviation',ctx);p.sampleIndex=c.idx;p.priority=c.priority;out.push(p);
      if(out.length>=4)break;
    }
    return out.sort((x,y)=>x.index-y.index);
  }

  function assemble(rows,sampled,values,majors,sideways,ctx){
    const anchors=[{index:0,type:null},...majors.map(p=>({index:p.index,type:p.type})),{index:rows.length-1,type:null}].sort((a,b)=>a.index-b.index);
    const secondaries=[...sideways];
    for(let i=0;i<anchors.length-1;i++)secondaries.push(...deviationCandidates(rows,sampled,values,anchors[i].index,anchors[i+1].index,ctx));
    const result=[...majors];
    for(let i=0;i<anchors.length-1;i++){
      const left=anchors[i],right=anchors[i+1],inside=secondaries.filter(p=>p.index>left.index&&p.index<right.index).sort((a,b)=>a.index-b.index);
      let expected=left.type==='high'?'low':left.type==='low'?'high':null;
      for(const p of inside){
        if(expected&&p.type!==expected)continue;
        result.push(p);expected=p.type==='high'?'low':'high';
      }
    }
    result.sort((a,b)=>a.index-b.index);
    const final=[];
    for(const p of result){
      const last=final.at(-1);
      if(!last||last.type!==p.type){final.push(p);continue;}
      if(last.source==='major'&&p.source!=='major')continue;
      if(last.source!=='major'&&p.source==='major'){final[final.length-1]=p;continue;}
      if(last.source==='major'&&p.source==='major'){final.push(p);continue;}
      const lp=last.priority||0,pp=p.priority||0;if(pp>lp)final[final.length-1]=p;
    }
    return final;
  }

  function detect(rows){
    if(!Array.isArray(rows)||rows.length<5)return {pivots:[],path:rows||[],diagnostics:{engineVersion:'frontend-major-anchor-v1',major:0,sidewaysZones:0,deviation:0}};
    const sampled=minMaxSample(rows,1200),count=sampled.length,smoothingWidth=odd(clamp(Math.round(count*.014),1,25)),values=smooth(sampled.map(r=>r.value),smoothingWidth);
    const robustRange=Math.max(quantile(values,.95)-quantile(values,.05),1e-12),diffs=[];
    for(let i=1;i<count;i++)diffs.push(Math.abs(values[i]-values[i-1]));
    const noise=Math.max(median(diffs),1e-12),radius=clamp(Math.round(count*.032),2,Math.max(2,Math.round(count*.11))),majorRadius=clamp(Math.round(count*.11),4,Math.max(4,Math.round(count*.2)));
    const ctx={count,robustRange,noise,radius,majorRadius,majorFloor:Math.max(robustRange*.085,noise*4.5),deviationUnit:Math.max(robustRange*.05,noise*3.8,1e-12),rawSnapRadius:Math.max(1,Math.round(rows.length*.006))};
    const majors=majorSkeleton(rows,sampled,values,ctx),zones=detectSidewaysZones(values,ctx),sideways=sidewaysCandidates(rows,sampled,values,zones,ctx),pivots=assemble(rows,sampled,values,majors,sideways,ctx);
    const path=[{date:rows[0].time,value:rows[0].value,virtual:true},...pivots.map(p=>({date:p.date,value:p.value,virtual:false})),{date:rows.at(-1).time,value:rows.at(-1).value,virtual:true}];
    return {pivots,path,diagnostics:{engineVersion:'frontend-major-anchor-v1',major:majors.length,sidewaysZones:zones.length,sidewaysBoundaries:pivots.filter(p=>p.source==='sideways-boundary').length,deviation:pivots.filter(p=>p.source==='deviation').length,sampled:count,raw:rows.length}};
  }

  window.PivotLabEngine=Object.freeze({detect});
})();