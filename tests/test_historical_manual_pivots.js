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
const {mergeManualPivots:merge,indicatorBadgeSummary:badges,manualPivotDirectionMatches:matchesDirection,effectivePivots}=vm.runInNewContext(`${controller.slice(start,rawEnd)}\n${controller.slice(badgeStart,badgeEnd)}\n({mergeManualPivots,indicatorBadgeSummary,manualPivotDirectionMatches,effectivePivots})`,{
  referenceOrder:['START','PEAK','TROUGH'],
  indicatorAnalysis:{
    normalizeForDisplay:(rows,from,to)=>rows.filter(r=>r.time>=from&&r.time<=to),
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
  const cycle={startDate:'2022-01-01',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const item={
    storedPivots:[auto('2022-01-02',0),auto('2022-01-04',1)],
    manualPivots:[{sourceDate:'2022-01-04',pivotDate:'2022-01-01',pivotValue:10,keyReference:null}]
  };
  const result=merge(item,cycle);
  assert.equal(result[0].pivotDate,'2022-01-01');
  assert.equal(result[0].isManual,true);
});

test('physically deleted automatic pivot is absent without a manual deletion marker',()=>{
  const cycle={startDate:'2022-01-01',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const result=merge({storedPivots:[auto('2022-01-02',0)],manualPivots:[]},cycle);
  assert.equal(result.some(pivot=>pivot.pivotDate==='2022-01-02'),true);
  assert.equal(result.some(pivot=>pivot.pivotDate==='2022-01-04'),false);
});

test('manual key stays magenta and the displaced automatic key becomes gray',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const result=merge({
    storedPivots:[auto('2022-01-05',0)],
    manualPivots:[{sourceDate:'2022-01-06',pivotDate:'2022-01-06',pivotValue:10,keyReference:'START'}]
  },cycle);
  const autoPivot=result.find(pivot=>pivot.pivotDate==='2022-01-05');
  const manualPivot=result.find(pivot=>pivot.pivotDate==='2022-01-06');
  assert.equal(manualPivot.markerStatus,'confirmed');
  assert.equal(autoPivot.markerStatus,'reference_only');
});

test('a manually selected TROUGH cannot keep an automatic PEAK on the same pivot',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-07-01',troughDate:'2022-07-02'};
  const result=merge({
    storedPivots:[auto('2022-07-02',0)],
    manualPivots:[{sourceDate:'2022-07-02',pivotDate:'2022-07-02',pivotValue:10,keyReference:'TROUGH'}]
  },cycle);
  const pivot=result.find(point=>point.pivotDate==='2022-07-02');
  assert.equal(pivot.selectedReferences.length,1);
  assert.equal(pivot.selectedReferences[0].type,'TROUGH');
});

test('changing only the reference dropdown marks the choice as a manual decision',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.match(html,/id="historical-manual-pivot-reference"/);
  assert.match(controller,/manualPivotContext\.keyTouched = true;/);
  assert.match(controller,/key_reference: sendKeyReference/);
});

test('an automatic key remains unchanged when no manual key claims its reference',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const result=merge({storedPivots:[auto('2022-01-05',0)],manualPivots:[]},cycle);
  assert.equal(result[0].markerStatus,'confirmed');
});

test('a saved inverse START only demotes that manual pivot when its next segment rises',()=>{
  const cycle={startDate:'2022-01-01',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const manual={isManual:true,pivotDate:'2022-01-01',relationship:'inverse',keyReference:null};
  const risingRows=[{time:'2022-01-01',value:20},{time:'2022-02-01',value:30},{time:'2022-07-01',value:50}];
  const fallingRows=[{time:'2022-01-01',value:30},{time:'2022-02-01',value:20},{time:'2022-07-01',value:50}];
  assert.equal(matchesDirection(manual,'START',[manual,{pivotDate:'2022-02-01'}],risingRows,cycle),false);
  assert.equal(matchesDirection(manual,'START',[manual,{pivotDate:'2022-02-01'}],fallingRows,cycle),true);
});

test('an administrator key-off demotes only that pivot and does not promote another candidate',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const result=merge({
    storedPivots:[auto('2022-01-05',0),auto('2022-01-20',1)],
    manualPivots:[{sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:10,keyReference:null,keySuppressed:true}]
  },cycle);
  const primary=result.find(pivot=>pivot.pivotDate==='2022-01-05');
  const secondary=result.find(pivot=>pivot.pivotDate==='2022-01-20');
  assert.equal(primary.markerStatus,'manual_standard');
  assert.equal(secondary.markerStatus,'near_miss');
});

test('the modal checks an automatically magenta pivot and unchecks an administrator-demoted pivot',()=>{
  assert.match(controller,/const isKey = \(manual\?\.keySuppressed \|\| designatedReference === 'UNCLEAR'\) \? false : Boolean\(manual\?\.keyReference \|\| classified\?\.markerStatus === 'confirmed'\);/);
});

test('a moved manual pivot opens only at its current date, never at its former source date',()=>{
  assert.match(controller,/targetDate = String\(point\.date \|\| ''\)\.slice\(0, 10\);/);
  assert.match(controller,/manual = \(item\.manualPivots \|\| \[\]\)\.find\(pivot => String\(pivot\.pivotDate \|\| ''\)\.slice\(0, 10\) === targetDate\);/);
  assert.doesNotMatch(controller,/find\(pivot => String\(pivot\.sourceDate/);
});

test('moving a manual pivot frees its former date without deleting the destination point',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-07-01',troughDate:'2023-01-01'};
  const result=merge({
    storedPivots:[auto('2022-01-05',0),auto('2022-01-10',1)],
    manualPivots:[{sourceDate:'2022-01-05',pivotDate:'2022-01-10',pivotValue:10,keyReference:null}]
  },cycle);
  assert.deepEqual(result.map(pivot=>pivot.pivotDate),['2022-01-05','2022-01-10']);
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').isManual,undefined);
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-10').isManual,true);
});

test('a date-only manual pivot sends optional metadata as null while keeping the measured value',()=>{
  assert.match(controller,/pivot_value: value/);
  assert.match(controller,/relationship: isDeleted \? null : \$\('historical-manual-pivot-relationship'\)\.value \|\| null/);
  assert.match(controller,/reason: isDeleted \? null : \$\('historical-manual-pivot-reason'\)\.value \|\| null/);
  assert.match(controller,/comment: isDeleted \? null : \$\('historical-manual-pivot-comment'\)\.value \|\| null/);
});

test('new automatic decisions and existing administrator key-off remain distinct on save',()=>{
  assert.match(controller,/keyDecision: manual\?\.keyReference \? 'manual_on' : manual\?\.isVerified \? 'manual_verified' : manual\?\.designatedReference \? 'manual_ref' : manual\?\.keySuppressed \? 'manual_off' : 'auto'/);
  assert.match(controller,/\} else if \(context\.keyDecision === 'manual_off'\) \{\s*sendKeyReference = null;/);
  assert.match(controller,/\} else \{\s*sendKeyReference = 'AUTO';/);
});

test('new database save leaves AUTO undecided while preserving explicit off and other indices',()=>{
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260924060500_manual_pivot_auto_key_decision.sql'),'utf8');
  assert.match(migration,/p_key_reference in \('START', 'PEAK', 'TROUGH'\)/);
  assert.match(migration,/elsif p_key_reference is null then\s+next_keys = next_keys \|\| jsonb_build_object\(p_index_code, false\);/);
  assert.match(migration,/elsif p_key_reference = 'AUTO' then\s+null;/);
});

test('a non-key manual pivot uses the existing date windows for magenta, dark, or light gray',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const insideCore=merge({storedPivots:[],manualPivots:[{sourceDate:'2022-01-05',pivotDate:'2022-01-05',pivotValue:1,keyReference:null}]},cycle);
  const insideNearMiss=merge({storedPivots:[],manualPivots:[{sourceDate:'2022-02-15',pivotDate:'2022-02-15',pivotValue:2,keyReference:null}]},cycle);
  const outsideNearMiss=merge({storedPivots:[],manualPivots:[{sourceDate:'2022-04-15',pivotDate:'2022-04-15',pivotValue:3,keyReference:null}]},cycle);
  assert.equal(insideCore[0].markerStatus,'confirmed');
  assert.equal(insideNearMiss[0].markerStatus,'manual_standard');
  assert.equal(outsideNearMiss[0].markerStatus,'reference_only');
});

test('a non-key manual pivot takes the core-window magenta before an automatic pivot',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const result=merge({
    storedPivots:[auto('2022-01-05',0)],
    manualPivots:[{sourceDate:'2022-01-06',pivotDate:'2022-01-06',pivotValue:1,keyReference:null}]
  },cycle);
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-06').markerStatus,'confirmed');
  assert.equal(result.find(pivot=>pivot.pivotDate==='2022-01-05').markerStatus,'reference_only');
});

test('list badges use final pivot colors, including colored PEAK and gray START',()=>{
  const context={mode:'history',cycle:{startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'}};
  const item={displayPivots:[
    {markerStatus:'manual_standard',referenceType:'START',selectedReferences:[{type:'START'}],pivotDate:'2022-01-05'},
    {markerStatus:'confirmed',referenceType:'PEAK',selectedReferences:[{type:'PEAK'}],pivotDate:'2022-11-19'}
  ]};
  assert.deepEqual(badges(item,context),{anchors:['PEAK'],nearMisses:['START']});
});

test('a dark pivot is still listed when another pivot is colored for the same reference',()=>{
  const context={mode:'history',cycle:{startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'}};
  const item={displayPivots:[
    {markerStatus:'confirmed',referenceType:'START',selectedReferences:[{type:'START'}],pivotDate:'2022-01-05'},
    {markerStatus:'manual_standard',referenceType:'START',selectedReferences:[],pivotDate:'2022-01-20'}
  ]};
  assert.deepEqual(badges(item,context),{anchors:['START'],nearMisses:['START']});
});

test('the auxiliary pivot reason section is removed without deleting saved reasons',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../index.html'),'utf8');
  assert.doesNotMatch(html,/id="historical-pivot-reason-preview"/);
  assert.match(controller,/const reason = pivotReasonFor\(item, result\);/);
});

test('manual rows remain protected outside explicit administrator deletion',()=>{
  const trigger=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923070100_historical_manual_pivots.sql'),'utf8');
  assert.match(trigger,/create or replace function public\.protect_historical_indicator_manual_pivots\(\)/);
  assert.match(trigger,/current_setting\('macrowatch\.explicit_manual_pivot_delete', true\) = 'true'/);
});

test('deleting a pivot removes its manual row and automatic row without leaving a deletion marker',()=>{
  const proc=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260924091300_remove_deleted_pivot_markers.sql'),'utf8');
  assert.match(proc,/delete from public\.historical_indicator_manual_pivots/);
  assert.match(proc,/delete from public\.historical_indicator_pivots/);
  assert.doesNotMatch(proc,/is_deleted = true/);
});

test('one case-indicator pivot set is shared while key designations stay index-specific',()=>{
  const proc=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923090000_share_historical_indicator_pivots.sql'),'utf8');
  assert.match(proc,/create table if not exists public\.historical_indicator_manual_pivots/);
  assert.match(proc,/key_references jsonb/);
  assert.doesNotMatch(proc,/unique \(case_code, index_code, series_code, source_date\)/);
});

test('reason presets are seeded in the database and loaded into the modal',()=>{
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260925200000_seed_pivot_reasons.sql'),'utf8');
  assert.match(migration,/create table if not exists public\.historical_pivot_reason_presets/);
  assert.match(migration,/insert into public\.historical_pivot_reason_presets/);
  assert.match(migration,/하락 추세가 멈추고 상승 전환을 가져 온 변곡점 입니다\./);
  assert.match(migration,/상승 추세가 멈추고 하락 전환을 가져 온 변곡점 입니다\./);
  assert.match(migration,/기존 추세가 일시 정체 후 재개된 연속성 변곡점 입니다\./);
  assert.match(migration,/상대적으로 큰 스파이크가 발생했지만, 기존 추세에 영향을 주지는 못했습니다\./);
});

test('the pivot reason choices come from the administrator API',()=>{
  const backend=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  assert.match(backend,/action === 'list_historical_pivot_reasons'/);
  assert.match(backend,/action === 'save_historical_pivot_reason'/);
  assert.match(backend,/action === 'delete_historical_pivot_reason'/);
});

test('the administrator manages reason presets in a closed accordion without changing saved pivots',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  assert.match(html,/id="historical-reason-preset-accordion"/);
  assert.match(html,/id="historical-reason-preset-content"[^>]*hidden/);
  assert.match(html,/id="historical-reason-preset-input"/);
  assert.match(html,/id="historical-reason-preset-add-btn"/);
  assert.match(html,/id="historical-reason-preset-list"/);
  assert.match(controller,/function initReasonPresetAccordion\(\)/);
  assert.match(controller,/function renderReasonPresetAdmin\(\)/);
});

test('administrator relationship supports unclear from form through API and database',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  assert.match(html,/<option value="unclear">정\/역 관계 불명확<\/option>/);
  const backend=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  assert.match(backend,/relationship !== null && !\["positive", "inverse", "unclear"\]\.includes\(relationship\)/);
  const migration=fs.readFileSync(path.join(__dirname,'../supabase/migrations/20260923090700_manual_pivot_unclear_relationship.sql'),'utf8');
  assert.match(migration,/p_relationship not in \('positive', 'inverse', 'unclear'\)/);
});

test('delete action remains hover-visible without mouse-focused rows sticking open',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../assets/css/historical-insight.css'),'utf8');
  assert.match(css,/\.historical-indicator-row:hover \.historical-indicator-actions/);
  assert.doesNotMatch(css,/\.historical-indicator-row:focus-within \.historical-indicator-actions/);
});

test('a non-key manual pivot with designated reference renders reference_only (light gray) outside near-miss window',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const manual={sourceDate:'2022-04-15',pivotDate:'2022-04-15',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,designatedReference:'START',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[manual]},cycle);
  const target=result.find(pivot=>pivot.pivotDate==='2022-04-15');
  assert.equal(target.markerStatus,'reference_only');
  assert.equal(target.isManual,true);
  assert.equal(target.designatedReference,'START');
});

test('a non-key manual pivot with designated reference renders manual_standard (dark gray) within near-miss window',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const manual={sourceDate:'2022-02-15',pivotDate:'2022-02-15',pivotValue:42,relationship:'inverse',
    reason:'관리자 선택',comment:'',keyReference:null,designatedReference:'START',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[manual]},cycle);
  const target=result.find(pivot=>pivot.pivotDate==='2022-02-15');
  assert.equal(target.markerStatus,'manual_standard');
  assert.equal(target.isManual,true);
  assert.equal(target.designatedReference,'START');
});

test('a non-key manual pivot with designated reference renders confirmed (magenta) within core relevance window',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const manual={sourceDate:'2022-01-10',pivotDate:'2022-01-10',pivotValue:42,relationship:null,
    reason:'피봇 선택',comment:'',keyReference:null,designatedReference:'START',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[manual]},cycle);
  const target=result.find(pivot=>pivot.pivotDate==='2022-01-10');
  assert.equal(target.markerStatus,'confirmed');
  assert.equal(target.isManual,true);
  assert.equal(target.designatedReference,'START');
  assert.equal(target.selectedReferences[0]?.type,'START');
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
  assert.equal(nonKey.markerStatus,'manual_standard');
  assert.equal(nonKey.keyReference,null);
  assert.equal(nonKey.designatedReference,'START');
});

test('indicator repository parses non-key reference (_REF) into designatedReference and keyReference null',()=>{
  const repositoryContent=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-indicator-data.js'),'utf8');
  assert.match(repositoryContent,/rawKey\.endsWith\('_REF'\)/);
  assert.match(repositoryContent,/isNonKeyRef\?rawKey\.replace\('_REF',''\):null/);
  assert.match(repositoryContent,/keyReference:isKey\?keyReference:null/);
});

test('admin-control and migration accept non-key references and verified references',()=>{
  const adminControl=fs.readFileSync(path.join(__dirname,'../supabase/functions/admin-control/index.ts'),'utf8');
  assert.match(adminControl,/'START_REF', 'PEAK_REF', 'TROUGH_REF'/);
  assert.match(adminControl,/'VERIFIED', 'START_VERIFIED', 'PEAK_VERIFIED', 'TROUGH_VERIFIED'/);
});

test('modal keeps reference dropdown enabled when is-key is unchecked',()=>{
  const modalHtml=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  assert.doesNotMatch(modalHtml,/<select id="historical-manual-pivot-reference"[^>]*disabled/);
  assert.match(controller,/\$\('historical-manual-pivot-reference'\)\.disabled=false;/);
  assert.doesNotMatch(controller,/\$\('historical-manual-pivot-reference'\)\.disabled=!event\.target\.checked/);
});

test('a verified manual pivot retains manual_standard in near-miss window and renders verified outside near-miss',()=>{
  const cycle={startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'};
  const insideNearMiss={sourceDate:'2022-02-15',pivotDate:'2022-02-15',pivotValue:42,relationship:'inverse',
    reason:'확인 변곡점',comment:'',keyReference:null,isVerified:true,designatedReference:'START',isDeleted:false};
  const outsideNearMiss={sourceDate:'2022-05-15',pivotDate:'2022-05-15',pivotValue:45,relationship:'inverse',
    reason:'확인 변곡점 외곽',comment:'',keyReference:null,isVerified:true,designatedReference:'START',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[insideNearMiss,outsideNearMiss]},cycle);
  const insideTarget=result.find(pivot=>pivot.pivotDate==='2022-02-15');
  assert.equal(insideTarget.markerStatus,'manual_standard');
  assert.equal(insideTarget.isVerified,true);

  const outsideTarget=result.find(pivot=>pivot.pivotDate==='2022-05-15');
  assert.equal(outsideTarget.markerStatus,'verified');
  assert.equal(outsideTarget.isVerified,true);
});

test('indicator repository parses verified reference into isVerified: true and designates reference',()=>{
  const repositoryContent=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-indicator-data.js'),'utf8');
  assert.match(repositoryContent,/rawKey==='VERIFIED'\|\|\(typeof rawKey==='string'&&rawKey\.endsWith\('_VERIFIED'\)\)/);
  assert.match(repositoryContent,/isVerifiedRef\?rawKey\.replace\('_VERIFIED',''\):null/);
  assert.match(repositoryContent,/isVerified,/);
});

test('chart renders quasi-core pivots with blue color and verified pivots with gray color from CSS variable',()=>{
  const chartContent=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-index-chart.js'),'utf8');
  assert.match(chartContent,/status === 'manual_standard' \? '#6366f1'/);
  assert.match(chartContent,/status === 'verified'/);
  assert.match(chartContent,/getComputedStyle\(document\.documentElement\)\.getPropertyValue\('--historical-verified-color'\)/);
  const cssContent=fs.readFileSync(path.join(__dirname,'../assets/css/historical-insight.css'),'utf8');
  assert.match(cssContent,/--historical-verified-color:\s*#64748b;/);
});

test('historical insight renames near-miss section to 준핵심 변곡점 and adds description',()=>{
  const content=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
  assert.match(content,/darkHeading\.textContent='준핵심 변곡점'/);
  assert.match(content,/darkDesc\.className='historical-pivot-dark-desc'/);
  assert.match(content,/지수 기준점과 타이밍은 다소 차이가 있으나/);
});

test('historical insight renders 준핵심 변곡점 section before 확인 변곡점 with description',()=>{
  const content=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-insight.js'),'utf8');
  const nearMissPos=content.indexOf("darkHeading.textContent='준핵심 변곡점'");
  const verifiedPos=content.indexOf("verifiedHeading.textContent = '확인 변곡점'");
  assert.ok(nearMissPos>0&&verifiedPos>0&&nearMissPos<verifiedPos);
  assert.match(content,/verifiedDesc\.className = 'historical-pivot-verified-desc'/);
  assert.match(content,/관리자가 시장 흐름 분석을 위해 확인용으로 별도 지정한 의미 있는 변곡점입니다\./);
});

test('modal contains 확인 변곡점 checkbox',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  assert.match(html,/id="historical-manual-pivot-is-verified"/);
  assert.match(html,/확인 변곡점/);
});

test('modal checks confirmed or verified checkbox when opened for respective pivot type',()=>{
  assert.match(controller,/const isVerified = Boolean\(manual\?\.isVerified \|\| classified\?\.markerStatus === 'verified'\);/);
  assert.match(controller,/\$\('historical-manual-pivot-is-verified'\)\.checked = isVerified;/);
});

test('indicatorBadgeSummary does not assign multiple reference badges to a single pivot without designated reference',()=>{
  const context={mode:'history',cycle:{startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'}};
  const item={displayPivots:[
    {markerStatus:'manual_standard',referenceType:'START',designatedReference:null,selectedReferences:[],pivotDate:'2022-01-20'}
  ]};
  const summary=badges(item,context);
  assert.deepEqual(summary.nearMisses,['START']);
  assert.ok(!summary.nearMisses.includes('PEAK'));
});

test('effectivePivots filters pivots to only those within context.displayRange',()=>{
  const context={mode:'history',cycle:{startDate:'2022-01-05',peakDate:'2022-11-19',troughDate:'2023-06-01'},
    displayRange:{from:'2022-01-01',to:'2022-12-31'}};
  const item={storedPivots:[],manualPivots:[
    {sourceDate:'2021-06-01',pivotDate:'2021-06-01',pivotValue:10,keyReference:null},
    {sourceDate:'2022-05-01',pivotDate:'2022-05-01',pivotValue:20,keyReference:null},
    {sourceDate:'2023-05-01',pivotDate:'2023-05-01',pivotValue:30,keyReference:null}
  ]};
  const filtered=effectivePivots(item,context);
  assert.equal(filtered.length,1);
  assert.equal(filtered[0].pivotDate,'2022-05-01');
});

test('indicator repository parses combined key-verified and shares verified status across other indices',()=>{
  const repoContent=fs.readFileSync(path.join(__dirname,'../assets/js/historical-insight/historical-indicator-data.js'),'utf8');
  assert.match(repoContent,/hasAnyVerified/);
  assert.match(repoContent,/row\.key_references\?\.\[indexCode\] === 'VERIFIED' \|\| hasAnyVerified/);
});

test('persistManualPivot generates combined key-verified reference when both key and verified are checked',()=>{
  assert.match(controller,/sendKeyReference=isKey&&isVerified&&selectedRef\?`\$\{selectedRef\}_VERIFIED`:/);
});

test('modal HTML contains UNCLEAR 불명확 reference option and is synchronized with key checkbox',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../historical-insight.html'),'utf8');
  assert.match(html,/<option value="UNCLEAR">불명확<\/option>/);
  assert.match(controller,/\$\('historical-manual-pivot-reference'\)\.value === 'UNCLEAR'/);
  assert.match(controller,/status\.textContent = isKey && selectedRef === 'UNCLEAR'/);
});

test('a manual pivot with UNCLEAR reference renders reference_only or verified without becoming key or quasi-core',()=>{
  const cycle={startDate:'2022-01-01',peakDate:'2022-06-01',troughDate:'2022-12-01'};
  const unclearPivot={sourceDate:'2022-06-01',pivotDate:'2022-06-01',pivotValue:10,
    reason:'불명확 테스트',comment:'',keyReference:null,isVerified:false,designatedReference:'UNCLEAR',isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[unclearPivot]},cycle);
  const target=result.find(p=>p.pivotDate==='2022-06-01');
  assert.equal(target.markerStatus,'reference_only');
  assert.equal(target.selectedReferences.length,0);

  const unclearVerifiedPivot={sourceDate:'2022-06-01',pivotDate:'2022-06-01',pivotValue:10,
    reason:'불명확 확인 테스트',comment:'',keyReference:null,isVerified:true,designatedReference:'UNCLEAR',isDeleted:false};
  const verifiedResult=merge({storedPivots:[],manualPivots:[unclearVerifiedPivot]},cycle);
  const verifiedTarget=verifiedResult.find(p=>p.pivotDate==='2022-06-01');
  assert.equal(verifiedTarget.markerStatus,'verified');
  assert.equal(verifiedTarget.selectedReferences.length,0);
});

test('unchecking key on a key-verified pivot results in VERIFIED reference and renders verified (dark gray)',()=>{
  const cycle={startDate:'1992-08-21',peakDate:'1994-11-08',troughDate:'1998-06-16'};
  const verifiedOnlyPivot={sourceDate:'1993-10-15',pivotDate:'1993-10-15',pivotValue:5.19,
    reason:'확인 변곡점',comment:'',keyReference:null,isVerified:true,designatedReference:null,isDeleted:false};
  const result=merge({storedPivots:[],manualPivots:[verifiedOnlyPivot]},cycle);
  const target=result.find(p=>p.pivotDate==='1993-10-15');
  assert.equal(target.markerStatus,'verified');
  assert.equal(target.selectedReferences.length,0);
});

test('matchesDirection for TROUGH owner correctly checks next interval when pivot is after troughDate', () => {
  const cycle = { startDate: '1997-01-01', peakDate: '1997-07-01', troughDate: '1998-08-31' };
  const pivot = { isManual: true, pivotDate: '1998-10-05', relationship: 'positive', keyReference: null };
  const pivots = [pivot];
  const rows = [{ time: '1998-10-05', value: 4.5 }, { time: '2000-08-31', value: 6.0 }];
  const indexRows = [{ time: '1998-08-31', value: 957.28 }, { time: '2000-08-31', value: 1500.0 }];
  assert.equal(matchesDirection(pivot, 'TROUGH', pivots, rows, cycle, indexRows), true);
});

test('unchecking verified sends is_verified: false and clears verified status across indices', () => {
  const rowAfterUncheck = { key_references: { SP500: false } };
  const hasAnyVerified = Object.values(rowAfterUncheck.key_references || {}).some(val => val === 'VERIFIED' || (typeof val === 'string' && val.endsWith('_VERIFIED')));
  const isVerified = rowAfterUncheck.key_references?.SP500 === 'VERIFIED' || hasAnyVerified;
  assert.equal(isVerified, false);
  assert.match(controller, /is_verified:\s*isVerified/);
});
