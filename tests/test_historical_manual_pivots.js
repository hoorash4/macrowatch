const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const test=require('node:test');

const controller=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
const start=controller.indexOf('  function classifyStoredPivots(');
const end=controller.indexOf('  function displayItem(',start);
assert.ok(start>=0&&end>start);
const merge=vm.runInNewContext(`${controller.slice(start,end)}\nmergeManualPivots`,{
  indicatorAnalysis:{
    relevanceWindow:()=>({from:'2022-01-01',to:'2022-01-31'}),
    nearMissWindow:()=>({from:'2022-01-01',to:'2022-01-31'})
  }
});
const auto=(date,order)=>({pivotDate:date,pivotOrder:order,pivotValue:order});

test('a manual pivot wins by date even when automatic order differs',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',3),auto('2022-01-20',0)],manualPivots:[{
    sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,isDeleted:false
  }]},{startDate:'2022-01-05'});
  assert.deepEqual(Array.from(result,pivot=>pivot.pivotDate),['2022-01-05','2022-01-20']);
  assert.equal(result[0].isManual,true);
  assert.equal(result[0].pivotValue,42);
  assert.equal(result[1].isManual,undefined);
});

test('manual deletion suppresses the same automatic date without showing a replacement',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0),auto('2022-01-20',1)],manualPivots:[{
    sourceDate:'2022-01-05',pivotDate:null,isDeleted:true
  }]},{startDate:'2022-01-05'});
  assert.deepEqual(Array.from(result,pivot=>pivot.pivotDate),['2022-01-20']);
});

test('manual key stays magenta and the displaced automatic key becomes gray',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[{
    sourceDate:'2022-01-20',pivotDate:'2022-01-20',pivotValue:50,relationship:'positive',
    reason:'관리자 선택',comment:'',keyReference:'START',isDeleted:false
  }]},{startDate:'2022-01-05'});
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'overridden_key');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-20').markerStatus,'confirmed');
});

test('an automatic key remains unchanged when no manual key claims its reference',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[]},{startDate:'2022-01-05'});
  assert.equal(result[0].markerStatus,'confirmed');
});

test('only explicit case deletion may physically delete manual rows',()=>{
  const sql=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923070100_historical_manual_pivots.sql'),'utf8');
  assert.match(sql,/before delete on public\.historical_indicator_manual_pivots/);
  assert.match(sql,/before truncate on public\.historical_indicator_manual_pivots/);
  assert.match(sql,/current_setting\('macrowatch\.explicit_manual_pivot_delete'/);
  assert.match(sql,/where case_code=p_case_code;/);
  assert.match(sql,/before insert on public\.historical_indicator_pivots/);
  assert.match(sql,/m\.source_date=new\.pivot_date or m\.pivot_date=new\.pivot_date/);
  assert.match(sql,/source_automatic_pivots jsonb not null/);
  assert.match(sql,/lock table public\.historical_indicator_pivots in share row exclusive mode/);
  assert.match(sql,/delete from public\.historical_indicator_pivots/);
  assert.match(sql,/if p_delete_manual_pivots then/);
  assert.doesNotMatch(sql,/case_code text not null references public\.historical_cases/);
});
