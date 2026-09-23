const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const test=require('node:test');

const source=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-pivot-scoring.js'),'utf8');
const context={window:{}};
vm.runInNewContext(source,context);
const scoring=context.window.MacroWatchHistoricalPivotScoring;
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

test('endpoint color factors are per anchor and the trough uses its own starting pivot',()=>{
  const magenta=pivot('2022-01-01'),dark=pivot('2022-01-01','near_miss');
  assert.equal(scoring.endpointFactor(magenta,magenta,'START'),1);
  assert.equal(scoring.endpointFactor(magenta,dark,'START'),.75);
  assert.equal(scoring.endpointFactor(dark,dark,'PEAK'),.5);
  assert.equal(scoring.endpointFactor(dark,null,'START'),.25);
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

test('historical detail cards display three independent scores without changing pivot records',()=>{
  const controller=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
  const start=controller.indexOf('  function renderStoredPivotScores('),end=controller.indexOf('  function clearIndicatorSelection(',start);
  assert.ok(start>=0&&end>start);
  const cards=Array.from({length:3},()=>{
    const label={},body={};
    return {label,body,classList:{toggle(){}},querySelector(selector){return selector==='strong'?label:selector==='p'?body:null;},append(){}};
  });
  const root={querySelectorAll:()=>cards};
  const createElement=()=>({className:'',textContent:'',append(){}});
  const render=vm.runInNewContext(`${controller.slice(start,end)}\nrenderStoredPivotScores`,{
    window:{MacroWatchHistoricalPivotScoring:scoring},$ :()=>root,document:{createElement},referenceOrder:['START','PEAK','TROUGH'],
    rawValueAtDate:(rows,date)=>rows.find(row=>row.time===date)?.value??null,
    indicatorValue:point=>String(point.pivotValue),pivotReasonFor:()=>'',
    relationshipLabel:relation=>relation
  });
  const points=[
    {pivotDate:'2022-01-01',pivotValue:0,markerStatus:'confirmed',selectedReferences:[{type:'START'}]},
    {pivotDate:'2022-02-01',pivotValue:10,markerStatus:'confirmed',selectedReferences:[{type:'PEAK'}]},
    {pivotDate:'2022-03-01',pivotValue:5,markerStatus:'confirmed',selectedReferences:[{type:'TROUGH'}]}
  ];
  const snapshot=JSON.stringify(points);
  render({storedPivots:points,displayPivots:points,rows:[
    {time:'2022-01-01',value:0},{time:'2022-02-01',value:10},{time:'2022-03-01',value:5},{time:'2024-03-01',value:15}
  ],meta:{}},{mode:'history',cycle:{startDate:'2022-01-01',peakDate:'2022-02-01',troughDate:'2022-03-01'}});
  for(const card of cards){
    assert.match(card.body.textContent,/변곡 시의성 .*점 · 관계 적합성 100점 · 추세 지속성 100점/);
    assert.match(card.label.textContent,/positive/);
  }
  assert.equal(JSON.stringify(points),snapshot);
});
