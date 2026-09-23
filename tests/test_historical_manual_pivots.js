const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const test=require('node:test');

const controller=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
const start=controller.indexOf('  function classifyStoredPivots(');
const end=controller.indexOf('  function rawValueAtDate(',start);
const badgeStart=controller.indexOf('  const anchorSummary=');
const badgeEnd=controller.indexOf('  const pivotReasonFor=',badgeStart);
assert.ok(start>=0&&end>start&&badgeStart>end&&badgeEnd>badgeStart);
const {mergeManualPivots:merge,indicatorBadgeSummary:badges}=vm.runInNewContext(`${controller.slice(start,end)}\n${controller.slice(badgeStart,badgeEnd)}\n({mergeManualPivots,indicatorBadgeSummary})`,{
  referenceOrder:['START','PEAK','TROUGH'],
  indicatorAnalysis:{
    relevanceWindow:date=>date==='2022-11-19'?{from:'2022-08-19',to:'2022-12-19'}:{from:'2022-01-01',to:'2022-01-31'},
    nearMissWindow:date=>date==='2022-11-19'?{from:'2022-05-19',to:'2023-01-19'}:date==='2023-06-01'?{from:'2022-12-01',to:'2023-08-01'}:{from:'2021-09-01',to:'2022-02-28'}
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
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'near_miss');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-20').markerStatus,'confirmed');
});

test('an automatic key remains unchanged when no manual key claims its reference',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[]},{startDate:'2022-01-05'});
  assert.equal(result[0].markerStatus,'confirmed');
});

test('an administrator key-off demotes only that pivot and does not promote another candidate',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0),auto('2022-01-20',1)],manualPivots:[{
    sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,keySuppressed:true,isDeleted:false
  }]},{startDate:'2022-01-05'});
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'manual_standard');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-20').markerStatus,'near_miss');
  const summary=badges({storedPivots:[],manualPivots:[{
    sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,keySuppressed:true,isDeleted:false
  }]},{mode:'history',cycle:{startDate:'2022-01-05'}});
  assert.deepEqual(Array.from(summary.anchors),[]);
  assert.deepEqual(Array.from(summary.nearMisses),['START']);
});

test('the modal checks an automatically magenta pivot and unchecks an administrator-demoted pivot',()=>{
  const from=controller.indexOf('  function openManualPivotModal(');
  const to=controller.indexOf('  async function persistManualPivot(',from);
  assert.ok(from>=0&&to>from);
  const fields=Object.fromEntries(['historical-manual-pivot-title','historical-manual-pivot-date',
    'historical-manual-pivot-relationship','historical-manual-pivot-reason','historical-manual-pivot-comment',
    'historical-manual-pivot-is-key','historical-manual-pivot-reference',
    'historical-manual-pivot-delete','historical-manual-pivot-status','historical-manual-pivot-modal']
    .map(id=>[id,{value:'',checked:false,hidden:true,focus(){}}]));
  const item={meta:{code:'TEST',title:'테스트 지표'},storedPivots:[auto('2022-01-05',0)],manualPivots:[]};
  const scope={isAdmin:true,activeMode:'history',functionClient:{},activeIndicatorContext:{analyses:[item]},
    activeCase:{code:'case'},activeCode:'SP500',$:id=>fields[id],manualPivotContext:null,
    effectivePivots:()=>[{pivotDate:'2022-01-05',markerStatus:'confirmed',selectedReferences:[{type:'START'}]}]};
  const open=vm.runInNewContext(`${controller.slice(from,to)}\nopenManualPivotModal`,scope);
  open({code:'TEST',date:'2022-01-05'});
  assert.equal(fields['historical-manual-pivot-is-key'].checked,true);
  assert.equal(fields['historical-manual-pivot-reference'].value,'START');
  scope.effectivePivots=()=>[{pivotDate:'2022-01-05',markerStatus:'manual_standard',selectedReferences:[]}];
  item.manualPivots=[{sourceDate:'2022-01-05',pivotDate:'2022-01-05',keyReference:null,keySuppressed:true,isDeleted:false}];
  open({code:'TEST',date:'2022-01-05'});
  assert.equal(fields['historical-manual-pivot-is-key'].checked,false);
  assert.equal(fields['historical-manual-pivot-reference'].value,'');
});

test('a date-only manual pivot sends optional metadata as null while keeping the measured value',async()=>{
  const html=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  const edge=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923100140_manual_pivot_optional_metadata.sql'),'utf8');
  assert.match(html,/id="historical-manual-pivot-date" type="date" required/);
  assert.doesNotMatch(html,/id="historical-manual-pivot-(?:relationship|reason)" required/);
  assert.match(edge,/relationship !== null && !\["positive", "inverse", "unclear"\]\.includes\(relationship\)/);
  assert.match(edge,/\(reason\?\.length \|\| 0\) > 250/);
  assert.match(migration,/check \(is_deleted or \(pivot_date is not null and pivot_value is not null\)\)/);
  assert.match(migration,/p_relationship is not null and p_relationship not in/);
  assert.match(migration,/next_keys=next_keys\|\|jsonb_build_object\(p_index_code,false\)/);
  const from=controller.indexOf('  async function persistManualPivot('),to=controller.indexOf('  const referenceOrder=',from);
  assert.ok(from>=0&&to>from);
  const fields=Object.fromEntries(['historical-manual-pivot-date','historical-manual-pivot-is-key',
    'historical-manual-pivot-reference','historical-manual-pivot-relationship','historical-manual-pivot-reason',
    'historical-manual-pivot-comment','historical-manual-pivot-status','historical-manual-pivot-save',
    'historical-manual-pivot-delete'].map(id=>[id,{value:'',checked:false,disabled:false,textContent:''}]));
  fields['historical-manual-pivot-date'].value='2022-01-05';
  let saved;
  const scope={isAdmin:true,manualPivotContext:{caseCode:'case',indexCode:'SP500',seriesCode:'TEST',
    sourceDate:'2022-01-05',item:{rows:[],manualPivots:[]}},functionClient:{invoke:async(_name,payload)=>{saved=payload;}},
    $:id=>fields[id],rawValueAtDate:()=>42,indicatorRepository:{clearManualPivots(){},clearScoreRows(){}},
    indexData:{indices:{SP500:'S&P 500'}},analysisCache:new Map(),rebuildHistoricalScores:async()=>{},
    closeManualPivotModal(){},activeCase:null,activeMode:'history'};
  const persist=vm.runInNewContext(`${controller.slice(from,to)}\npersistManualPivot`,scope);
  await persist(false);
  assert.equal(fields['historical-manual-pivot-status'].textContent,'저장 중');
  assert.equal(saved.pivot_date,'2022-01-05');
  assert.equal(saved.pivot_value,42);
  assert.equal(saved.relationship,null);
  assert.equal(saved.reason,null);
  assert.equal(saved.comment,null);
  assert.equal(saved.key_reference,null);
});

test('a non-key manual pivot uses the existing date windows for magenta, dark, or light gray',()=>{
  const manual=(date)=>({sourceDate:date,pivotDate:date,pivotValue:42,relationship:'inverse',reason:'관리자 선택',comment:'',keyReference:null,isDeleted:false});
  const result=merge({storedPivots:[],manualPivots:[manual('2022-01-05'),manual('2022-02-15'),manual('2022-03-01')]},{startDate:'2022-01-05'});
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'confirmed');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-02-15').markerStatus,'manual_standard');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-03-01').markerStatus,'reference_only');
});

test('a non-key manual pivot takes the core-window magenta before an automatic pivot',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[{
    sourceDate:'2022-01-20',pivotDate:'2022-01-20',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,isDeleted:false
  }]},{startDate:'2022-01-05'});
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-20').markerStatus,'confirmed');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'near_miss');
});

test('list badges use final pivot colors, including colored PEAK and gray START',()=>{
  const item={storedPivots:[],manualPivots:[
    {sourceDate:'2021-10-05',pivotDate:'2021-10-05',pivotValue:1,reason:'관리자 선택',keyReference:null,isDeleted:false},
    {sourceDate:'2022-11-20',pivotDate:'2022-11-20',pivotValue:2,reason:'관리자 선택',keyReference:'PEAK',isDeleted:false}
  ],byReference:{START:{pivotDate:'wrong-source'}},nearMissPivots:[{referenceType:'TROUGH',pivotDate:'wrong-source'}]};
  const summary=badges(item,{mode:'history',cycle:{startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'}});
  assert.deepEqual(Array.from(summary.anchors),['PEAK']);
  assert.deepEqual(Array.from(summary.nearMisses),['START']);
});

test('a dark pivot is still listed when another pivot is colored for the same reference',()=>{
  const item={storedPivots:[],manualPivots:[
    {sourceDate:'2021-10-05',pivotDate:'2021-10-05',pivotValue:1,reason:'관리자 선택',keyReference:null,isDeleted:false},
    {sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:2,reason:'관리자 선택',keyReference:'START',isDeleted:false}
  ]};
  const summary=badges(item,{mode:'history',cycle:{startDate:'2022-01-05'}});
  assert.deepEqual(Array.from(summary.anchors),['START']);
  assert.deepEqual(Array.from(summary.nearMisses),['START']);
});

test('the auxiliary pivot reason section is removed without deleting saved reasons',()=>{
  assert.doesNotMatch(controller,/기타 피봇 판정 근거|appendAuxiliaryPivotReasons/);
  assert.match(controller,/pivotReasonFor\(item,result\)/);
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

test('one case-indicator pivot set is shared while key designations stay index-specific',()=>{
  const repository=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-indicator-data.js'),'utf8');
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923090000_share_historical_indicator_pivots.sql'),'utf8');
  assert.match(controller,/loadStoredPivots\(activeCase\.code,activeCase\.primaryIndex,item\.code\)/);
  assert.match(controller,/catalog\(activeCode\)/);
  assert.match(controller,/loadManualPivots\(activeCase\.code,activeCode,item\.code\)/);
  assert.match(repository,/allowed\.has\(item\.marketScope\)/);
  assert.match(repository,/row\.key_references\?\.\[indexCode\]/);
  assert.match(repository,/\[\['case_code',caseCode\],\['series_code',seriesCode\]\]/);
  assert.match(migration,/historical_manual_source_shared_unique/);
  assert.match(migration,/m\.case_code=new\.case_code and m\.series_code=new\.series_code/);
  assert.doesNotMatch(migration,/m\.index_code=new\.index_code/);
  assert.match(migration,/key_references=jsonb_build_object\(index_code,key_reference\)/);
  assert.match(migration,/next_keys=coalesce\(prior\.key_references,'\{\}'::jsonb\)-p_index_code/);
  assert.match(migration,/delete from public\.historical_indicator_pivots a/);
  assert.doesNotMatch(migration,/delete from public\.historical_indicator_manual_pivots/);
});

test('reason presets keep symmetric directions without duration labels',()=>{
  const presets=controller.match(/const MANUAL_PIVOT_REASONS=Object\.freeze\(\[([\s\S]*?)\]\);/)?.[1];
  assert.ok(presets);
  assert.match(presets,/상승이 멈추고 고점권 횡보로 국면이 바뀌었습니다/);
  assert.match(presets,/하락이 멈추고 저점권 횡보로 국면이 바뀌었습니다/);
  assert.match(presets,/상승 흐름에서 급등한 뒤 방향을 되돌렸고, 이후 하락 흐름이 이어졌습니다/);
  assert.match(presets,/하락 흐름에서 급락한 뒤 방향을 되돌렸고, 이후 상승 흐름이 이어졌습니다/);
  assert.match(presets,/급등 후 추세적인 하락세로 전환됐습니다/);
  assert.match(presets,/급락 후 추세적인 상승세로 전환됐습니다/);
  assert.match(controller,/이전\/이후 추세가 불명확 합니다\./);
  assert.doesNotMatch(presets,/장기|오랫동안|장기간|중장기|막바지/);
});

test('administrator relationship supports unclear from form through API and database',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  const edge=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923090700_manual_pivot_unclear_relationship.sql'),'utf8');
  assert.match(html,/<option value="unclear">불명확<\/option>/);
  assert.match(edge,/\["positive", "inverse", "unclear"\]\.includes\(relationship\)/);
  assert.match(migration,/check \(relationship in \('positive','inverse','unclear'\)\)/);
  assert.match(migration,/p_relationship not in \('positive','inverse','unclear'\)/);
});

test('delete action remains hover-visible without mouse-focused rows sticking open',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../assets/css/historical-insight.css'),'utf8');
  assert.match(css,/\.historical-indicator-row:hover \.historical-indicator-actions/);
  assert.match(css,/\.historical-indicator-row:has\(:focus-visible\) \.historical-indicator-actions/);
  assert.doesNotMatch(css,/\.historical-indicator-row:focus-within \.historical-indicator-actions/);
});
