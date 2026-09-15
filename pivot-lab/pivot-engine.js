(() => {
  'use strict';

  const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
  const median=values=>{
    if(!values.length)return 0;
    const sorted=[...values].sort((a,b)=>a-b),mid=Math.floor(sorted.length/2);
    return sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2;
  };
  const quantile=(values,q)=>{
    if(!values.length)return 0;
    const sorted=[...values].sort((a,b)=>a-b),pos=(sorted.length-1)*q,base=Math.floor(pos),rest=pos-base;
    return sorted[base+1]===undefined?sorted[base]:sorted[base]+rest*(sorted[base+1]-sorted[base]);
  };
  const odd=value=>value%2?value:value+1;

  function centeredMedian(values,width){
    if(width<=1)return [...values];
    const half=Math.floor(width/2);
    return values.map((_,index)=>median(values.slice(Math.max(0,index-half),Math.min(values.length,index+half+1))));
  }

  function rawExtremeIndex(rows,center,radius,type){
    const from=Math.max(0,center-radius),to=Math.min(rows.length-1,center+radius);
    let selected=from;
    for(let index=from+1;index<=to;index++){
      if(type==='high'&&rows[index].value>rows[selected].value)selected=index;
      if(type==='low'&&rows[index].value<rows[selected].value)selected=index;
    }
    return selected;
  }

  function regressionStats(values){
    const n=values.length;
    if(n<3)return {slope:0,r2:0,efficiency:0,predictedSpan:0};
    const meanX=(n-1)/2,meanY=values.reduce((sum,value)=>sum+value,0)/n;
    let cov=0,varX=0,ssTot=0;
    for(let index=0;index<n;index++){
      const dx=index-meanX,dy=values[index]-meanY;
      cov+=dx*dy;varX+=dx*dx;ssTot+=dy*dy;
    }
    const slope=varX?cov/varX:0,intercept=meanY-slope*meanX;
    let ssRes=0,travel=0;
    for(let index=0;index<n;index++){
      const residual=values[index]-(intercept+slope*index);
      ssRes+=residual*residual;
      if(index)travel+=Math.abs(values[index]-values[index-1]);
    }
    const net=values.at(-1)-values[0];
    const efficiency=travel?Math.abs(net)/travel:0;
    return {slope,r2:ssTot?clamp(1-ssRes/ssTot,0,1):0,efficiency,predictedSpan:Math.abs(slope)*(n-1)};
  }

  function sameTypeRepresentative(last,item){
    const better=item.type==='high'?item.value>last.value:item.value<last.value;
    const threshold=Math.max(((last.requiredThreshold||0)+(item.requiredThreshold||0))/2,1e-12);
    const similar=Math.abs(item.value-last.value)<=threshold*.42;
    const sideways=(last.regimeState==='sideways'||item.regimeState==='sideways');
    if(better||(similar&&sideways))return item;
    return last;
  }

  function normalizeAlternation(items){
    const out=[];
    for(const item of items){
      const last=out.at(-1);
      if(!last||last.type!==item.type){out.push(item);continue;}
      out[out.length-1]=sameTypeRepresentative(last,item);
    }
    return out;
  }

  function localRegime(smoothed,index,coarseHalfWidth,fineHalfWidth,visibleRange,noise){
    const coarseFrom=Math.max(0,index-coarseHalfWidth),coarseTo=Math.min(smoothed.length-1,index+coarseHalfWidth);
    const fineFrom=Math.max(0,index-fineHalfWidth),fineTo=Math.min(smoothed.length-1,index+fineHalfWidth);
    const coarse=smoothed.slice(coarseFrom,coarseTo+1),fine=smoothed.slice(fineFrom,fineTo+1);
    if(coarse.length<5||fine.length<3)return {confidence:0,state:'choppy',direction:0,multiplier:.74,radiusMultiplier:.72,efficiency:0};

    const stats=regressionStats(coarse);
    const coarseRange=Math.max(quantile(coarse,.9)-quantile(coarse,.1),noise*2,1e-12);
    const directionalExtent=clamp(stats.predictedSpan/Math.max(coarseRange*.72,noise*5,1e-12),0,1);
    const third=Math.max(1,Math.floor(coarse.length/3));
    const first=median(coarse.slice(0,third)),middle=median(coarse.slice(third,coarse.length-third)),last=median(coarse.slice(coarse.length-third));
    const direction=stats.slope>0?1:stats.slope<0?-1:0;
    const monotone=direction>0?(first<=middle&&middle<=last):direction<0?(first>=middle&&middle>=last):false;
    const monotoneFactor=monotone?1:.68;
    const trendConfidence=clamp((stats.r2*.58+stats.efficiency*.42)*directionalExtent*monotoneFactor*1.18,0,1);

    const fineRange=Math.max(quantile(fine,.9)-quantile(fine,.1),noise*2,1e-12);
    const fineStats=regressionStats(fine);
    const coarseMedians=[first,middle,last];
    const medianDrift=Math.max(...coarseMedians)-Math.min(...coarseMedians);
    const bandStability=clamp(1-medianDrift/Math.max(coarseRange*.58,noise*4,1e-12),0,1);
    const compression=clamp(1-coarseRange/Math.max(visibleRange*.34,noise*10,1e-12),0,1);
    const fineOscillation=clamp(1-fineStats.efficiency,0,1);
    const sidewaysConfidence=clamp(fineOscillation*(compression*.58+bandStability*.42)*(1-trendConfidence*.55),0,1);

    let state='choppy',confidence=Math.max(trendConfidence,sidewaysConfidence);
    if(trendConfidence>=.5&&trendConfidence>=sidewaysConfidence*1.05)state='trend';
    else if(sidewaysConfidence>=.54)state='sideways';

    let multiplier,radiusMultiplier;
    if(state==='trend'){
      multiplier=clamp(1.05+confidence*.7,1.05,1.75);
      radiusMultiplier=clamp(1.05+confidence*.55,1.05,1.6);
    }else if(state==='sideways'){
      multiplier=clamp(1.12+confidence*.62,1.12,1.72);
      radiusMultiplier=clamp(1.1+confidence*.55,1.1,1.62);
    }else{
      const ambiguity=clamp(1-Math.max(trendConfidence,sidewaysConfidence),0,1);
      multiplier=clamp(.88-ambiguity*.18,.7,.9);
      radiusMultiplier=clamp(.88-ambiguity*.22,.62,.9);
    }
    return {confidence,state,direction:state==='trend'?direction:0,multiplier,radiusMultiplier,efficiency:stats.efficiency,localRange:fineRange,trendConfidence,sidewaysConfidence};
  }

  function transientSpike(rows,index,noise,robustRange){
    if(index<2||index>rows.length-3)return false;
    const value=rows[index].value;
    const left=median([rows[index-2].value,rows[index-1].value]);
    const right=median([rows[index+1].value,rows[index+2].value]);
    const baseline=(left+right)/2;
    const excursion=Math.abs(value-baseline);
    const sideGap=Math.abs(left-right);
    const spikeFloor=Math.max(noise*5.5,robustRange*.065,1e-12);
    return excursion>=spikeFloor&&sideGap<=excursion*.34;
  }

  function continuationDirection(left,right){
    if(left.type!==right.type)return 0;
    if(left.type==='low'&&right.value<left.value)return -1;
    if(left.type==='high'&&right.value>left.value)return 1;
    return 0;
  }

  function tripletTrendSupport(left,current,right,direction){
    if(!direction)return 0;
    const items=[left,current,right].filter(item=>item.regimeState==='trend'&&item.regimeDirection===direction);
    if(!items.length)return 0;
    return items.reduce((sum,item)=>sum+item.regimeConfidence,0)/3;
  }

  function simplifyStructure(items){
    let out=normalizeAlternation(items);
    let changed=true;
    while(changed&&out.length>=3){
      changed=false;
      for(let index=1;index<out.length-1;index++){
        const left=out[index-1],current=out[index],right=out[index+1];
        const leftMove=Math.abs(current.value-left.value),rightMove=Math.abs(right.value-current.value);
        const excursion=Math.min(leftMove,rightMove),dominantMove=Math.max(leftMove,rightMove,1e-12);
        const threshold=Math.max((left.requiredThreshold+current.requiredThreshold+right.requiredThreshold)/3,1e-12);
        const localRadius=Math.max(2,Math.round((left.localRadius+current.localRadius+right.localRadius)/3));
        const leftGap=current.index-left.index,rightGap=right.index-current.index;
        const minGap=Math.min(leftGap,rightGap);

        const direction=continuationDirection(left,right);
        const trendSupport=tripletTrendSupport(left,current,right,direction);
        const correctionRatio=excursion/dominantMove;
        const correctionFloor=.2+trendSupport*.22;
        const trendContinuation=direction!==0&&trendSupport>=.34&&correctionRatio<correctionFloor&&excursion<threshold*(1.18+trendSupport*.45);

        const outerLevelGap=Math.abs(right.value-left.value);
        const sidewaysSupport=[left,current,right].reduce((sum,item)=>sum+(item.regimeState==='sideways'?item.regimeConfidence:0),0)/3;
        const sidewaysOscillation=sidewaysSupport>=.28&&outerLevelGap<=threshold*.9&&excursion<threshold*(1.32+sidewaysSupport*.35);

        const choppyContext=[left,current,right].filter(item=>item.regimeState==='choppy').length>=2;
        const weakFloor=choppyContext?.72:1;
        const weakMove=excursion<threshold*weakFloor;
        const tooTight=minGap<localRadius*.62&&excursion<threshold*(choppyContext?1.08:1.42);

        if(!trendContinuation&&!sidewaysOscillation&&!weakMove&&!tooTight)continue;
        out.splice(index,1);
        out=normalizeAlternation(out);
        changed=true;
        break;
      }
    }
    return out;
  }

  function detect(rows,{startIndex=0,endIndex=null}={}){
    const count=rows.length;
    if(count<5)return Object.freeze({pivots:Object.freeze([]),threshold:0,radius:0,smoothingWidth:1,startIndex:0,endIndex:Math.max(0,count-1),averageRegimeConfidence:0});

    const visibleStart=clamp(Math.floor(startIndex),0,count-1);
    const visibleEnd=clamp(Math.ceil(endIndex==null?count-1:endIndex),visibleStart,count-1);
    const visibleCount=Math.max(1,visibleEnd-visibleStart+1);
    const buffer=Math.max(3,Math.round(visibleCount*.24));
    const workStart=Math.max(0,visibleStart-buffer),workEnd=Math.min(count-1,visibleEnd+buffer);
    const work=rows.slice(workStart,workEnd+1);
    const localVisibleStart=visibleStart-workStart,localVisibleEnd=visibleEnd-workStart;

    const smoothingWidth=odd(clamp(Math.round(visibleCount*.016),1,31));
    const rawValues=work.map(row=>row.value);
    const smoothed=centeredMedian(rawValues,smoothingWidth);
    const visibleValues=smoothed.slice(localVisibleStart,localVisibleEnd+1);
    const q05=quantile(visibleValues,.05),q95=quantile(visibleValues,.95);
    const robustRange=Math.max(q95-q05,1e-12);
    const diffs=[];
    for(let index=localVisibleStart+1;index<=localVisibleEnd;index++)diffs.push(Math.abs(smoothed[index]-smoothed[index-1]));
    const noise=Math.max(median(diffs),1e-12);
    const threshold=Math.max(robustRange*.048,noise*3.1,1e-12);
    const radius=clamp(Math.round(visibleCount*.033),2,Math.max(2,Math.round(visibleCount*.11)));
    const rawRadius=Math.max(1,Math.floor(smoothingWidth/2));
    const coarseHalfWidth=clamp(Math.round(visibleCount*.14),6,Math.max(6,Math.round(visibleCount*.24)));
    const fineHalfWidth=clamp(Math.round(visibleCount*.055),3,Math.max(3,Math.round(visibleCount*.11)));
    const regimes=smoothed.map((_,index)=>localRegime(smoothed,index,coarseHalfWidth,fineHalfWidth,robustRange,noise));
    const candidates=[];

    for(let index=2;index<work.length-2;index++){
      const regime=regimes[index];
      const localRadius=clamp(Math.round(radius*regime.radiusMultiplier),2,Math.max(2,Math.round(radius*1.65)));
      if(index<localRadius||index>=work.length-localRadius)continue;
      const value=smoothed[index];
      const left=smoothed.slice(index-localRadius,index),right=smoothed.slice(index+1,index+localRadius+1);
      if(!left.length||!right.length)continue;
      const leftMax=Math.max(...left),rightMax=Math.max(...right),leftMin=Math.min(...left),rightMin=Math.min(...right);
      const high=value>=leftMax&&value>=rightMax;
      const low=value<=leftMin&&value<=rightMin;
      const highProminence=high?Math.min(value-leftMin,value-rightMin):0;
      const lowProminence=low?Math.min(leftMax-value,rightMax-value):0;
      const localThreshold=threshold*regime.multiplier;
      let type=null;
      if(highProminence>=localThreshold*.76&&highProminence>=lowProminence)type='high';
      else if(lowProminence>=localThreshold*.76)type='low';
      if(!type)continue;

      let rawIndex=rawExtremeIndex(work,index,rawRadius,type);
      if(transientSpike(work,rawIndex,noise,robustRange)){
        if(rawIndex!==index&&!transientSpike(work,index,noise,robustRange))rawIndex=index;
        else continue;
      }
      const absoluteIndex=workStart+rawIndex;
      const point=rows[absoluteIndex];
      candidates.push({
        type,index:absoluteIndex,date:point.time,value:point.value,
        prominence:type==='high'?highProminence:lowProminence,
        requiredThreshold:localThreshold,localRadius,
        regimeConfidence:regime.confidence,regimeState:regime.state,regimeDirection:regime.direction
      });
    }

    const sorted=[...new Map(candidates.sort((a,b)=>a.index-b.index).map(item=>[`${item.type}:${item.index}`,item])).values()];
    let filtered=[];
    for(const item of sorted){
      const last=filtered.at(-1);
      if(!last){filtered.push(item);continue;}
      if(last.type===item.type){
        filtered[filtered.length-1]=sameTypeRepresentative(last,item);
        continue;
      }
      const move=Math.abs(item.value-last.value),gap=item.index-last.index;
      const pairThreshold=(last.requiredThreshold+item.requiredThreshold)/2;
      const pairRadius=(last.localRadius+item.localRadius)/2;
      const choppyPair=last.regimeState==='choppy'||item.regimeState==='choppy';
      if(move<pairThreshold*(choppyPair?.82:1))continue;
      if(gap<pairRadius*.62&&move<pairThreshold*(choppyPair?1.15:1.4))continue;
      filtered.push(item);
    }

    filtered=simplifyStructure(filtered);
    filtered=filtered.filter(item=>item.index>=visibleStart&&item.index<=visibleEnd);

    const visibleRegimes=regimes.slice(localVisibleStart,localVisibleEnd+1);
    const averageRegimeConfidence=visibleRegimes.length?visibleRegimes.reduce((sum,item)=>sum+item.confidence,0)/visibleRegimes.length:0;
    const regimeCounts=visibleRegimes.reduce((acc,item)=>{acc[item.state]=(acc[item.state]||0)+1;return acc;},{trend:0,sideways:0,choppy:0});

    return Object.freeze({
      pivots:Object.freeze(filtered.map(item=>Object.freeze({...item}))),
      threshold,radius,smoothingWidth,robustRange,noise,averageRegimeConfidence,
      regimeCounts:Object.freeze(regimeCounts),
      startIndex:visibleStart,endIndex:visibleEnd,
      startDate:rows[visibleStart]?.time||null,endDate:rows[visibleEnd]?.time||null,
    });
  }

  window.PivotLabEngine=Object.freeze({detect});
})();
