const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const test=require('node:test');

const controller=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
const start=controller.indexOf('  function manualPivotDirectionMatches(');
const end=controller.indexOf('  function rawValueAtDate(',start);
const rawEnd=controller.indexOf('  function closeManualPivotModal(',end);
const badgeStart=controller.indexOf('  const anchorSummary=');
const badgeEnd=controller.indexOf('  const pivotReasonFor=',badgeStart);
assert.ok(start>=0&&end>start&&badgeStart>end&&badgeEnd>badgeStart);
const {mergeManualPivots:merge,indicatorBadgeSummary:badges,manualPivotDirectionMatches:matchesDirection}=vm.runInNewContext(`${controller.slice(start,rawEnd)}\n${controller.slice(badgeStart,badgeEnd)}\n({mergeManualPivots,indicatorBadgeSummary,manualPivotDirectionMatches})`,{
  referenceOrder:['START','PEAK','TROUGH'],
  indicatorAnalysis:{
    relevanceWindow:date=>date==='2022-11-19'?{from:'2022-08-19',to:'2022-12-19'}:date==='2022-09-30'?{from:'2022-06-30',to:'2022-10-30'}:{from:'2022-01-01',to:'2022-01-31'},
    nearMissWindow:date=>date==='2022-11-19'?{from:'2022-05-19',to:'2023-01-19'}:date==='2022-09-30'?{from:'2022-03-30',to:'2022-11-30'}:date==='2023-06-01'?{from:'2022-12-01',to:'2023-08-01'}:{from:'2021-09-01',to:'2022-02-28'}
  }
});
const auto=(date,order)=>({pivotDate:date,pivotOrder:order,pivotValue:order});

test('saved inverse color compares only the first pivot-to-pivot direction',()=>{
  const cycle={startDate:'2022-01-01',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const manual={isManual:true,pivotDate:'2022-01-01',relationship:'inverse',keyReference:null};
  const pivots=[manual,{pivotDate:'2022-02-01'},{pivotDate:'2022-03-01'}];
  const rows=[{time:'2022-01-01',value:30},{time:'2022-02-01',value:20},
    {time:'2022-03-01',value:40},{time:'2022-07-01',value:50}];
  assert.equal(matchesDirection(manual,'START',pivots,rows,cycle),true);
});

test('the frontend keeps a leading inverse TROUGH pivot magenta through the last monthly buffer observation',()=>{
  const cycle={startDate:'2020-03-19',peakDate:'2021-07-06',troughDate:'2022-09-30'};
  const rows=[{time:'2022-07-01',value:6.3},{time:'2022-09-01',value:5.5},{time:'2024-09-01',value:1.6}];
  const indexRows=[{time:'2022-09-30',value:2155.49},{time:'2024-08-30',value:2674.31}];
  const item={rows,storedPivots:[],manualPivots:[{sourceDate:'2022-07-01',pivotDate:'2022-07-01',
    pivotValue:6.3,relationship:'inverse',reason:'관리자 근거',keyReference:null}]};
  const result=merge(item,cycle,indexRows);
  assert.equal(result[0].markerStatus,'confirmed');
  assert.equal(result[0].selectedReferences[0].type,'TROUGH');
});

test('saved relationship ends its first segment at the next reference when no pivot occurs before it',()=>{
  const cycle={startDate:'2022-01-01',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const manual={isManual:true,pivotDate:'2022-07-01',relationship:'positive',keyReference:null};
  const pivots=[manual,{pivotDate:'2023-02-01'}];
  const rows=[{time:'2022-07-01',value:30},{time:'2023-01-01',value:20},{time:'2023-02-01',value:40}];
  assert.equal(matchesDirection(manual,'PEAK',pivots,rows,cycle),true);
});

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

test('physically deleted automatic pivot is absent without a manual deletion marker',()=>{
  const result=merge({storedPivots:[auto('2022-01-20',1)],manualPivots:[]},{startDate:'2022-01-05'});
  assert.deepEqual(Array.from(result,pivot=>pivot.pivotDate),['2022-01-20']);
  const sql=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260924091300_remove_deleted_pivot_markers.sql'),'utf8');
  assert.match(sql,/delete from public\.historical_indicator_manual_pivots m/);
  assert.match(sql,/a\.pivot_date=target_date/);
  assert.match(sql,/target_date=p_source_date/);
  assert.doesNotMatch(sql,/a\.pivot_date in \(p_source_date,p_pivot_date\)/);
  assert.match(sql,/drop column is_deleted/);
});

test('manual key stays magenta and the displaced automatic key becomes gray',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[{
    sourceDate:'2022-01-20',pivotDate:'2022-01-20',pivotValue:50,relationship:'positive',
    reason:'관리자 선택',comment:'',keyReference:'START',isDeleted:false
  }]},{startDate:'2022-01-05'});
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'near_miss');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-20').markerStatus,'confirmed');
});

test('a manually selected TROUGH cannot keep an automatic PEAK on the same pivot',()=>{
  const cycle={peakDate:'2022-01-01',troughDate:'2022-01-20'};
  const item={rows:[],storedPivots:[],manualPivots:[{
    sourceDate:'2022-01-19',pivotDate:'2022-01-19',pivotValue:42,
    relationship:null,reason:'',comment:'',keyReference:'TROUGH'
  }]};
  const selected=merge(item,cycle)[0];
  assert.deepEqual(Array.from(selected.selectedReferences,ref=>ref.type),['TROUGH']);
  const summary=badges(item,{mode:'history',cycle,indexRows:[]});
  assert.deepEqual(Array.from(summary.anchors),['TROUGH']);
});

test('changing only the reference dropdown marks the choice as a manual decision',()=>{
  const from=controller.indexOf("  $('historical-manual-pivot-is-key').addEventListener('change'");
  const to=controller.indexOf("  $('historical-manual-pivot-close').addEventListener",from);
  assert.ok(from>=0&&to>from);
  const listeners={},reference={disabled:false,value:'TROUGH'};
  const scope={manualPivotContext:{keyTouched:false},$:id=>({
    ...reference,addEventListener:(_event,listener)=>{listeners[id]=listener;}
  })};
  vm.runInNewContext(controller.slice(from,to),scope);
  listeners['historical-manual-pivot-reference']();
  assert.equal(scope.manualPivotContext.keyTouched,true);
});

test('an automatic key remains unchanged when no manual key claims its reference',()=>{
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[]},{startDate:'2022-01-05'});
  assert.equal(result[0].markerStatus,'confirmed');
});

test('a saved inverse START only demotes that manual pivot when its next segment rises',()=>{
  const cycle={startDate:'2022-01-01',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const rows=[{time:'2022-01-01',value:10},{time:'2022-01-20',value:20},
    {time:'2022-07-01',value:30},{time:'2023-01-01',value:10}];
  const item={rows,storedPivots:[{pivotDate:'2022-01-20',pivotOrder:0,pivotValue:20}],manualPivots:[{
    sourceDate:'2022-01-01',pivotDate:'2022-01-01',pivotValue:10,relationship:'inverse',
    reason:'',comment:'',keyReference:null,isDeleted:false
  }]};
  const result=merge(item,cycle);
  assert.equal(result.find(pivot=>pivot.isManual).markerStatus,'reference_only');
  assert.equal(result.find(pivot=>!pivot.isManual).markerStatus,'confirmed');
  item.manualPivots[0].keyReference='START';
  assert.equal(merge(item,cycle).find(pivot=>pivot.isManual).markerStatus,'confirmed');
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
    manualReasonLoadError:'',
    autoPivotRows:item=>(item.pivots||[]).map(pivot=>({pivotDate:pivot.date,pivotValue:pivot.value})),
    effectivePivots:()=>[{pivotDate:'2022-01-05',markerStatus:'confirmed',selectedReferences:[{type:'START'}]}]};
  const open=vm.runInNewContext(`${controller.slice(from,to)}\nopenManualPivotModal`,scope);
  open({code:'TEST',date:'2022-01-05'});
  assert.equal(fields['historical-manual-pivot-is-key'].checked,true);
  assert.equal(fields['historical-manual-pivot-reference'].value,'START');
  assert.equal(scope.manualPivotContext.keyDecision,'auto');
  assert.equal(fields['historical-manual-pivot-delete'].hidden,false);
  item.storedPivots=[];
  item.pivots=[{date:'2022-01-05',value:42,grade:'A'}];
  open({code:'TEST',date:'2022-01-05'});
  assert.equal(fields['historical-manual-pivot-delete'].hidden,false);
  scope.effectivePivots=()=>[{pivotDate:'2022-01-05',markerStatus:'manual_standard',selectedReferences:[]}];
  item.manualPivots=[{sourceDate:'2022-01-05',pivotDate:'2022-01-05',keyReference:null,keySuppressed:true,isDeleted:false}];
  open({code:'TEST',date:'2022-01-05'});
  assert.equal(fields['historical-manual-pivot-is-key'].checked,false);
  assert.equal(fields['historical-manual-pivot-reference'].value,'');
  assert.equal(scope.manualPivotContext.keyDecision,'manual_off');
});

test('a moved manual pivot opens only at its current date, never at its former source date',()=>{
  const from=controller.indexOf('  function openManualPivotModal(');
  const to=controller.indexOf('  async function persistManualPivot(',from);
  const fields=Object.fromEntries(['historical-manual-pivot-title','historical-manual-pivot-date',
    'historical-manual-pivot-relationship','historical-manual-pivot-reason','historical-manual-pivot-comment',
    'historical-manual-pivot-is-key','historical-manual-pivot-reference',
    'historical-manual-pivot-delete','historical-manual-pivot-status','historical-manual-pivot-modal']
    .map(id=>[id,{value:'',checked:false,hidden:true,focus(){}}]));
  const item={meta:{code:'TEST',title:'기준금리'},storedPivots:[],manualPivots:[{
    sourceDate:'1998-11-18',pivotDate:'1999-05-18',pivotValue:4.75,keyReference:null
  }]};
  const scope={isAdmin:true,activeMode:'history',functionClient:{},activeIndicatorContext:{analyses:[item]},
    activeCase:{code:'case'},activeCode:'KOSPI',$:id=>fields[id],manualPivotContext:null,
    manualReasonLoadError:'',autoPivotRows:()=>[],effectivePivots:()=>[]};
  const open=vm.runInNewContext(`${controller.slice(from,to)}\nopenManualPivotModal`,scope);
  open({code:'TEST',date:'1998-11-18'});
  assert.equal(scope.manualPivotContext.existing,false);
  assert.equal(scope.manualPivotContext.sourceDate,'1998-11-18');
  assert.equal(fields['historical-manual-pivot-delete'].hidden,true);
  open({code:'TEST',date:'1999-05-18'});
  assert.equal(scope.manualPivotContext.existing,true);
  assert.equal(fields['historical-manual-pivot-delete'].hidden,false);
});

test('moving a manual pivot frees its former date without deleting the destination point',()=>{
  const sql=fs.readFileSync(path.join(__dirname,
    '../supabase/migrations/20260924062912_rekey_moved_historical_manual_pivots.sql'),'utf8');
  assert.match(sql,/set source_date=pivot_date\s+where source_date<>pivot_date/);
  assert.match(sql,/if prior\.source_date is not null and p_source_date<>p_pivot_date then[\s\S]*set source_date=p_pivot_date/);
  assert.match(sql,/values \(p_case_code,origin_index,p_series_code,p_pivot_date,p_pivot_date,p_pivot_value/);
  assert.match(sql,/target_date=p_source_date/);
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
  assert.equal(saved.key_reference,'AUTO');
});

test('new automatic decisions and existing administrator key-off remain distinct on save',async()=>{
  const from=controller.indexOf('  async function persistManualPivot('),to=controller.indexOf('  const referenceOrder=',from);
  const fields=Object.fromEntries(['historical-manual-pivot-date','historical-manual-pivot-is-key',
    'historical-manual-pivot-reference','historical-manual-pivot-relationship','historical-manual-pivot-reason',
    'historical-manual-pivot-comment','historical-manual-pivot-status','historical-manual-pivot-save',
    'historical-manual-pivot-delete'].map(id=>[id,{value:'',checked:false,disabled:false,textContent:''}]));
  fields['historical-manual-pivot-date'].value='2022-01-05';
  const sent=[];
  const scope={isAdmin:true,manualPivotContext:{caseCode:'case',indexCode:'SP500',seriesCode:'TEST',
    sourceDate:'2022-01-05',keyTouched:false,keyDecision:'auto',item:{rows:[],manualPivots:[]}},
    functionClient:{invoke:async(_name,payload)=>{sent.push(payload);}},$:id=>fields[id],rawValueAtDate:()=>42,
    indicatorRepository:{clearManualPivots(){},clearScoreRows(){}},indexData:{indices:{SP500:'S&P 500'}},
    analysisCache:new Map(),rebuildHistoricalScores:async()=>{},closeManualPivotModal(){},activeCase:null,activeMode:'history'};
  const persist=vm.runInNewContext(`${controller.slice(from,to)}\npersistManualPivot`,scope);
  fields['historical-manual-pivot-is-key'].checked=true;
  fields['historical-manual-pivot-reference'].value='START';
  await persist(false);
  assert.equal(sent.at(-1).key_reference,'AUTO');
  scope.manualPivotContext.keyTouched=true;
  await persist(false);
  assert.equal(sent.at(-1).key_reference,'START');
  fields['historical-manual-pivot-is-key'].checked=false;
  await persist(false);
  assert.equal(sent.at(-1).key_reference,'START_REF');
  fields['historical-manual-pivot-reference'].value='';
  await persist(false);
  assert.equal(sent.at(-1).key_reference,null);
  scope.manualPivotContext.keyTouched=false;
  scope.manualPivotContext.keyDecision='manual_off';
  await persist(false);
  assert.equal(sent.at(-1).key_reference,null);
});

test('new database save leaves AUTO undecided while preserving explicit off and other indices',()=>{
  const sql=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260924060500_manual_pivot_auto_key_decision.sql'),'utf8');
  assert.match(sql,/p_key_reference not in \('START','PEAK','TROUGH','AUTO'\)/);
  assert.match(sql,/next_keys=coalesce\(prior\.key_references,'\{\}'::jsonb\)-p_index_code/);
  assert.match(sql,/if p_key_reference in \('START','PEAK','TROUGH'\) then/);
  assert.match(sql,/elsif p_key_reference is null then\s+next_keys=next_keys\|\|jsonb_build_object\(p_index_code,false\)/);
  assert.match(sql,/where a\.case_code=p_case_code and a\.series_code=p_series_code/);
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

test('manual rows remain protected outside explicit administrator deletion',()=>{
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

test('deleting a pivot removes its manual row and automatic row without leaving a deletion marker',()=>{
  const sql=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923224100_remove_deleted_manual_pivot_markers.sql'),'utf8');
  const deletion=sql.slice(sql.indexOf('  if p_is_deleted then'),sql.indexOf('  if p_pivot_date is null'));
  assert.match(deletion,/perform set_config\('macrowatch\.explicit_manual_pivot_delete','true',true\)/);
  assert.match(deletion,/delete from public\.historical_indicator_manual_pivots m[\s\S]*m\.source_date=p_source_date/);
  assert.match(deletion,/delete from public\.historical_indicator_pivots a/);
  assert.doesNotMatch(deletion,/insert into public\.historical_indicator_manual_pivots/);
  assert.match(sql,/delete from public\.historical_indicator_manual_pivots where is_deleted=true/);
  assert.match(sql,/if auth\.role\(\) is distinct from 'service_role'/);
  assert.match(sql,/if not exists \(select 1 from public\.user_accounts where user_id=p_user_id and is_admin=true\)/);
});

test('one case-indicator pivot set is shared while key designations stay index-specific',()=>{
  const repository=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-indicator-data.js'),'utf8');
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923090000_share_historical_indicator_pivots.sql'),'utf8');
  assert.match(controller,/loadStoredPivots\(activeCase\.code,activeCase\.pivotSourceIndex,item\.code\)/);
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

test('reason presets are seeded in the database and loaded into the modal',()=>{
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260924053900_historical_pivot_reason_presets.sql'),'utf8');
  const presets=migration.split('insert into public.historical_pivot_reason_presets')[1];
  assert.ok(presets);
  assert.match(presets,/상승이 멈추고 고점권 횡보로 국면이 바뀌었습니다/);
  assert.match(presets,/하락이 멈추고 저점권 횡보로 국면이 바뀌었습니다/);
  assert.match(presets,/상승 흐름에서 급등한 뒤 방향을 되돌렸고, 이후 하락 흐름이 이어졌습니다/);
  assert.match(presets,/하락 흐름에서 급락한 뒤 방향을 되돌렸고, 이후 상승 흐름이 이어졌습니다/);
  assert.match(presets,/급등 후 추세적인 하락세로 전환됐습니다/);
  assert.match(presets,/급락 후 추세적인 상승세로 전환됐습니다/);
  assert.match(presets,/이전\/이후 추세가 불명확 합니다\./);
  assert.doesNotMatch(presets,/장기|오랫동안|장기간|중장기|막바지/);
  assert.doesNotMatch(controller,/MANUAL_PIVOT_REASONS/);
  assert.match(controller,/action:'list_historical_pivot_reason_presets'/);
  assert.match(migration,/alter table public\.historical_pivot_reason_presets enable row level security/);
  assert.equal((presets.match(/\(\d+, '/g)||[]).length,19);
});

test('the pivot reason choices come from the administrator API',async()=>{
  const from=controller.indexOf('  async function loadManualReasonPresets(');
  const to=controller.indexOf('  function state(',from);
  assert.ok(from>=0&&to>from);
  const select={options:[],replaceChildren(...items){this.options=items;},add(item){this.options.push(item);}};
  const scope={$:()=>select,Option:function(label,value){this.label=label;this.value=value;},
    functionClient:{invoke:async(name,payload)=>{
      assert.equal(name,'admin-control');
      assert.equal(payload.action,'list_historical_pivot_reason_presets');
      return{items:[{phrase:'새 관리자 문구'}]};
    }},manualReasonLoadError:''};
  const load=vm.runInNewContext(`${controller.slice(from,to)}\nloadManualReasonPresets`,scope);
  await load();
  assert.deepEqual(Array.from(select.options,item=>item.value),['','새 관리자 문구']);
  assert.equal(scope.manualReasonLoadError,'');
});

test('the administrator manages reason presets in a closed accordion without changing saved pivots',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../admin.html'),'utf8');
  const admin=fs.readFileSync(path.join(__dirname,'../assets/js/admin/admin.js'),'utf8');
  const edge=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  assert.match(html,/<section data-admin-card-id="historical-pivot-reasons"[\s\S]*?<details class="group">/);
  assert.match(admin,/save_historical_pivot_reason_preset/);
  assert.match(admin,/delete_historical_pivot_reason_preset/);
  assert.match(edge,/action === "list_historical_pivot_reason_presets"/);
  assert.match(edge,/action === "save_historical_pivot_reason_preset"/);
  assert.match(edge,/action === "delete_historical_pivot_reason_preset"/);
  assert.doesNotMatch(edge,/delete\(\)\.eq\("reason"/);
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

test('a non-key manual pivot with designated reference renders reference_only (light gray)',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const manual={sourceDate:'2022-02-15',pivotDate:'2022-02-15',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,designatedReference:'START',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[manual]},cycle);
  const target=result.find(pivot=>pivot.pivotDate==='2022-02-15');
  assert.equal(target.markerStatus,'reference_only');
  assert.equal(target.isManual,true);
  assert.equal(target.designatedReference,'START');
});

test('a non-key manual pivot with designated reference does not displace a confirmed magenta key',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const keyPivot={sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:50,relationship:'positive',
    reason:'핵심 변곡점',comment:'',keyReference:'START',designatedReference:'START',isDeleted:false};
  const nonKeyRefPivot={sourceDate:'2022-02-15',pivotDate:'2022-02-15',pivotValue:40,relationship:'inverse',
    reason:'참고 변곡점',comment:'',keyReference:null,designatedReference:'START',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[keyPivot,nonKeyRefPivot]},cycle);
  const confirmed=result.find(pivot=>pivot.pivotDate==='2022-01-05');
  const nonKey=result.find(pivot=>pivot.pivotDate==='2022-02-15');
  assert.equal(confirmed.markerStatus,'confirmed');
  assert.equal(confirmed.keyReference,'START');
  assert.equal(nonKey.markerStatus,'reference_only');
  assert.equal(nonKey.keyReference,null);
  assert.equal(nonKey.designatedReference,'START');
});

test('indicator repository parses non-key reference (_REF) into designatedReference and keyReference null',()=>{
  const repositoryContent=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-indicator-data.js'),'utf8');
  assert.match(repositoryContent,/rawKey\.endsWith\('_REF'\)/);
  assert.match(repositoryContent,/rawKey\.replace\('_REF',''\)/);
  assert.match(repositoryContent,/keyReference:isKey\?rawKey:null/);
});

test('admin control and migration accept non-key references START_REF, PEAK_REF, TROUGH_REF',()=>{
  const edge=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260926050000_manual_pivot_non_key_reference.sql'),'utf8');
  assert.match(edge,/\["START", "PEAK", "TROUGH", "AUTO", "START_REF", "PEAK_REF", "TROUGH_REF"\]\.includes\(keyReference\)/);
  assert.match(migration,/p_key_reference not in \('START',\s*'PEAK',\s*'TROUGH',\s*'AUTO',\s*'START_REF',\s*'PEAK_REF',\s*'TROUGH_REF'\)/);
  assert.match(migration,/elsif p_key_reference in \('START_REF',\s*'PEAK_REF',\s*'TROUGH_REF'\) then/);
});

test('modal keeps reference dropdown enabled when is-key is unchecked',()=>{
  const modalHtml=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  assert.doesNotMatch(modalHtml,/<select id="historical-manual-pivot-reference"[^>]*disabled/);
  assert.match(controller,/\$\('historical-manual-pivot-reference'\)\.disabled=false;/);
  assert.doesNotMatch(controller,/\$\('historical-manual-pivot-reference'\)\.disabled=!event\.target\.checked/);
});

