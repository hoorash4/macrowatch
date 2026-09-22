const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const test=require('node:test');
const vm=require('node:vm');
const ROOT=path.resolve(__dirname,'..');
const read=file=>fs.readFileSync(path.join(ROOT,file),'utf8');
function load(file,name,window={}){vm.runInNewContext(read(file),{window});return window[name];}
const analysis=()=>load('assets/js/historical-insight/historical-indicator-analysis.js','MacroWatchHistoricalIndicatorAnalysis');
const month=(index,value)=>{const d=new Date(Date.UTC(2000,index,1));return{time:d.toISOString().slice(0,10),value};};
const segments=(defs)=>{let n=0,value=100,out=[];for(const [months,delta] of defs){for(let i=0;i<months;i++){out.push(month(n++,value));value+=delta;}}return out;};
const dailySegments=(defs)=>{let n=0,value=100,out=[];for(const [days,delta] of defs){for(let i=0;i<days;i++){const d=new Date(Date.UTC(2000,0,1+n++));out.push({time:d.toISOString().slice(0,10),value});value+=delta;}}return out;};

test('regime, relevance, scale, and scoring policies are centralized without obsolete rules',()=>{
  const a=analysis(),source=read('assets/js/historical-insight/historical-indicator-analysis.js');
  assert.deepEqual({...a.ANALYSIS_POLICY.minimumRegimeDays},{short:31,medium:61,long:92});
  assert.deepEqual({...a.ANALYSIS_POLICY.relevanceMonths},{before:3,after:1});
  assert.deepEqual({...a.ANALYSIS_POLICY.nearMiss},{enabled:true,before:6,after:2});
  assert.deepEqual({...a.ANALYSIS_POLICY.correction},{maximumRetracementFraction:.5,maximumVolatilityUnits:3});
  assert.deepEqual({...a.ANALYSIS_POLICY.resumption},{minimumDurationRatio:2,minimumMoveRatio:1.5});
  assert.deepEqual({...a.ANALYSIS_POLICY.transient},{maximumDurationShare:.25,minimumProminenceUnits:1});
  assert.deepEqual({...a.ANALYSIS_POLICY.referenceWeights},{structural:.45,timing:.35,duration:.2});
  assert.deepEqual({...a.ANALYSIS_POLICY.pivotSelection},{structuralSimilarityPoints:5});
  assert.deepEqual({...a.ANALYSIS_POLICY.coverageBonusByCount},{1:0,2:12,3:25});
  assert.equal(a.ANALYSIS_POLICY.relationship.maximumBonus,10);
  assert.doesNotMatch(source,/shortBefore|longBefore|beforeMonths|relevanceDays/);
  assert.doesNotMatch(source,/candidateRange/);
  assert.doesNotMatch(source,/monthlySamples|stateStarts|extremeForBoundary|directionalChangeThreshold|\.025/);
  assert.doesNotMatch(source,/COVID|US2Y|RETAIL|2020-|2021-|2022-|코로나|소매/);
});

test('six-month rise, one-month correction, and a new high remain one rising regime',()=>{
  const a=analysis(),path=a.detectRetrospectiveRegimes(segments([[6,3],[1,-2],[4,3]]),{frequency:'M',minimumRegimeDays:92});
  assert.deepEqual(Array.from(path.regimes,x=>x.type),['rising']);assert.equal(path.pivots.length,0);assert.equal(path.pending,null);
});

test('six-month fall, one-month rebound, and a new low remain one falling regime',()=>{
  const a=analysis(),path=a.detectRetrospectiveRegimes(segments([[6,-3],[1,2],[4,-3]]),{frequency:'M',minimumRegimeDays:92});
  assert.deepEqual(Array.from(path.regimes,x=>x.type),['falling']);assert.equal(path.pivots.length,0);
});

test('a long shallow correction that makes a new high remains one retrospective rising regime',()=>{
  const a=analysis(),rows=segments([[8,5],[4,-1],[10,5]]),past=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:92}),online=a.detectOnlineState(rows,{frequency:'M',minimumRegimeDays:92});
  assert.deepEqual(Array.from(past.regimes,x=>x.type),['rising']);assert.equal(past.pivots.length,0);assert.equal(online.pivots.length,0);assert.ok(online.invalidations.some(item=>item.reason==='replaced_by_new_extreme'));
});

test('brief spikes and dips that recover into the original trend remain transient across frequencies',()=>{
  const a=analysis(),make=(count,step,spikeIndex,spike,days)=>Array.from({length:count},(_,index)=>{const date=new Date(Date.UTC(2000,0,1+index*days));return{time:date.toISOString().slice(0,10),value:100+index*step+(index===spikeIndex?spike:0)};}),cases=[['D',make(220,.15,100,35,1),31,'rising'],['W',make(60,.7,28,-30,7),61,'rising'],['M',make(30,2,14,40,31),92,'rising']];
  for(const [frequency,rows,minimumRegimeDays,expected] of cases){const path=a.detectRetrospectiveRegimes(rows,{frequency,minimumRegimeDays}),independent=a.retrospectiveExtremePivots(rows,{frequency,minimumRegimeDays});assert.deepEqual(Array.from(path.regimes,item=>item.type),[expected],frequency);assert.equal(path.pivots.length,0,frequency);assert.equal(independent.length,0,frequency);}
});

test('a brief isolated excursion that returns to a sideways range is not a structural regime',()=>{
  const a=analysis(),rows=Array.from({length:24},(_,index)=>month(index,100+(index===11?35:0))),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:61}),independent=a.retrospectiveExtremePivots(rows,{frequency:'M',minimumRegimeDays:61});
  assert.deepEqual(Array.from(path.regimes,item=>item.type),['sideways']);assert.equal(path.pivots.length,0);assert.equal(independent.length,0);
});

test('a volatility-scaled deep reversal remains an independent retrospective regime',()=>{
  const a=analysis(),path=a.detectRetrospectiveRegimes(segments([[8,5],[4,-10],[7,8]]),{frequency:'M',minimumRegimeDays:92});
  assert.ok(path.regimes.some(regime=>regime.type==='falling'));assert.ok(path.pivots.some(pivot=>pivot.previousRegime==='rising'&&pivot.nextRegime==='falling'));
});

test('a deep temporary reversal is internal when the original trend resumes much longer and stronger',()=>{
  const a=analysis(),options={frequency:'D',minimumRegimeDays:31},cases=[[[[120,1],[45,-2],[240,1.5]],'rising'],[[[120,-1],[45,2],[240,-1.5]],'falling']];
  for(const [segmentsDefinition,direction] of cases){const rows=dailySegments(segmentsDefinition),path=a.detectRetrospectiveRegimes(rows,options),major=a.majorStructuralPivots(rows,options,path),discovery=a.discoverReferenceCandidates(rows,rows[120].time,options);assert.deepEqual(Array.from(path.regimes,item=>item.type),[direction]);assert.equal(path.pivots.length,0);assert.equal(major.length,0);assert.equal(discovery.candidates.length,0);assert.equal(path.invalidations.filter(item=>item.structuralClassification==='internal_swing').length,2);}
});

test('a comparable reversal and resumption remain separate structural regimes',()=>{
  const a=analysis(),rows=dailySegments([[120,1],[90,-2],[120,1.5]]),options={frequency:'D',minimumRegimeDays:31},path=a.detectRetrospectiveRegimes(rows,options),major=a.majorStructuralPivots(rows,options,path);
  assert.deepEqual(Array.from(path.regimes,item=>item.type),['rising','falling','rising']);assert.equal(path.pivots.length,2);assert.equal(major.length,2);
});

test('a short but deep move becomes a new regime once the minimum duration is met',()=>{
  const a=analysis(),rows=segments([[6,5],[2,-15],[3,-5]]),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:31}),pivot=path.pivots.find(item=>item.previousRegime==='rising'&&item.nextRegime==='falling');
  assert.ok(pivot);assert.equal(pivot.pivotValue,Math.max(...rows.map(row=>row.value)));assert.ok(pivot.durationAfter>=31);assert.equal(pivot.regimeBoundaryDate,pivot.pivotDate);assert.notEqual(pivot.confirmationDate,pivot.pivotDate);
});

test('direct falling-to-rising reversal preserves the raw trough rather than a transition boundary',()=>{
  const a=analysis(),rows=segments([[6,-5],[2,15],[3,5]]),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:31}),pivot=path.pivots.find(item=>item.previousRegime==='falling'&&item.nextRegime==='rising');
  assert.ok(pivot);assert.equal(pivot.pivotValue,Math.min(...rows.map(row=>row.value)));assert.equal(pivot.regimeBoundaryDate,pivot.pivotDate);assert.equal(pivot.breakoutOrBreakdownDate,null);assert.notEqual(pivot.confirmationDate,pivot.pivotDate);
});

test('sustained boxes end directional regimes at their raw extremes',()=>{
  const a=analysis(),risingRows=segments([[6,3],[5,0]]),fallingRows=segments([[5,-3],[5,0]]),rising=a.detectRetrospectiveRegimes(risingRows,{frequency:'M',minimumRegimeDays:92}),falling=a.detectRetrospectiveRegimes(fallingRows,{frequency:'M',minimumRegimeDays:92});
  for(const path of [rising,falling]){assert.equal(path.pivots.length,1);assert.equal(path.pivots[0].nextRegime,'sideways');assert.equal(path.pivots[0].pivotType,'extreme');assert.equal(path.pivots[0].regimeBoundaryDate,path.pivots[0].pivotDate);assert.equal(path.pivots[0].breakoutOrBreakdownDate,null);assert.equal(path.pivots[0].confirmationEvidence.type,'structural_persistence');assert.notEqual(path.pivots[0].pivotDate,path.pivots[0].confirmationDate);}
  assert.equal(rising.pivots[0].previousRegime,'rising');assert.equal(falling.pivots[0].previousRegime,'falling');
  assert.equal(rising.pivots[0].pivotValue,Math.max(...risingRows.map(row=>row.value)));assert.equal(falling.pivots[0].pivotValue,Math.min(...fallingRows.map(row=>row.value)));
  const risingCandidate=rising.technicalCandidates.find(item=>item.previousRegime==='rising'&&item.nextRegime==='sideways');assert.equal(risingCandidate.pivotDate,rising.pivots[0].pivotDate);
});

test('sideways breakouts and breakdowns preserve their raw departure extremes before confirmation',()=>{
  const a=analysis(),risingRows=segments([[5,0],[6,5]]),fallingRows=segments([[5,0],[6,-5]]),rising=a.detectRetrospectiveRegimes(risingRows,{frequency:'M',minimumRegimeDays:61}).pivots.find(item=>item.previousRegime==='sideways'&&item.nextRegime==='rising'),falling=a.detectRetrospectiveRegimes(fallingRows,{frequency:'M',minimumRegimeDays:61}).pivots.find(item=>item.previousRegime==='sideways'&&item.nextRegime==='falling');
  for(const [pivot,rows,type] of [[rising,risingRows,'range_breakout'],[falling,fallingRows,'range_breakdown']]){assert.ok(pivot);assert.equal(pivot.pivotType,'departure');assert.equal(pivot.pivotDate,rows[5].time);assert.equal(pivot.regimeBoundaryDate,pivot.pivotDate);assert.ok(pivot.breakoutOrBreakdownDate>pivot.pivotDate);assert.equal(pivot.confirmationEvidence.type,type);assert.equal(pivot.confirmationEvidence.eventDate,pivot.breakoutOrBreakdownDate);}
});

test('smoothing detects structure without moving the final pivot away from the raw extreme',()=>{
  const a=analysis(),values=[100,105,110,140,120,100,80,60,50],rows=values.map((value,index)=>month(index,value)),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:31}),pivot=path.pivots.find(item=>item.nextRegime==='falling');
  assert.ok(pivot);assert.equal(pivot.pivotDate,rows[3].time);assert.equal(pivot.pivotValue,140);assert.ok(pivot.breakoutOrBreakdownDate>pivot.pivotDate);assert.equal(pivot.confirmationEvidence.eventDate,pivot.breakoutOrBreakdownDate);
});

test('market trend duration selects one, two, or three-month indicator regimes independently',()=>{
  const a=analysis();assert.equal(a.requiredMinimumRegimeDays(60),31);assert.equal(a.requiredMinimumRegimeDays(120),61);assert.equal(a.requiredMinimumRegimeDays(240),92);
});

test('every market anchor uses exactly three months before and one month after',()=>{
  const a=analysis(),expected={from:'2020-03-15',to:'2020-07-15',before:92,after:30};
  for(const type of a.REFERENCE_ORDER)assert.deepEqual({...a.relevanceWindow('2020-06-15')},expected,type);
  assert.deepEqual({...a.nearMissWindow('2020-06-15')},{from:'2019-12-15',to:'2020-08-15',before:183,after:61});
});

test('major pivots only in the expanded observation band remain visible as zero-score near misses',()=>{
  const a=analysis(),rows=segments([[6,-5],[6,5]]),item={searchStart:rows[0].time,searchEnd:rows.at(-1).time};
  for(const referenceDate of [rows[4].time,rows[10].time]){
    const cycle={startDate:referenceDate,peakDate:null,troughDate:null},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle,rows),pivot=result.nearMissPivots[0];
    assert.equal(result.results.length,0);assert.equal(result.meaningfulReferenceCount,0);assert.equal(result.referenceCoverageCount,0);assert.equal(result.overallScore,0);assert.equal(result.visible,true);
    assert.ok(pivot);assert.equal(pivot.referenceType,'START');assert.equal(pivot.pivotDate,rows[6].time);assert.equal(pivot.score,0);assert.equal(pivot.timingScore,0);assert.equal(pivot.relationshipBonus,0);assert.equal(pivot.pivotRole,'near-miss');assert.equal(pivot.markerStatus,'near_miss');
    const official=a.relevanceWindow(referenceDate),expanded=a.nearMissWindow(referenceDate);assert.ok(pivot.pivotDate>=expanded.from&&pivot.pivotDate<=expanded.to);assert.ok(pivot.pivotDate<official.from||pivot.pivotDate>official.to);
  }
});

test('official pivots are never duplicated as zero-score near misses',()=>{
  const a=analysis(),rows=segments([[6,-5],[6,5]]),referenceDate=rows[6].time,cycle={startDate:referenceDate,peakDate:null,troughDate:null},item={searchStart:rows[0].time,searchEnd:rows.at(-1).time},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle,rows);
  assert.equal(result.results.length,1);assert.equal(result.nearMissPivots.length,0);assert.ok(result.overallScore>0);assert.equal(result.meaningfulReferenceCount,1);
});

test('reference candidates are searched inside the anchor window and validated against the broad retrospective path',()=>{
  const a=analysis(),rows=segments([[10,-4],[10,5]]),referenceDate=rows[10].time,cycle={startDate:referenceDate,peakDate:rows.at(-1).time,troughDate:null};
  const result=a.resultForReference(rows,{frequency:'M'},'START',referenceDate,cycle),outside=a.resultForReference(rows,{frequency:'M'},'START',rows.at(-1).time,{...cycle,startDate:rows.at(-1).time});
  assert.ok(result.result);assert.equal(result.result.pivotRole,'market-relevant');assert.ok(result.result.confirmationDate>a.relevanceWindow(referenceDate).to);assert.equal(result.result.indicatorTrendDays,result.result.structuralPersistenceDays);assert.ok(result.result.structuralPersistenceEndDate>=result.result.pivotDate);
  assert.equal(outside.result,null);assert.ok(result.technicalPivots.length>0);
});

test('historical validation uses observations beyond the case search range',()=>{
  const a=analysis(),rows=segments([[10,-4],[10,5]]),cycle={startDate:rows[10].time,peakDate:rows.at(-1).time,troughDate:null},item={searchStart:rows[3].time,searchEnd:rows[11].time},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle);
  assert.ok(result.byReference.START);assert.ok(result.byReference.START.confirmationDate>item.searchEnd);
});

test('a provisional reversal that resumes the old trend is excluded before market relevance matching',()=>{
  const a=analysis(),rows=segments([[8,5],[4,-1],[10,5]]),referenceDate=rows[8].time,cycle={startDate:referenceDate,peakDate:rows.at(-1).time,troughDate:null},discovery=a.discoverReferenceCandidates(rows,referenceDate,{frequency:'M',minimumRegimeDays:92}),result=a.resultForReference(rows,{frequency:'M'},'START',referenceDate,cycle);
  assert.equal(discovery.candidates.length,0);assert.ok(discovery.path.technicalCandidates.length>0);assert.equal(result.result,null);
});

test('historical analysis separates technical pivots from market-relevant pivots and leaves unmatched anchors null',()=>{
  const a=analysis(),rows=segments([[6,-4],[6,5]]),cycle={startDate:rows[7].time,peakDate:rows.at(-1).time,troughDate:null},item={searchStart:rows[0].time,searchEnd:rows.at(-1).time},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle);
  assert.ok(result.technicalPivots.length>0);assert.ok(result.technicalPivots.every(pivot=>pivot.pivotRole==='technical'));assert.ok(result.marketRelevantPivots.every(pivot=>pivot.pivotRole==='market-relevant'));assert.equal(result.marketRelevantPivots.length,1);assert.equal(result.byReference.PEAK,null);assert.equal(result.byReference.TROUGH,null);
});

test('duration scoring caps at one hundred and weak pivot counts cannot overpower quality',()=>{
  const a=analysis();assert.equal(Math.round(a.durationScore(4,12)),33);assert.equal(Math.round(a.durationScore(10,12)),83);assert.equal(a.durationScore(12,12),100);assert.equal(a.durationScore(18,12),100);
  assert.ok(a.durationScore(243,365)>a.durationScore(365,1095));
  assert.ok(a.overallScore([{score:90}])>a.overallScore([{score:30},{score:30},{score:30}]));
});

test('structural persistence continues through sideways and same-direction resumption until an opposite structure',()=>{
  const a=analysis(),pivot={pivotDate:'2020-01-01',confirmationDate:'2020-02-01',previousRegime:'falling',nextRegime:'rising',durationAfter:152},pivots=[pivot,{pivotDate:'2020-06-01',previousRegime:'rising',nextRegime:'sideways'},{pivotDate:'2020-09-01',previousRegime:'sideways',nextRegime:'rising'},{pivotDate:'2021-01-01',previousRegime:'rising',nextRegime:'falling'}],result=a.structuralPersistence(pivot,pivots,'2021-06-01');
  assert.equal(result.endDate,'2021-01-01');assert.equal(result.terminatedBy,'falling');assert.equal(result.durationDays,366);
});

test('structural persistence keeps a preserved direction through a final sideways range',()=>{
  const a=analysis(),pivot={pivotDate:'2020-01-01',confirmationDate:'2020-02-01',previousRegime:'rising',nextRegime:'falling',durationAfter:152},pivots=[pivot,{pivotDate:'2020-06-01',previousRegime:'falling',nextRegime:'sideways'}],result=a.structuralPersistence(pivot,pivots,'2021-01-01');
  assert.equal(result.endDate,'2021-01-01');assert.equal(result.terminatedBy,null);assert.equal(result.durationDays,366);
});

test('trend strength and structural persistence remain separate score components',()=>{
  const a=analysis(),weakLong={pivotValue:100,nextRegimeValue:101,durationAfter:31,structuralPersistenceDays:365,requiredMinimumDays:92},strongShort={pivotValue:100,nextRegimeValue:120,durationAfter:31,structuralPersistenceDays:31,requiredMinimumDays:92};
  assert.ok(a.trendStrengthScore(strongShort,5)>a.trendStrengthScore(weakLong,5));assert.ok(a.structuralPersistenceScore(weakLong)>a.structuralPersistenceScore(strongShort));
});

test('two and three distinct aligned references receive strong coverage rewards',()=>{
  const a=analysis(),pivot=(referenceType,pivotDate,score,previousRegime,nextRegime)=>({referenceType,pivotDate,regimeBoundaryDate:pivotDate,pivotRole:'market-relevant',score,previousRegime,nextRegime,nativeRelationship:'positive',relationship:'positive',cycleRelationship:'positive',relationshipStatus:'aligned'}),start=pivot('START','2020-01-01',85,'falling','rising'),peak=pivot('PEAK','2020-06-01',85,'rising','falling'),trough=pivot('TROUGH','2020-12-01',75,'falling','rising'),single=pivot('START','2021-01-01',95,'falling','rising'),two=[start,peak],three=[{...start,score:75},{...peak,score:75},trough];
  const oneCoverage=a.referenceCoverage([single]),twoCoverage=a.referenceCoverage(two),threeCoverage=a.referenceCoverage(three);assert.deepEqual({count:oneCoverage.count,bonus:oneCoverage.bonus,references:Array.from(oneCoverage.references)},{count:1,bonus:0,references:['START']});assert.deepEqual({count:twoCoverage.count,bonus:twoCoverage.bonus,references:Array.from(twoCoverage.references)},{count:2,bonus:12,references:['START','PEAK']});assert.deepEqual({count:threeCoverage.count,bonus:threeCoverage.bonus,references:Array.from(threeCoverage.references)},{count:3,bonus:25,references:['START','PEAK','TROUGH']});assert.ok(a.overallScore(two)>a.overallScore([single]));assert.ok(a.overallScore(three)>a.overallScore([single]));
});

test('duplicate conflict unresolved and technical pivots do not inflate coverage',()=>{
  const a=analysis(),base={pivotRole:'market-relevant',score:90,nativeRelationship:'positive',relationship:'positive',cycleRelationship:'positive',relationshipStatus:'aligned'},start={...base,referenceType:'START',pivotDate:'2020-01-01',regimeBoundaryDate:'2020-01-01',previousRegime:'falling',nextRegime:'rising'},duplicatePeak={...base,referenceType:'PEAK',pivotDate:'2020-01-01',regimeBoundaryDate:'2020-01-01',previousRegime:'rising',nextRegime:'falling'},conflict={...base,referenceType:'TROUGH',pivotDate:'2020-12-01',regimeBoundaryDate:'2020-12-01',previousRegime:'falling',nextRegime:'rising',relationshipStatus:'conflict'},technical={...base,referenceType:'PEAK',pivotDate:'2020-06-01',regimeBoundaryDate:'2020-06-01',previousRegime:'rising',nextRegime:'falling',pivotRole:'technical'},unresolved={...conflict,relationshipStatus:'unresolved',cycleRelationship:'unresolved'};
  const coverage=a.referenceCoverage([start,duplicatePeak,conflict,technical,unresolved]);assert.equal(coverage.count,1);assert.equal(coverage.bonus,0);assert.deepEqual(Array.from(coverage.references),['START']);
});

test('current engine exposes watch, candidate, structural-only, and continuation invalidation',()=>{
  const a=analysis(),watchRows=segments([[6,3],[6,-1]]),candidateRows=segments([[6,3],[3,-20]]),resumedRows=segments([[6,3],[2,-4],[2,30]]),confirmedRows=segments([[6,-3],[4,15]]);
  const run=rows=>a.analyzeCurrent({code:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time);
  assert.equal(run(watchRows).evidence.status,'watch');assert.equal(run(candidateRows).evidence.status,'candidate');
  const resumed=run(resumedRows);assert.deepEqual(Array.from(resumed.regimes,x=>x.type),['rising']);assert.equal(resumed.evidence.status,'watching');assert.equal(resumed.evidence.contribution,0);assert.ok(resumed.evidence.invalidations.some(x=>x.reason==='replaced_by_new_extreme'));
  const confirmed=run(confirmedRows);assert.equal(confirmed.evidence.status,'structural_only');assert.equal(confirmed.evidence.signalState,'structural_only');assert.equal(confirmed.results.length,1);assert.equal(confirmed.results[0].pivotRole,'structural-only');assert.ok(confirmed.evidence.contribution<1);
});

test('elapsed time alone neither ends an online trend nor confirms a pivot',()=>{
  const a=analysis(),flatAfterHigh=segments([[6,3],[8,0]]),shallowCorrection=segments([[6,3],[8,-.5]]),flat=a.analyzeCurrent({code:'X',frequency:'M'},flatAfterHigh,flatAfterHigh[0].time,flatAfterHigh.at(-1).time),shallow=a.analyzeCurrent({code:'X',frequency:'M'},shallowCorrection,shallowCorrection[0].time,shallowCorrection.at(-1).time);
  assert.equal(flat.evidence.status,'watching');assert.equal(flat.pivots.length,0);assert.equal(shallow.evidence.status,'watch');assert.equal(shallow.pivots.length,0);
});

test('online sideways state requires a structural range breakout before trend confirmation',()=>{
  const a=analysis(),candidateRows=segments([[6,0],[3,20]]),confirmedRows=segments([[6,0],[5,10]]),candidate=a.analyzeCurrent({code:'X',frequency:'M'},candidateRows,candidateRows[0].time,candidateRows.at(-1).time),confirmed=a.analyzeCurrent({code:'X',frequency:'M'},confirmedRows,confirmedRows[0].time,confirmedRows.at(-1).time);
  assert.equal(candidate.evidence.status,'candidate');assert.equal(candidate.evidence.pending.previousRegime,'sideways');assert.ok(candidate.evidence.pending.candidateDate<candidate.evidence.pending.breakoutOrBreakdownDate);assert.equal(candidate.evidence.pending.confirmationEvidence.confirmedAt,null);assert.equal(confirmed.evidence.status,'structural_only');assert.equal(confirmed.evidence.pivot.previousRegime,'sideways');assert.equal(confirmed.evidence.pivot.nextRegime,'rising');assert.ok(confirmed.evidence.pivot.pivotDate<confirmed.evidence.pivot.breakoutOrBreakdownDate);assert.ok(confirmed.evidence.pivot.breakoutOrBreakdownDate<confirmed.evidence.pivot.confirmationDate);assert.equal(confirmed.evidence.pivot.confirmationEvidence.type,'range_breakout');
});

test('watch can retain a recent structural-only signal without treating it as market confirmed',()=>{
  const a=analysis(),rows=segments([[6,-5],[4,12],[3,-20]]),current=a.analyzeCurrent({code:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time),probability=a.currentPivotProbability([current]);
  assert.equal(current.evidence.status,'watch');assert.ok(current.evidence.retainedPivot);assert.equal(current.evidence.signalState,'structural_only');assert.ok(current.evidence.contribution>0&&current.evidence.contribution<1);assert.equal(probability.marketRelevantCount,0);assert.equal(probability.structuralOnlyCount,1);
});

test('a final range shorter than the requested duration does not become a retrospective regime',()=>{
  const a=analysis(),rows=[];for(let i=0;i<213;i++){const d=new Date(Date.UTC(2000,0,1+i));let value;if(i<31)value=1.8-i*.004;else if(i<145)value=1.56+(i%7)*.001;else value=Math.max(.2,1.56-(i-145)*.035);rows.push({time:d.toISOString().slice(0,10),value});}
  const path=a.detectRetrospectiveRegimes(rows,{frequency:'D',minimumRegimeDays:31});
  assert.deepEqual(Array.from(path.regimes,item=>item.type),['falling']);assert.equal(path.pivots.length,0);
});

test('a monotonic monthly rise stays one regime without intermediate pivots',()=>{
  const a=analysis(),rows=[];for(let i=0;i<20;i++)rows.push({time:new Date(Date.UTC(2000,i,1)).toISOString().slice(0,10),value:.15+i*.12});
  const path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:92});assert.equal(path.pivots.length,0);assert.deepEqual(Array.from(path.regimes,x=>x.type),['rising']);
});

test('an initial range breaks into a fall and the later trough uses the raw minimum',()=>{
  const a=analysis(),values=[100,100,100,96,92,88,84,80,76,82,88,94],rows=values.map((value,index)=>({time:new Date(Date.UTC(2000,index,1)).toISOString().slice(0,10),value})),path=a.detectRetrospectiveRegimes(rows,{frequency:'M',minimumRegimeDays:31});
  const breakdown=path.pivots.find(x=>x.previousRegime==='sideways'&&x.nextRegime==='falling'),trough=path.pivots.find(x=>x.previousRegime==='falling');assert.ok(breakdown);assert.equal(breakdown.pivotDate,rows[2].time);assert.ok(breakdown.breakoutOrBreakdownDate>breakdown.pivotDate);assert.ok(trough);assert.equal(trough.pivotValue,Math.min(...rows.map(row=>row.value)));assert.notEqual(trough.pivotDate,trough.confirmationDate);
});

test('reference timing rewards earlier leading pivots across the full relevance window',()=>{
  const a=analysis();assert.equal(a.timingScore(-92),100);assert.equal(Math.round(a.timingScore(0)),25);assert.equal(a.timingScore(31),0);assert.ok(a.timingScore(-60)>a.timingScore(-30));assert.ok(a.timingScore(-30)>a.timingScore(0));assert.ok(a.timingScore(0)>a.timingScore(15));const base=a.referenceScore(80,70,90);assert.equal(base,82.5);assert.ok(base+a.ANALYSIS_POLICY.relationship.maximumBonus<100);
});

test('structural direction decides relationship while correlation only controls confidence and bonus',()=>{
  const a=analysis(),market=Array.from({length:25},(_,index)=>month(index,100+index*index)),positive=market.map(row=>({...row})),inverse=market.map(row=>({...row,value:300-row.value})),flat=market.map(row=>({...row,value:10})),referenceDate=market[12].time;
  const positiveResult=a.relationshipForReference(positive,market,{frequency:'M'},'START',referenceDate,{previousRegime:'sideways',nextRegime:'rising'}),inverseResult=a.relationshipForReference(inverse,market,{frequency:'M'},'START',referenceDate,{previousRegime:'rising',nextRegime:'falling'}),weakResult=a.relationshipForReference(flat,market,{frequency:'M'},'START',referenceDate,{previousRegime:'falling',nextRegime:'rising'}),conflictingResult=a.relationshipForReference(positive,market,{frequency:'M'},'START',referenceDate,{previousRegime:'rising',nextRegime:'falling'}),unclearResult=a.relationshipForReference(positive,market,{frequency:'M'},'START',referenceDate,{previousRegime:'rising',nextRegime:'sideways'});
  assert.equal(positiveResult.relationship,'positive');assert.ok(positiveResult.bonus>0);assert.equal(inverseResult.relationship,'inverse');assert.ok(inverseResult.bonus>0);
  assert.equal(weakResult.relationship,'positive');assert.equal(weakResult.confidence,0);assert.equal(weakResult.bonus,0);
  assert.equal(conflictingResult.relationship,'inverse');assert.equal(conflictingResult.confidence,0);assert.equal(conflictingResult.bonus,0);
  assert.equal(unclearResult.relationship,'unclear');assert.equal(unclearResult.bonus,0);
});

test('missing correlation data does not downgrade a confirmed structural relationship',()=>{
  const a=analysis(),rows=[month(0,10),month(1,20)],result=a.relationshipForReference(rows,[],{frequency:'M'},'PEAK',rows[1].time,{confirmed:true,previousRegime:'falling',nextRegime:'rising'}),ambiguous=a.relationshipForReference(rows,rows,{frequency:'M'},'START',rows[1].time,{confirmed:true,previousRegime:'falling',nextRegime:'rising',structuralAmbiguity:true});
  assert.equal(result.relationship,'inverse');assert.equal(result.confidence,0);assert.equal(result.bonus,0);assert.equal(ambiguous.relationship,'unclear');
});

test('reference relationships compare transition roles instead of only the next direction',()=>{
  const a=analysis(),relation=(type,previousRegime,nextRegime)=>a.structuralRelationship(type,{previousRegime,nextRegime,confirmed:true});
  assert.equal(relation('START','falling','rising'),'positive');assert.equal(relation('START','sideways','rising'),'positive');assert.equal(relation('START','rising','falling'),'inverse');assert.equal(relation('START','sideways','falling'),'inverse');assert.equal(relation('START','rising','sideways'),'unclear');
  assert.equal(relation('PEAK','rising','falling'),'positive');assert.equal(relation('PEAK','sideways','falling'),'positive');assert.equal(relation('PEAK','falling','rising'),'inverse');
  assert.equal(relation('TROUGH','falling','rising'),'positive');assert.equal(relation('TROUGH','falling','sideways'),'positive');assert.equal(relation('TROUGH','rising','falling'),'inverse');assert.equal(relation('TROUGH','rising','sideways'),'inverse');
});

test('one cycle compares both hypotheses globally and records conflicts without changing pivots',()=>{
  const a=analysis(),result=(referenceType,pivotDate,relationship,baseScore=70,relationshipBonus=5)=>({referenceType,pivotDate,regimeBoundaryDate:pivotDate,relationship,relationshipConfidence:.8,relationshipBonus,baseScore,score:baseScore+relationshipBonus,pivotSelectionScore:baseScore,structuralScore:90,timingScore:80}),start=result('START','2020-01-01','unclear'),peak=result('PEAK','2020-06-01','inverse',90),trough=result('TROUGH','2020-12-01','positive',50),normalized=a.applyCycleRelationship({START:start,PEAK:peak,TROUGH:trough});
  assert.equal(normalized.cycleRelationship,'inverse');assert.deepEqual(Array.from(normalized.results,item=>item.relationship),['inverse','inverse','inverse']);assert.deepEqual(Array.from(normalized.results,item=>item.pivotDate),[start.pivotDate,peak.pivotDate,trough.pivotDate]);
  assert.equal(normalized.byReference.START.relationshipStatus,'unresolved_evidence');assert.equal(normalized.byReference.START.relationshipBonus,0);assert.equal(normalized.byReference.PEAK.relationshipStatus,'aligned');assert.equal(normalized.byReference.PEAK.relationshipBonus,5);assert.equal(normalized.byReference.TROUGH.relationshipStatus,'conflict');assert.equal(normalized.byReference.TROUGH.nativeRelationship,'positive');assert.equal(normalized.byReference.TROUGH.relationshipBonus,0);assert.equal(normalized.byReference.TROUGH.score,trough.baseScore);
});

test('cycle hypothesis selection is not greedy on START and stays unresolved when neither side dominates',()=>{
  const a=analysis(),result=(referenceType,pivotDate,relationship,baseScore)=>({referenceType,pivotDate,regimeBoundaryDate:pivotDate,relationship,relationshipConfidence:0,relationshipBonus:0,baseScore,score:baseScore,pivotSelectionScore:baseScore,structuralScore:90,timingScore:80}),positive=a.applyCycleRelationship({START:result('START','2020-01-01','inverse',50),PEAK:result('PEAK','2020-06-01','positive',70),TROUGH:result('TROUGH','2020-12-01','positive',60)}),unresolved=a.applyCycleRelationship({START:null,PEAK:result('PEAK','2020-06-01','inverse',70),TROUGH:result('TROUGH','2020-12-01','positive',65)});
  assert.equal(positive.cycleRelationship,'positive');assert.ok(positive.results.every(item=>item.relationship==='positive'));assert.equal(unresolved.cycleRelationship,'unresolved');assert.ok(unresolved.results.every(item=>item.relationship==='unclear'&&item.score===item.baseScore));
});

test('an unresolved cycle keeps every structural pivot without relationship penalties',()=>{
  const a=analysis(),pivot=(type,date)=>({referenceType:type,pivotDate:date,regimeBoundaryDate:date,relationship:'unclear',relationshipConfidence:0,relationshipBonus:0,baseScore:65,score:65,structuralScore:90,timingScore:80}),normalized=a.applyCycleRelationship({START:null,PEAK:pivot('PEAK','2020-06-01'),TROUGH:pivot('TROUGH','2020-12-01')});
  assert.equal(normalized.cycleRelationship,'unresolved');assert.equal(normalized.results.length,2);assert.ok(normalized.results.every(item=>item.relationship==='unclear'&&item.score===item.baseScore));
});

test('pivot assignment is independent from relationship bonus',()=>{
  const a=analysis(),base={referenceType:'START',pivotDate:'2020-01-01',regimeBoundaryDate:'2020-01-01',baseScore:80,score:80,pivotSelectionScore:80,structuralScore:80,timingScore:80},bonus={...base,pivotDate:'2020-01-02',regimeBoundaryDate:'2020-01-02',baseScore:70,score:80,pivotSelectionScore:70,relationshipBonus:10},assigned=a.assignReferencePivots({START:{candidates:[bonus,base]}});
  assert.equal(assigned.START.pivotDate,base.pivotDate);
});

test('pivot assignment chooses structural certainty even when timing makes a weaker leading pivot score higher',()=>{
  const a=analysis(),leading={referenceType:'START',pivotDate:'2020-01-01',regimeBoundaryDate:'2020-01-01',pivotSelectionScore:58,structuralScore:60,durationScore:53,timingScore:100,baseScore:72},lagging={referenceType:'START',pivotDate:'2020-04-01',regimeBoundaryDate:'2020-04-01',pivotSelectionScore:94,structuralScore:95,durationScore:92,timingScore:5,baseScore:67},assigned=a.assignReferencePivots({START:{candidates:[leading,lagging]}});
  assert.ok(leading.timingScore>lagging.timingScore);assert.ok(leading.baseScore>lagging.baseScore);assert.equal(assigned.START.pivotDate,lagging.pivotDate);
});

test('cycle-level role fit is resolved globally before choosing among valid structural pivots',()=>{
  const a=analysis(),pivot=(referenceType,pivotDate,relationship,pivotSelectionScore)=>({referenceType,pivotDate,regimeBoundaryDate:pivotDate,relationship,relationshipConfidence:.8,relationshipBonus:5,pivotSelectionScore,structuralScore:pivotSelectionScore,timingScore:50,baseScore:70,score:75}),strongInverse=pivot('START','2020-01-01','inverse',95),weakPositive=pivot('START','2020-02-01','positive',70),peak=pivot('PEAK','2020-06-01','positive',90),trough=pivot('TROUGH','2020-12-01','positive',90),assigned=a.assignReferencePivots({START:{candidates:[weakPositive,strongInverse]},PEAK:{candidates:[peak]},TROUGH:{candidates:[trough]}}),normalized=a.applyCycleRelationship(assigned);
  assert.equal(assigned.START.pivotDate,weakPositive.pivotDate);assert.equal(normalized.cycleRelationship,'positive');assert.ok(normalized.results.every(item=>item.relationship==='positive'&&item.relationshipStatus==='aligned'));
});

test('proximity breaks ties only between structurally similar pivots and does not prefer leading dates',()=>{
  const a=analysis(),candidate=(pivotDate,offsetDays,pivotSelectionScore)=>({referenceType:'START',referenceDate:'2020-04-01',pivotDate,regimeBoundaryDate:pivotDate,relationship:'positive',offsetDays,pivotSelectionScore,structuralScore:pivotSelectionScore,timingScore:offsetDays<0?100:0,baseScore:50}),farLeading=candidate('2020-01-01',-91,90),nearLagging=candidate('2020-04-20',19,88),assigned=a.assignReferencePivots({START:{candidates:[farLeading,nearLagging]}});
  assert.equal(assigned.START.pivotDate,nearLagging.pivotDate);
});

test('a materially stronger structural pivot beats a closer candidate',()=>{
  const a=analysis(),candidate=(pivotDate,offsetDays,pivotSelectionScore)=>({referenceType:'PEAK',referenceDate:'2020-04-01',pivotDate,regimeBoundaryDate:pivotDate,relationship:'positive',offsetDays,pivotSelectionScore,structuralScore:pivotSelectionScore,timingScore:50,baseScore:50}),strongFar=candidate('2020-01-15',-77,94),weakerNear=candidate('2020-03-28',-4,84),assigned=a.assignReferencePivots({PEAK:{candidates:[weakerNear,strongFar]}});
  assert.equal(assigned.PEAK.pivotDate,strongFar.pivotDate);
});

test('START PEAK and TROUGH are evaluated independently and one indicator can retain all three',()=>{
  const a=analysis(),rows=segments([[6,-4],[6,5],[6,-5],[8,5]]),cycle={startDate:rows[6].time,peakDate:rows[12].time,troughDate:rows[18].time},item={searchStart:rows[0].time,searchEnd:rows.at(-1).time},result=a.analyzeHistorical({code:'X',title:'X',frequency:'M'},rows,item,cycle,rows);
  assert.deepEqual(Array.from(result.results,item=>item.referenceType),['START','PEAK','TROUGH']);assert.equal(new Set(result.results.map(item=>item.pivotDate)).size,3);assert.ok(result.results.every(item=>item.pivotDate>=a.relevanceWindow(item.referenceDate).from&&item.pivotDate<=a.relevanceWindow(item.referenceDate).to));
});

test('current analysis keeps confirmed market anchors retrospective while latest evidence uses online classifications',()=>{
  const a=analysis(),rows=segments([[6,-4],[6,5],[6,-1],[8,5]]),cycle={startDate:rows[6].time,peakDate:null,troughDate:null},item={searchStart:rows[0].time,searchEnd:null},result=a.analyzeCurrent({code:'X',title:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time,{item,cycle,marketRows:rows});
  assert.ok(result.byReference.START);assert.equal(result.byReference.START.pivotRole,'market-relevant');assert.equal(result.byReference.START.classification,'market_relevant_confirmed');assert.ok(['watching','watch','candidate','structural_only','market_relevant_confirmed'].includes(result.evidence.status));assert.ok(result.evidence.baseScore>=result.byReference.START.score);
});

test('a current structural signal stays provisional until a market anchor makes it relevant',()=>{
  const a=analysis(),rows=segments([[6,-3],[5,15]]),plain=a.analyzeCurrent({code:'X',title:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time),pivotDate=plain.evidence.pivot.pivotDate,cycle={startDate:pivotDate,peakDate:null,troughDate:null},item={searchStart:rows[0].time,searchEnd:null},anchored=a.analyzeCurrent({code:'X',title:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time,{item,cycle,marketRows:rows});
  assert.equal(plain.evidence.signalState,'structural_only');assert.equal(plain.confirmedReferences.length,0);assert.ok(anchored.byReference.START);assert.equal(anchored.byReference.START.classification,'market_relevant_confirmed');assert.equal(anchored.evidence.signalState,'market_relevant_confirmed');assert.equal(anchored.evidence.synergyEligible,false);
  assert.equal(anchored.evidence.score,anchored.evidence.baseScore);assert.equal(anchored.evidence.contribution,anchored.evidence.baseScore/100);
});

test('a structurally valid current pivot survives beyond six months without a market anchor',()=>{
  const a=analysis(),rows=segments([[6,-3],[20,15]]),result=a.analyzeCurrent({code:'X',title:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time);
  assert.ok(result.evidence.pivot);assert.ok((Date.parse(rows.at(-1).time)-Date.parse(result.evidence.pivot.confirmationDate))/86400000>183);assert.equal(result.evidence.signalState,'structural_only');assert.equal(result.confirmedReferences.length,0);
});

test('an anchor outside the relevance window leaves a valid current pivot structural-only',()=>{
  const a=analysis(),rows=segments([[6,-3],[20,15]]),plain=a.analyzeCurrent({code:'X',title:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time),cycle={startDate:rows.at(-1).time,peakDate:null,troughDate:null},item={searchStart:rows[0].time,searchEnd:null},anchored=a.analyzeCurrent({code:'X',title:'X',frequency:'M'},rows,rows[0].time,rows.at(-1).time,{item,cycle,marketRows:rows});
  assert.equal(plain.evidence.signalState,'structural_only');assert.equal(anchored.byReference.START,null);assert.equal(anchored.evidence.signalState,'structural_only');
});

test('each valid current candidate receives its own calendar-month synergy group',()=>{
  const a=analysis(),make=(code,date)=>({meta:{code,title:code},evidence:{signalState:'candidate',signalDate:date,synergyEligible:true,baseScore:30,score:30,contribution:.3,invalidations:[]}}),scored=a.applyCurrentSynergy([make('A','2024-01-15'),make('B','2024-02-10'),make('C','2024-03-05')]);
  assert.deepEqual(Array.from(scored[0].evidence.synergyGroup,x=>x.code),['B']);assert.deepEqual(Array.from(scored[1].evidence.synergyGroup,x=>x.code),['A','C']);assert.deepEqual(Array.from(scored[2].evidence.synergyGroup,x=>x.code),['B']);assert.ok(scored[1].evidence.synergyBonus>scored[0].evidence.synergyBonus);
});

test('candidate invalidation removes its own score and rolls back every peer synergy immediately',()=>{
  const a=analysis(),make=(code,date)=>({meta:{code,title:code},evidence:{signalState:'candidate',signalDate:date,synergyEligible:true,baseScore:30,score:30,contribution:.3,invalidations:[]}}),first=a.applyCurrentSynergy([make('A','2024-01-15'),make('B','2024-02-10')]),after=a.applyCurrentSynergy([make('A','2024-01-15'),{meta:{code:'B',title:'B'},evidence:{signalState:'watching',signalDate:null,synergyEligible:false,baseScore:0,score:0,contribution:0,invalidations:[{reason:'range_return'}]}}]);
  assert.ok(first[0].evidence.synergyBonus>0);assert.equal(after[0].evidence.synergyBonus,0);assert.equal(after[1].evidence.score,0);assert.ok(a.currentPivotProbability(after).probability<a.currentPivotProbability(first).probability);
});

test('a replaced candidate date builds a new synergy group from that date',()=>{
  const a=analysis(),make=(code,date)=>({meta:{code,title:code},evidence:{signalState:'candidate',signalDate:date,synergyEligible:true,baseScore:30,score:30,contribution:.3,invalidations:[]}}),before=a.applyCurrentSynergy([make('A','2024-01-15'),make('B','2024-02-10')]),after=a.applyCurrentSynergy([make('A','2024-05-15'),make('B','2024-02-10')]);
  assert.equal(before[0].evidence.synergyGroup.length,1);assert.equal(after[0].evidence.synergyGroup.length,0);assert.equal(after[1].evidence.synergyGroup.length,0);
});

test('confirmed and provisional evidence for one indicator are alternatives rather than duplicate contributions',()=>{
  const a=analysis(),confirmed={meta:{code:'A',title:'A'},confirmedReferences:[{score:80}],evidence:{signalState:'candidate',signalDate:'2024-01-15',synergyEligible:true,provisionalBaseScore:30,confirmedBaseScore:80,baseScore:80,score:80,contribution:.8,invalidations:[]}},peer={meta:{code:'B',title:'B'},confirmedReferences:[],evidence:{signalState:'candidate',signalDate:'2024-02-10',synergyEligible:true,provisionalBaseScore:30,confirmedBaseScore:0,baseScore:30,score:30,contribution:.3,invalidations:[]}},scored=a.applyCurrentSynergy([confirmed,peer]),summary=a.currentPivotProbability(scored);
  assert.equal(scored[0].evidence.synergyBonus,6);assert.equal(scored[0].evidence.score,80);assert.equal(summary.marketRelevantCount,1);
});

test('a resumed trend removes the old candidate and a later reversal uses the replacement extreme',()=>{
  const a=analysis(),rows=segments([[6,3],[2,-10],[2,20],[4,-15]]),path=a.detectOnlineState(rows,{frequency:'M',minimumRegimeDays:92}),replacement=path.invalidations.find(item=>item.reason==='replaced_by_new_extreme'),pivot=path.pivots.at(-1);
  assert.ok(replacement);assert.ok(pivot);assert.equal(pivot.pivotDate,replacement.replacedBy);assert.equal(pivot.pivotValue,Math.max(...rows.map(row=>row.value)));assert.equal(pivot.structuralStatus,'structural_confirmed');
});

test('market-specific minimum duration is applied while segmenting the full retrospective structure',()=>{
  const a=analysis(),rows=dailySegments([[120,1],[45,-2],[60,2],[120,-2]]),short=a.detectRetrospectiveRegimes(rows,{frequency:'D',minimumRegimeDays:31}),long=a.detectRetrospectiveRegimes(rows,{frequency:'D',minimumRegimeDays:92});
  assert.ok(short.pivots.length>long.pivots.length);assert.ok(short.regimes.length>long.regimes.length);assert.ok(long.regimes.every(item=>item.requiredMinimumDays===92));
});

test('full-path retrospective validation recovers a clear high and low independently of sequential segmentation',()=>{
  const a=analysis(),highRows=dailySegments([[120,1],[120,-2]]),lowRows=dailySegments([[120,-1],[120,2]]),high=a.retrospectiveExtremePivots(highRows,{frequency:'D',minimumRegimeDays:92}),low=a.retrospectiveExtremePivots(lowRows,{frequency:'D',minimumRegimeDays:92}),highPivot=high.find(item=>item.previousRegime==='rising'&&item.nextRegime==='falling'),lowPivot=low.find(item=>item.previousRegime==='falling'&&item.nextRegime==='rising');
  assert.ok(highPivot);assert.equal(highPivot.pivotValue,Math.max(...highRows.map(row=>row.value)));assert.equal(highPivot.validationSource,'retrospective_full_path');
  assert.ok(lowPivot);assert.equal(lowPivot.pivotValue,Math.min(...lowRows.map(row=>row.value)));assert.equal(lowPivot.validationSource,'retrospective_full_path');
});

test('candidate validation does not require the clear extreme to exist in the sequential pivot list',()=>{
  const a=analysis(),rows=dailySegments([[120,1],[120,-2]]),candidate=a.retrospectiveExtremePivots(rows,{frequency:'D',minimumRegimeDays:92}).find(item=>item.previousRegime==='rising'),emptyPath={pivots:[],invalidations:[]},validated=a.validateRetrospectiveCandidate(candidate,emptyPath,92,rows,'D');
  assert.ok(candidate);assert.ok(validated);assert.equal(validated.pivotDate,candidate.pivotDate);assert.equal(validated.validationSource,'retrospective_full_path');
});

test('full-path discovery preserves a direct extreme when sequential state transitions split it through sideways',()=>{
  const a=analysis(),rows=dailySegments([[40,-2],[40,0],[40,2]]),referenceDate=rows[40].time,discovery=a.discoverReferenceCandidates(rows,referenceDate,{frequency:'D',minimumRegimeDays:31}),direct=discovery.candidates.find(item=>item.previousRegime==='falling'&&item.nextRegime==='rising');
  assert.ok(discovery.path.pivots.some(item=>item.nextRegime==='sideways'));assert.ok(discovery.path.pivots.some(item=>item.previousRegime==='sideways'));assert.ok(direct);assert.equal(direct.pivotValue,Math.min(...rows.map(row=>row.value)));assert.equal(direct.validationSource,'retrospective_full_path');assert.ok(discovery.majorPivots.includes(direct));
});

test('full-path extreme validation rejects a shallow correction when the original trend resumes',()=>{
  const a=analysis(),rows=dailySegments([[120,1],[100,-.2],[120,1]]),localHigh=rows[120].time,pivots=a.retrospectiveExtremePivots(rows,{frequency:'D',minimumRegimeDays:92});
  assert.equal(pivots.some(item=>item.pivotDate===localHigh&&item.previousRegime==='rising'),false);
});

test('an actual trend extreme outside the relevance window cannot be replaced by an earlier local extreme',()=>{
  const a=analysis(),rows=dailySegments([[120,1],[45,-2],[120,2],[120,-2]]),localPeak=rows[119].time,cycle={startDate:rows[0].time,peakDate:localPeak,troughDate:rows.at(-1).time},discovery=a.discoverReferenceCandidates(rows,localPeak,{frequency:'D',minimumRegimeDays:92}),result=a.resultForReference(rows,{frequency:'D'},'PEAK',localPeak,cycle),actualPeak=discovery.path.pivots.find(pivot=>pivot.previousRegime==='rising');
  assert.ok(actualPeak);assert.equal(actualPeak.pivotValue,Math.max(...rows.map(row=>row.value)));assert.ok(actualPeak.pivotDate>discovery.window.to);assert.equal(discovery.candidates.length,0);assert.equal(result.result,null);
});

test('a correction low inside the reference window cannot replace a major pivot after the rising trend resumes',()=>{
  const a=analysis(),rows=dailySegments([[40,2],[40,-.5],[40,2]]),referenceDate=rows[80].time,cycle={startDate:rows[0].time,peakDate:rows[49].time,troughDate:referenceDate},options={frequency:'D',minimumRegimeDays:31},path=a.detectRetrospectiveRegimes(rows,options),independent=a.retrospectiveExtremePivots(rows,options),discovery=a.discoverReferenceCandidates(rows,referenceDate,options),result=a.resultForReference(rows,{frequency:'D'},'TROUGH',referenceDate,cycle);
  const correction=independent.find(item=>item.pivotDate===referenceDate&&item.previousRegime==='falling'&&item.nextRegime==='rising');assert.ok(correction);assert.ok(path.invalidations.some(item=>item.candidateDate===referenceDate&&item.structuralClassification==='internal_swing'));assert.equal(a.validateRetrospectiveCandidate(correction,path,31,rows,'D'),null);assert.equal(discovery.majorPivots.length,0);assert.equal(discovery.candidates.length,0);assert.equal(result.result,null);assert.equal(result.technicalPivots.length,0);
});

test('a rebound high inside the reference window cannot replace a major pivot after the falling trend resumes',()=>{
  const a=analysis(),rows=dailySegments([[40,-2],[40,.5],[40,-2]]),referenceDate=rows[80].time,cycle={startDate:rows[0].time,peakDate:referenceDate,troughDate:rows[111].time},options={frequency:'D',minimumRegimeDays:31},path=a.detectRetrospectiveRegimes(rows,options),independent=a.retrospectiveExtremePivots(rows,options),discovery=a.discoverReferenceCandidates(rows,referenceDate,options),result=a.resultForReference(rows,{frequency:'D'},'PEAK',referenceDate,cycle);
  assert.ok(independent.some(item=>item.pivotDate===referenceDate&&item.previousRegime==='rising'&&item.nextRegime==='falling'));assert.ok(path.invalidations.some(item=>item.candidateDate===referenceDate&&item.structuralClassification==='internal_swing'));assert.equal(discovery.majorPivots.length,0);assert.equal(discovery.candidates.length,0);assert.equal(result.result,null);assert.equal(result.technicalPivots.length,0);
});

test('multiple structural pivots in one relevance window remain available for the final assignment stage',()=>{
  const a=analysis(),rows=dailySegments([[70,1],[45,-2],[45,2],[45,-2],[60,2]]),referenceDate=rows[135].time,cycle={startDate:rows[0].time,peakDate:referenceDate,troughDate:rows[195].time},discovery=a.discoverReferenceCandidates(rows,referenceDate,{frequency:'D',minimumRegimeDays:31}),result=a.resultForReference(rows,{frequency:'D'},'PEAK',referenceDate,cycle);
  assert.ok(discovery.candidates.length>1);assert.equal(result.result,null);assert.equal(result.candidates.length,discovery.candidates.length);
});

test('earlier market references own a selected pivot and later references cannot reuse it',()=>{
  const a=analysis(),candidate=(referenceType,pivotDate,score,regimeBoundaryDate=pivotDate)=>({referenceType,pivotDate,regimeBoundaryDate,score,structuralScore:score,timingScore:score}),sharedStart=candidate('START','2020-01-10',90),startAlternative=candidate('START','2019-12-01',50),sharedPeak=candidate('PEAK','2020-01-10',95),trough=candidate('TROUGH','2020-06-01',70),references={START:{candidates:[sharedStart,startAlternative]},PEAK:{candidates:[sharedPeak]},TROUGH:{candidates:[trough]}},reversed={TROUGH:references.TROUGH,PEAK:references.PEAK,START:references.START},assigned=a.assignReferencePivots(references),assignedReversed=a.assignReferencePivots(reversed);
  assert.equal(assigned.START.pivotDate,sharedStart.pivotDate);assert.equal(assigned.PEAK,null);assert.equal(assigned.TROUGH.pivotDate,trough.pivotDate);assert.deepEqual(JSON.parse(JSON.stringify(assignedReversed)),JSON.parse(JSON.stringify(assigned)));assert.equal(new Set(Object.values(assigned).filter(Boolean).map(item=>item.pivotDate)).size,2);
});

test('same structural boundary conflicts even when candidate pivot dates differ',()=>{
  const a=analysis(),start={referenceType:'START',pivotDate:'2020-01-01',regimeBoundaryDate:'2020-01-15',score:80,structuralScore:80,timingScore:80},peak={referenceType:'PEAK',pivotDate:'2020-01-05',regimeBoundaryDate:'2020-01-15',score:90,structuralScore:90,timingScore:90},assigned=a.assignReferencePivots({START:{candidates:[start]},PEAK:{candidates:[peak]}});
  assert.equal(assigned.START,start);assert.equal(assigned.PEAK,null);assert.equal(assigned.TROUGH,null);
});

test('retrospective and online state engines are physically separate and expose date diagnostics',()=>{
  const source=read('assets/js/historical-insight/historical-indicator-analysis.js'),a=analysis(),rows=segments([[6,5],[5,-10]]),past=a.detectRetrospectiveRegimes(rows,{frequency:'M'}),current=a.detectOnlineState(rows,{frequency:'M',minimumRegimeDays:92}),pivot=past.pivots.find(item=>item.previousRegime==='rising');
  assert.match(source,/function retrospectiveTrendPath/);assert.match(source,/function onlineTrendPath/);assert.doesNotMatch(source,/trendPath\([^)]*online/);assert.ok(pivot);assert.ok(pivot.regimeBoundaryDate);assert.ok(pivot.confirmationDate);assert.notEqual(pivot.pivotDate,pivot.confirmationDate);assert.equal(current.online,true);
});

test('low-scoring meaningful indicators remain visible and sort by score with deterministic ties',()=>{
  const a=analysis(),items=[
    {meta:{title:'나'},overallScore:10,meaningfulReferenceCount:1,maxReferenceScore:10,visible:true},
    {meta:{title:'가'},overallScore:80,meaningfulReferenceCount:1,maxReferenceScore:80,visible:true}
  ];assert.deepEqual(items.filter(x=>x.visible).sort(a.compareAnalyses).map(x=>x.meta.title),['가','나']);
});

test('display normalization stays separate from raw values',()=>{
  const normalized=analysis().normalizeForDisplay([month(0,10),month(1,20),month(2,30)],'2000-01-01','2000-03-01');
  assert.deepEqual(Array.from(normalized,row=>Math.round(row.value)),[0,50,100]);assert.deepEqual(Array.from(normalized,row=>row.rawValue),[10,20,30]);
});

test('indicator selection keeps exactly one selected series and can reset between cases',()=>{
  const api=load('assets/js/historical-insight/historical-indicator-selection.js','MacroWatchHistoricalIndicatorSelection'),state=api.create(),items=['A','B','C','D','E','F'].map(code=>({meta:{code}}));state.reconcile(items);
  assert.equal(state.select('A').selected,'A');assert.equal(state.select('F').selected,'F');assert.equal(state.clear().selected,null);
});

test('market scope metadata drives KOSPI and US catalog membership',()=>{
  const registry={allSeries:[{code:'US2Y',marketScope:'US'},{code:'US_POLICY_RATE_MID',title:'미국 기준금리 (약 6주 간격)',marketScope:'US'},{code:'KR3Y',marketScope:'KR'},{code:'KR_POLICY_RATE',title:'한국 기준금리 (1개월 간격)',marketScope:'KR'},{code:'KR_CORE_CPI',title:'Core CPI (식료품·에너지 제외)',marketScope:'KR'},{code:'WTI',marketScope:'GLOBAL'}]},window={MacroWatchEconomicSeriesRegistry:registry};
  const api=load('assets/js/historical-insight/historical-indicator-data.js','MacroWatchHistoricalIndicators',window),repo=api.createRepository({});
  assert.deepEqual(Array.from(repo.catalog('SP500'),x=>x.code),['US2Y','US_POLICY_RATE_MID','WTI']);assert.deepEqual(Array.from(repo.catalog('NASDAQ_COMPOSITE'),x=>x.code),['US2Y','US_POLICY_RATE_MID','WTI']);assert.deepEqual(Array.from(repo.catalog('KOSPI'),x=>x.code),['US2Y','US_POLICY_RATE_MID','KR3Y','KR_POLICY_RATE','KR_CORE_CPI','WTI']);
});

test('indicator data spans twenty-four months without changing chart viewport',()=>{
  const a=analysis();assert.deepEqual({...a.displayWindow({searchStart:'2018-01-01'},{startDate:'2020-03-31',troughDate:'2022-12-31'},'2026-09-15')},{from:'2018-03-31',to:'2024-12-31'});
  const controller=read('assets/js/historical-insight/historical-insight.js'),chart=read('assets/js/historical-insight/historical-index-chart.js');
  assert.doesNotMatch(controller,/checked\.length\)chart\?\.focus/);assert.match(chart,/getVisibleRange/);assert.match(chart,/setVisibleRange/);
});

test('UI uses one radio-selected magenta indicator without dimming other series',()=>{
  const html=read('historical-insight.html'),css=read('assets/css/historical-insight.css'),chart=read('assets/js/historical-insight/historical-index-chart.js'),controller=read('assets/js/historical-insight/historical-insight.js');
  assert.doesNotMatch(html,/historical-indicator-count|historical-indicator-selection-message|<details id="historical-indicator-accordion"/);assert.match(html,/id="historical-indicator-clear"/);assert.match(controller,/input\.type='radio'/);assert.match(controller,/input\.name='historical-indicator'/);assert.match(controller,/selection\.clear\(\).*input\[name="historical-indicator"\]/);assert.match(css,/grid-template-columns: 16px minmax\(0,1fr\) auto/);assert.match(css,/accent-color: var\(--historical-indicator-color\)/);assert.match(css,/\.historical-indicator-score \{[^}]*justify-self:end/);assert.match(css,/historical-reference-badge\[data-reference="START"\]/);assert.match(css,/historical-reference-badge\[data-reference="PEAK"\]/);assert.match(css,/historical-reference-badge\[data-reference="TROUGH"\]/);
  assert.match(controller,/selection\.select\(item\.meta\.code\);drawIndicators\(context\);focusCase\(\)/);
  assert.match(chart,/leftPriceScale: \{ visible: true/);assert.match(chart,/const indicatorColor='#c026d3'/);assert.doesNotMatch(chart,/rgba\(color|onIndicatorActivate/);assert.match(chart,/subscribeClick\(onChartClick\)/);assert.doesNotMatch(controller,/최대 5개|snapshot\.active|snapshot\.checked/);
  assert.match(css,/\.historical-indicator-result-grid strong \{[^}]*font-size: 14px/);assert.match(css,/\.historical-indicator-result-grid p \{[^}]*font-size: 13px/);assert.match(controller,/card\.classList\.toggle\('is-empty',!result\)/);
  for(const text of ['종합 점수','피봇 유효성','피봇 타이밍','추세 지속성','관계 신뢰도','관계 보너스'])assert.ok(controller.includes(text));
  assert.match(controller,/기준점 \$\{result\.referenceDate\}/);assert.match(controller,/피봇점 \$\{result\.pivotDate\}/);
  assert.match(controller,/unclear:'정\/역 관계 불명확'/);assert.match(controller,/positive:'정 관계'/);assert.match(controller,/inverse:'역 관계'/);
  assert.doesNotMatch(controller,/피봇 확인 \$\{result\.confirmationDate\}|관계 \$\{result\.relationship\}.*최종/);
  assert.match(controller,/CANDIDATE · 구조 피봇 후보/);assert.match(controller,/WATCH · 조정 감시/);assert.match(controller,/구조 품질/);assert.match(controller,/MARKET RELEVANT · 시장 기준점 관련 확정/);assert.match(chart,/item\.displayPivots\|\|item\.results/);
  assert.match(controller,/item\.nearMissPivots/);assert.match(controller,/analysis\.meaningfulReferenceCount>0/);
  assert.match(chart,/'near_miss','overridden_key','manual_standard'/);assert.match(chart,/--historical-near-miss-color/);assert.match(css,/\.historical-reference-badge\.is-near-miss/);
  assert.doesNotMatch(controller,/leading|coincident|lagging|trendConsistency|FILTER_THRESHOLDS/);
});

test('coverage view remains canonical, security-invoker, and read-only',()=>{
  const sql=read('supabase/migrations/20260915144500_add_economic_series_coverage.sql');assert.match(sql,/economic_chart_series_coverage\s*\nwith \(security_invoker = true\)/);assert.match(sql,/economic_chart_series_points/);assert.match(sql,/grant select[^;]*authenticated, service_role/);
});

test('current regime title setting remains administrator-update only',()=>{
  const sql=read('supabase/migrations/20260915151000_add_historical_current_settings.sql');assert.match(sql,/current_name text not null default '현재 국면 관찰 중'/);assert.match(sql,/Administrators update historical current settings/);assert.doesNotMatch(sql,/grant insert[^;]*authenticated/);
});
