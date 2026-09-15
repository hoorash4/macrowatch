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

  function normalizeAlternation(items){
    const out=[];
    for(const item of items){
      const last=out.at(-1);
      if(!last||last.type!==item.type){out.push(item);continue;}
      const better=item.type==='high'?item.value>last.value:item.value<last.value;
      if(better)out[out.length-1]=item;
    }
    return out;
  }

  function simplify(items,threshold,minGap){
    let out=normalizeAlternation(items);
    let changed=true;
    while(changed&&out.length>=3){
      changed=false;
      for(let index=1;index<out.length-1;index++){
        const left=out[index-1],current=out[index],right=out[index+1];
        const leftMove=Math.abs(current.value-left.value),rightMove=Math.abs(right.value-current.value);
        const leftGap=current.index-left.index,rightGap=right.index-current.index;
        const weakMove=Math.min(leftMove,rightMove)<threshold;
        const tooTight=Math.min(leftGap,rightGap)<minGap&&Math.min(leftMove,rightMove)<threshold*1.6;
        if(!weakMove&&!tooTight)continue;
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
    if(count<5)return Object.freeze({pivots:Object.freeze([]),threshold:0,radius:0,smoothingWidth:1,startIndex:0,endIndex:Math.max(0,count-1)});

    const visibleStart=clamp(Math.floor(startIndex),0,count-1);
    const visibleEnd=clamp(Math.ceil(endIndex==null?count-1:endIndex),visibleStart,count-1);
    const visibleCount=Math.max(1,visibleEnd-visibleStart+1);
    const buffer=Math.max(3,Math.round(visibleCount*.22));
    const workStart=Math.max(0,visibleStart-buffer),workEnd=Math.min(count-1,visibleEnd+buffer);
    const work=rows.slice(workStart,workEnd+1);
    const localVisibleStart=visibleStart-workStart,localVisibleEnd=visibleEnd-workStart;

    const smoothingWidth=odd(clamp(Math.round(visibleCount*.018),1,31));
    const rawValues=work.map(row=>row.value);
    const smoothed=centeredMedian(rawValues,smoothingWidth);
    const visibleValues=smoothed.slice(localVisibleStart,localVisibleEnd+1);
    const q05=quantile(visibleValues,.05),q95=quantile(visibleValues,.95);
    const robustRange=Math.max(q95-q05,1e-12);
    const diffs=[];
    for(let index=localVisibleStart+1;index<=localVisibleEnd;index++)diffs.push(Math.abs(smoothed[index]-smoothed[index-1]));
    const noise=median(diffs);
    const threshold=Math.max(robustRange*.06,noise*3.5,1e-12);
    const radius=clamp(Math.round(visibleCount*.035),2,Math.max(2,Math.round(visibleCount*.12)));
    const rawRadius=Math.max(1,Math.floor(smoothingWidth/2));
    const candidates=[];

    for(let index=radius;index<work.length-radius;index++){
      const value=smoothed[index];
      const left=smoothed.slice(index-radius,index),right=smoothed.slice(index+1,index+radius+1);
      if(!left.length||!right.length)continue;
      const leftMax=Math.max(...left),rightMax=Math.max(...right),leftMin=Math.min(...left),rightMin=Math.min(...right);
      const high=value>=leftMax&&value>=rightMax;
      const low=value<=leftMin&&value<=rightMin;
      const highProminence=high?Math.min(value-leftMin,value-rightMin):0;
      const lowProminence=low?Math.min(leftMax-value,rightMax-value):0;
      let type=null;
      if(highProminence>=threshold*.8&&highProminence>=lowProminence)type='high';
      else if(lowProminence>=threshold*.8)type='low';
      if(!type)continue;
      const rawIndex=rawExtremeIndex(work,index,rawRadius,type);
      const absoluteIndex=workStart+rawIndex;
      const point=rows[absoluteIndex];
      candidates.push({type,index:absoluteIndex,date:point.time,value:point.value,prominence:type==='high'?highProminence:lowProminence});
    }

    const sorted=[...new Map(candidates.sort((a,b)=>a.index-b.index).map(item=>[`${item.type}:${item.index}`,item])).values()];
    let filtered=[];
    for(const item of sorted){
      const last=filtered.at(-1);
      if(!last){filtered.push(item);continue;}
      if(last.type===item.type){
        const better=item.type==='high'?item.value>last.value:item.value<last.value;
        if(better)filtered[filtered.length-1]=item;
        continue;
      }
      const move=Math.abs(item.value-last.value),gap=item.index-last.index;
      if(move<threshold)continue;
      if(gap<radius&&move<threshold*1.5)continue;
      filtered.push(item);
    }

    filtered=simplify(filtered,threshold,Math.max(2,Math.round(radius*.75)));
    filtered=filtered.filter(item=>item.index>=visibleStart&&item.index<=visibleEnd);

    return Object.freeze({
      pivots:Object.freeze(filtered.map(item=>Object.freeze({...item}))),
      threshold,radius,smoothingWidth,robustRange,noise,
      startIndex:visibleStart,endIndex:visibleEnd,
      startDate:rows[visibleStart]?.time||null,endDate:rows[visibleEnd]?.time||null,
    });
  }

  window.PivotLabEngine=Object.freeze({detect});
})();
