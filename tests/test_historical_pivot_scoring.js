const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const test=require('node:test');

const source=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-pivot-scoring.js'),'utf8');
const context={window:{}};
vm.runInNewContext(source,context);
const scoring=context.window.MacroWatchHistoricalPivotScoring;
const storedScoring=import('../supabase/functions/_shared/pivot/historical-score.mjs');
const pivot=(pivotDate,markerStatus='confirmed')=>({pivotDate,markerStatus});

test('timeliness awards the approved magenta and halved dark-gray boundaries',()=>{
  const reference='2022-10-01';
  assert.equal(scoring.timelinessScore(reference,pivot('2022-07-01')),100);
  assert.equal(scoring.timelinessScore(reference,pivot('2022-11-01')),50);
  assert.equal(scoring.timelinessScore(reference,pivot('2022-04-01','near_miss')),25);
  assert.equal(scoring.timelinessScore(reference,pivot('2022-06-30','near_miss')),49);
  assert.equal(scoring.timelinessScore(reference,pivot('2022-11-02','near_miss')),24);
  assert.equal(scoring.timelinessScore(reference,pivot('2022-12-01','near_miss')),12);
  assert.equal(scoring.timelinessScore(reference,null),0);
});

test('continuity stops at the first intermediate pivot and halves a dark-gray start',()=>{
  const magenta=pivot('2022-01-01'),dark=pivot('2022-01-01','near_miss');
  const intermediate=pivot('2022-02-15','reference_only');
  assert.equal(scoring.continuityScore(magenta,intermediate,'2022-04-01',90),50);
  assert.equal(scoring.continuityScore(dark,intermediate,'2022-04-01',90),25);
  assert.equal(scoring.continuityScore(magenta,null,'2022-04-01',90),100);
  assert.equal(scoring.continuityScore(null,null,'2022-04-01',90),0);
  assert.equal(scoring.continuityScore(pivot('2021-12-15'),intermediate,'2022-04-01',90,'2022-01-01'),50);
});

test('the magenta reference wins, otherwise the highest-timeliness dark pivot wins',()=>{
  const reference='2022-10-01';
  const early=pivot('2022-04-01','near_miss'),near=pivot('2022-06-30','near_miss');
  const magenta={...pivot('2022-10-01'),selectedReferences:[{type:'START'}]};
  assert.equal(scoring.referencePivot([early,near],'START',reference).pivotDate,near.pivotDate);
  assert.equal(scoring.referencePivot([early,near,magenta],'START',reference).pivotDate,magenta.pivotDate);
});

test('only the first light-gray intermediate point ends continuity',()=>{
  const points=[pivot('2022-02-12','near_miss'),pivot('2022-03-01','reference_only'),pivot('2022-02-15','reference_only')];
  assert.equal(scoring.firstIntermediate(points,'2022-01-01','2022-04-01').pivotDate,'2022-02-15');
  assert.deepEqual(Array.from(scoring.intermediatePivots(points,'2022-01-01','2022-04-01'),point=>point.pivotDate),['2022-02-15','2022-03-01']);
});

test('relationship color factor uses only the starting pivot',()=>{
  const magenta=pivot('2022-01-01'),dark=pivot('2022-01-01','near_miss');
  assert.equal(scoring.endpointFactor(magenta,magenta,'START'),1);
  assert.equal(scoring.endpointFactor(magenta,dark,'START'),1);
  assert.equal(scoring.endpointFactor(magenta,null,'START'),1);
  assert.equal(scoring.endpointFactor(dark,dark,'PEAK'),.5);
  assert.equal(scoring.endpointFactor(dark,null,'START'),.5);
  assert.equal(scoring.endpointFactor(null,magenta,'START'),0);
  assert.equal(scoring.endpointFactor(null,null,'START'),0);
  assert.equal(scoring.endpointFactor(magenta,null,'TROUGH'),1);
  assert.equal(scoring.endpointFactor(dark,null,'TROUGH'),.5);
});

test('relationship measures straight pivot-to-pivot days, not raw micro-waves',()=>{
  const rows=[{time:'2022-01-01',value:0},{time:'2022-01-11',value:10},{time:'2022-01-21',value:0}];
  const at=(source,date)=>source.find(row=>row.time===date)?.value;
  const points=[pivot('2022-01-11','reference_only')];
  const input={pivots:points,fromDate:'2022-01-01',toDate:'2022-01-21',rows,valueAtDate:at,expectedDirection:1,factor:.75};
  assert.deepEqual({...scoring.relationshipScore(input)},{relationship:'unclear',score:0});
  assert.deepEqual({...scoring.relationshipScore({...input,manualRelationship:'positive'})},{relationship:'positive',score:37});
  assert.deepEqual({...scoring.relationshipScore({...input,manualRelationship:'inverse'})},{relationship:'inverse',score:37});
  assert.deepEqual({...scoring.relationshipScore({...input,manualRelationship:'unclear'})},{relationship:'unclear',score:0});
});

test('trough relationship uses the full 24-month benchmark even if the next pivot is early',()=>{
  const rows=[{time:'2022-01-01',value:0},{time:'2022-04-01',value:10}];
  const at=(source,date)=>source.find(row=>row.time===date)?.value;
  const result=scoring.relationshipScore({pivots:[],fromDate:'2022-01-01',toDate:'2022-04-01',benchmarkEndDate:'2024-01-01',rows,valueAtDate:at,expectedDirection:1,factor:1});
  assert.equal(result.relationship,'positive');
  assert.equal(result.score,12);
});

test('a first flat segment gives half its days to the next non-flat direction',()=>{
  const rows=[{time:'2022-01-01',value:5},{time:'2022-01-11',value:5},{time:'2022-01-21',value:10}];
  const at=(source,date)=>source.find(row=>row.time===date)?.value;
  const result=scoring.relationshipScore({pivots:[pivot('2022-01-11','reference_only')],fromDate:'2022-01-01',toDate:'2022-01-21',rows,valueAtDate:at,expectedDirection:1,factor:1});
  assert.equal(result.relationship,'positive');
  assert.equal(result.score,75);
});

test('a fully flat span gives half credit when its relationship was set manually',()=>{
  const rows=[{time:'2022-01-01',value:5},{time:'2022-02-01',value:5}];
  const at=(source,date)=>source.find(row=>row.time===date)?.value;
  const input={pivots:[],fromDate:'2022-01-01',toDate:'2022-02-01',rows,valueAtDate:at,expectedDirection:1,factor:1};
  assert.deepEqual({...scoring.relationshipScore({...input,manualRelationship:'inverse'})},{relationship:'inverse',score:50});
  assert.deepEqual({...scoring.relationshipScore({...input,manualRelationship:'positive'})},{relationship:'positive',score:50});
  assert.deepEqual({...scoring.relationshipScore(input)},{relationship:'unclear',score:0});
});

test('composite uses only the approved 4:3:3 weights and floors the result',()=>{
  assert.equal(scoring.compositeScore(100,100,100),100);
  assert.equal(scoring.compositeScore(58,17,67),48);
  assert.equal(scoring.compositeScore(0,0,0),0);
});

test('stored scores match the existing front-end formulas for all three references',async()=>{
  const {mergedPivots,scoreReferences,SCORE_VERSION}=await storedScoring;
  assert.equal(SCORE_VERSION,'historical-pivot-4-3-3-v3');
  const cycle={startDate:'2022-01-01',peakDate:'2022-02-01',troughDate:'2022-03-01'};
  const automatic=['2022-01-01','2022-02-01','2022-03-01'].map((date,index)=>({pivot_order:index,pivot_date:date,pivot_value:[0,10,5][index]}));
  const pivots=mergedPivots({automatic,manual:[],cycle,indexCode:'SP500'});
  const rows=[{observation_date:'2022-01-01',value:0},{observation_date:'2022-02-01',value:10},
    {observation_date:'2022-03-01',value:5},{observation_date:'2024-03-01',value:15}];
  const saved=scoreReferences({pivots,rows,cycle});
  for(const type of ['START','PEAK','TROUGH']){
    const referenceDate=cycle[`${type.toLowerCase()}Date`],point=pivots.find(pivot=>pivot.pivotDate===referenceDate);
    assert.equal(saved[type].timelinessScore,scoring.timelinessScore(referenceDate,point));
    assert.equal(saved[type].continuityScore,scoring.continuityScore(point,null,
      type==='START'?cycle.peakDate:type==='PEAK'?cycle.troughDate:'2024-03-01',
      scoring.days(referenceDate,type==='START'?cycle.peakDate:type==='PEAK'?cycle.troughDate:'2024-03-01'),referenceDate));
    assert.equal(saved[type].score,scoring.compositeScore(saved[type].timelinessScore,
      saved[type].relationshipSuitabilityScore,saved[type].continuityScore));
  }
  assert.deepEqual(pivots.map(point=>point.markerStatus),['confirmed','confirmed','confirmed']);
});

test('stored relationship scores ignore the end pivot and credit manually classified flat days',async()=>{
  const {mergedPivots,scoreReferences}=await storedScoring;
  const cycle={startDate:'2022-01-01',peakDate:'2022-02-01',troughDate:'2022-03-01'};
  const manual=[
    {source_date:'2022-01-01',pivot_date:'2022-01-01',pivot_value:5,
      relationship:'inverse',key_references:{SP500:'START'},is_deleted:false},
    {source_date:'2022-02-01',pivot_date:'2022-02-01',pivot_value:5,
      relationship:'inverse',key_references:{SP500:'PEAK'},is_deleted:false}
  ];
  const rows=[{observation_date:'2022-01-01',value:5},{observation_date:'2022-02-01',value:5},
    {observation_date:'2022-03-01',value:10}];
  const both=scoreReferences({pivots:mergedPivots({manual,cycle,indexCode:'SP500'}),rows,cycle});
  const onlyStart=scoreReferences({pivots:mergedPivots({manual:manual.slice(0,1),cycle,indexCode:'SP500'}),rows,cycle});
  const onlyPeak=scoreReferences({pivots:mergedPivots({manual:manual.slice(1),cycle,indexCode:'SP500'}),rows,cycle});
  assert.equal(both.START.relationshipSuitabilityScore,50);
  assert.equal(onlyStart.START.relationshipSuitabilityScore,50);
  assert.equal(onlyPeak.PEAK.relationshipSuitabilityScore,100);
  assert.equal(onlyPeak.TROUGH.relationshipSuitabilityScore,0);
});

test('stored score input keeps manual deletion and the surviving manual pivot separate',async()=>{
  const {mergedPivots}=await storedScoring;
  const cycle={startDate:'2022-01-01',peakDate:'2022-02-01',troughDate:'2022-03-01'};
  const automatic=[{pivot_order:0,pivot_date:'2022-01-01',pivot_value:1},
    {pivot_order:1,pivot_date:'2022-02-01',pivot_value:2}];
  const manual=[{source_date:'2022-01-01',is_deleted:true},
    {source_date:'2022-02-01',pivot_date:'2022-02-03',pivot_value:3,
      relationship:'positive',reason:'관리자 수정',key_references:{SP500:'PEAK'},is_deleted:false}];
  const merged=mergedPivots({automatic,manual,cycle,indexCode:'SP500'});
  assert.deepEqual(merged.map(point=>point.pivotDate),['2022-02-03']);
  assert.equal(merged[0].isManual,true);
  assert.ok(merged[0].selectedReferences.some(ref=>ref.type==='PEAK'));
});

test('one saved manual pivot remains the same date across three independently scored market cycles',async()=>{
  const {mergedPivots,scoreReferences}=await storedScoring;
  const manual=[{source_date:'2022-01-01',pivot_date:'2022-01-01',pivot_value:10,
    relationship:'positive',reason:'관리자 입력',key_references:{SP500:'START'},is_deleted:false}];
  const rows=[{observation_date:'2021-01-01',value:5},{observation_date:'2022-01-01',value:10},
    {observation_date:'2022-09-01',value:15},{observation_date:'2023-03-01',value:20},
    {observation_date:'2025-03-01',value:25}];
  const cycles=[
    {startDate:'2022-01-01',peakDate:'2022-09-01',troughDate:'2023-03-01'},
    {startDate:'2022-02-01',peakDate:'2022-09-01',troughDate:'2023-03-01'},
    {startDate:'2021-12-01',peakDate:'2022-09-01',troughDate:'2023-03-01'}
  ];
  const scores=cycles.map((cycle,index)=>scoreReferences({
    pivots:mergedPivots({automatic:[],manual,cycle,indexCode:['SP500','NASDAQ_COMPOSITE','KOSPI'][index]}),
    rows,cycle
  }).START);
  assert.deepEqual(scores.map(score=>score.pivotDate),['2022-01-01','2022-01-01','2022-01-01']);
  assert.equal(new Set(scores.map(score=>score.score)).size,3);
});
test('historical detail cards read the saved scores without calculating from pivot records',()=>{
  const controller=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
  const start=controller.indexOf('  function renderHistoricalPivotScores('),end=controller.indexOf('  function clearIndicatorSelection(',start);
  assert.ok(start>=0&&end>start);
  const cards=[];
  const root={hidden:false,replaceChildren(){cards.length=0;},append(node){if(node.className?.includes('historical-pivot-detail-grid'))cards.push(...node.children);}};
  const createElement=()=>({className:'',textContent:'',children:[],classList:{toggle(){}},append(...children){this.children.push(...children);}});
  const render=vm.runInNewContext(`${controller.slice(start,end)}\nrenderHistoricalPivotScores`,{
    $:()=>root,document:{createElement},referenceOrder:['START','PEAK','TROUGH'],
    indicatorValue:point=>String(point.pivotValue),appendDReviews:()=>{},relationshipLabel:relation=>relation
  });
  const score=(date,value)=>({pivotDate:date,pivotValue:value,offsetDays:0,relationship:'positive',
    timelinessScore:100,relationshipSuitabilityScore:100,continuityScore:100,score:100,pivotReason:'저장된 근거'});
  const byReference={START:score('2022-01-01',0),PEAK:score('2022-02-01',10),TROUGH:score('2022-03-01',5)};
  const points=[{pivotDate:'2022-01-01'},{pivotDate:'2022-02-01'},{pivotDate:'2022-03-01'}];
  const snapshot=JSON.stringify(points);
  const cycle={startDate:'2022-01-01',peakDate:'2022-02-01',troughDate:'2022-03-01'};
  render({storedPivots:points,byReference,meta:{}},{mode:'history',cycle});
  assert.equal(cards.length,3);
  for(const card of cards){
    assert.match(card.children[1].textContent,/변곡 시의성 100점 · 관계 적합성 100점 · 추세 지속성 100점/);
    assert.match(card.children[0].textContent,/positive · 종합 점수 100점/);
  }
  assert.equal(JSON.stringify(points),snapshot);
  render({storedPivots:points,byReference:{...byReference,PEAK:{referenceDate:cycle.peakDate,relationship:'unclear',score:0}},meta:{}},{mode:'history',cycle});
  assert.match(cards[1].children[0].textContent,/기준점 피봇 없음 · unclear/);
  assert.match(cards[1].children[1].textContent,/관계 적합성 0점/);
  assert.doesNotMatch(controller.slice(start,end),/MacroWatchHistoricalPivotScoring|relationshipScore\(/);
});

