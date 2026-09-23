(() => {
  'use strict';
  const DAY=86400000;
  const day=value=>Math.floor(Date.parse(`${value}T00:00:00Z`)/DAY);
  const days=(from,to)=>day(to)-day(from);
  const shiftMonths=(value,amount)=>{
    const [year,month,dayOfMonth]=value.split('-').map(Number);
    const result=new Date(Date.UTC(year,month-1+amount,1));
    const lastDay=new Date(Date.UTC(result.getUTCFullYear(),result.getUTCMonth()+1,0)).getUTCDate();
    result.setUTCDate(Math.min(dayOfMonth,lastDay));
    return result.toISOString().slice(0,10);
  };
  function timelinessScore(referenceDate,pivot){
    if(!referenceDate||!pivot)return 0;
    const date=pivot.pivotDate,coreStart=shiftMonths(referenceDate,-3),coreEnd=shiftMonths(referenceDate,1);
    const darkStart=shiftMonths(referenceDate,-6),darkEnd=shiftMonths(referenceDate,2);
    if(pivot.markerStatus==='confirmed'){
      if(date<coreStart||date>coreEnd)return 0;
      return Math.floor(100-50*days(coreStart,date)/days(coreStart,coreEnd));
    }
    if(!['near_miss','overridden_key','manual_standard'].includes(pivot.markerStatus))return 0;
    if(date>=darkStart&&date<=coreStart)return Math.floor((50+49*days(darkStart,date)/days(darkStart,coreStart))*.5);
    if(date>=coreEnd&&date<=darkEnd)return Math.floor((49-24*days(coreEnd,date)/days(coreEnd,darkEnd))*.5);
    return 0;
  }
  function referencePivot(pivots,referenceType,referenceDate){
    const source=[...(pivots||[])];
    const magenta=source.find(pivot=>pivot.markerStatus==='confirmed'&&pivot.selectedReferences?.some(ref=>ref.type===referenceType));
    if(magenta)return magenta;
    const dark=source.filter(pivot=>['near_miss','overridden_key','manual_standard'].includes(pivot.markerStatus)&&timelinessScore(referenceDate,pivot)>0);
    return dark.sort((left,right)=>timelinessScore(referenceDate,right)-timelinessScore(referenceDate,left))[0]||null;
  }
  function firstIntermediate(pivots,fromDate,toDate){
    return [...(pivots||[])].filter(pivot=>pivot.markerStatus==='reference_only'&&pivot.pivotDate>fromDate&&pivot.pivotDate<toDate)
      .sort((left,right)=>left.pivotDate.localeCompare(right.pivotDate))[0]||null;
  }
  function intermediatePivots(pivots,fromDate,toDate){
    return [...(pivots||[])].filter(pivot=>pivot.markerStatus==='reference_only'&&pivot.pivotDate>fromDate&&pivot.pivotDate<toDate)
      .sort((left,right)=>left.pivotDate.localeCompare(right.pivotDate));
  }
  function endpointFactor(startPivot,endPivot,type){
    return startPivot?.markerStatus==='confirmed'?1:startPivot?.markerStatus?0.5:0;
  }
  function relationshipScore({pivots,fromDate,toDate,benchmarkEndDate=toDate,rows,valueAtDate,expectedDirection,factor,manualRelationship}){
    const totalDays=days(fromDate,benchmarkEndDate);
    if(totalDays<=0||!factor||manualRelationship==='unclear')return {relationship:manualRelationship||'unclear',score:0};
    const points=[{pivotDate:fromDate},...intermediatePivots(pivots,fromDate,toDate),{pivotDate:toDate}];
    const segments=[];
    for(let index=1;index<points.length;index++){
      const left=points[index-1],right=points[index];
      const start=valueAtDate(rows,left.pivotDate),end=valueAtDate(rows,right.pivotDate);
      if(!Number.isFinite(start)||!Number.isFinite(end))return {relationship:'unclear',score:0};
      segments.push({direction:Math.sign(end-start),days:days(left.pivotDate,right.pivotDate)});
    }
    const totals={up:0,down:0};
    for(let index=0;index<segments.length;index++){
      const segment=segments[index];
      let direction=segment.direction;
      if(!direction){
        direction=segments.slice(0,index).reverse().find(previous=>previous.direction)?.direction||0;
        if(!direction)direction=segments.slice(index+1).find(next=>next.direction)?.direction||0;
      }
      if(!direction&&['positive','inverse'].includes(manualRelationship))
        direction=manualRelationship==='positive'?expectedDirection:-expectedDirection;
      if(direction>0)totals.up+=segment.days*(segment.direction?1:0.5);
      if(direction<0)totals.down+=segment.days*(segment.direction?1:0.5);
    }
    const matching=expectedDirection>0?totals.up:totals.down;
    const opposing=expectedDirection>0?totals.down:totals.up;
    const automatic=matching===opposing?'unclear':matching>opposing?'positive':'inverse';
    const relationship=manualRelationship||automatic;
    const aligned=relationship==='positive'?matching:relationship==='inverse'?opposing:0;
    return {relationship,score:Math.floor(100*Math.min(1,aligned/totalDays)*factor)};
  }
  function continuityScore(pivot,intermediate,endDate,benchmarkDays,startDate=pivot?.pivotDate){
    if(!pivot||!endDate||!Number.isFinite(benchmarkDays)||benchmarkDays<=0)return 0;
    const end=intermediate?.pivotDate||endDate;
    const activeDays=Math.max(0,days(startDate,end));
    const colorFactor=pivot.markerStatus==='confirmed'?1:.5;
    return Math.floor(100*Math.min(1,activeDays/benchmarkDays)*colorFactor);
  }
  const compositeScore=(timeliness,relationship,continuity)=>Math.floor(timeliness*.4+relationship*.3+continuity*.3);
  window.MacroWatchHistoricalPivotScoring=Object.freeze({days,shiftMonths,timelinessScore,referencePivot,firstIntermediate,intermediatePivots,endpointFactor,relationshipScore,continuityScore,compositeScore});
})();

