(() => {
  'use strict';
  const $=id=>document.getElementById(id),host=$('historical-chart-host');if(!host)return;
  const status=$('historical-chart-status'),meta=$('historical-chart-meta'),message=$('historical-chart-message'),retry=$('historical-chart-retry'),fullRange=$('historical-chart-full-range'),caseRange=$('historical-chart-case-range');
  const marketButtons=[...document.querySelectorAll('[data-historical-index]')],modeButtons=[...document.querySelectorAll('[data-historical-mode]')];
  const indexData=window.MacroWatchHistoricalData,cycleData=window.MacroWatchHistoricalCycles,indicatorData=window.MacroWatchHistoricalIndicators,indicatorAnalysis=window.MacroWatchHistoricalIndicatorAnalysis,selectionApi=window.MacroWatchHistoricalIndicatorSelection;
  let activeCode='NASDAQ_COMPOSITE',activeCase=null,activeMode='history',activeHistoricalCode=null,cases=[],currentSettings={currentName:'현재 국면 관찰 중'},currentSource=null,currentModel=null,requestToken=0,indexRepository,caseRepository,indicatorRepository,chart,activeRows=[],currentUser=null,isAdmin=false,functionClient=null,deleteCaseCode=null;
  let analysisCache=new Map(),visibleIndicators=[],selection=selectionApi?.create(),activeIndicatorContext=null,hiddenIndicatorCodes=new Set();
  let manualPivotContext=null;
  const MANUAL_PIVOT_REASONS=Object.freeze([
    '장기 상승 추세가 멈추고 하락 전환을 가져 온 큰 변곡점 입니다.',
    '오랫동안 이어진 상승이 멈추고 고점권 횡보로 국면이 바뀌었습니다.',
    '상승 막바지에 급등했지만 고점을 유지하지 못했고, 이후 하락 흐름으로 돌아섰습니다.',
    '급등 후 급락으로 이어지며 상승 흐름이 꺾인 전환점입니다.',
    '이전 고점에 다시 도달했으나 넘어서지 못하면서 장기 상승의 종료가 드러났습니다.',
    '장기 하락을 마친 뒤 상승 흐름이 이어지기 시작했습니다.',
    '장기 하락 추세가 멈추고 상승 전환을 가져 온 큰 변곡점 입니다.',
    '하락 막바지에 급락한 뒤 방향을 되돌렸고, 이후 상승 흐름이 이어졌습니다.',
    '급락 후 급등으로 이어지며 하락 흐름이 꺾인 전환점입니다.',
    '이전 저점을 다시 시험했으나 더 내려가지 않으면서 장기 하락의 종료가 드러났습니다.',
    '장기간 유지된 횡보 범위를 위로 벗어나 새로운 상승 흐름이 시작됐습니다.',
    '장기간 유지된 횡보 범위를 아래로 벗어나 새로운 하락 흐름이 시작됐습니다.',
    '급등 이후 이전보다 높은 수준을 유지하며 중장기 흐름이 달라졌습니다.',
    '급락 이후 이전보다 낮은 수준에 머물며 중장기 흐름이 달라졌습니다.',
    '기존 추세를 크게 뛰어넘는 급등의 시작점입니다.',
    '기존 추세를 크게 밑도는 급락의 시작점입니다.'
  ]);

  function state(kind,text){host.dataset.state=kind;host.setAttribute('aria-busy',String(kind==='loading'));status.textContent=text;message.textContent=kind==='ready'?'':text;message.hidden=kind==='ready';retry.hidden=kind!=='error';fullRange.disabled=caseRange.disabled=kind!=='ready';}
  function setMarket(code){activeCode=code;for(const button of marketButtons){const selected=button.dataset.historicalIndex===code;button.classList.toggle('is-active',selected);button.setAttribute('aria-selected',String(selected));}}
  function formatPoint(kind,point){const card=document.querySelector(`[data-cycle-point="${kind}"]`);if(!card)return;card.querySelector('strong').textContent=point?point.time:'미확정';card.querySelector('span').textContent=point?window.MacroWatchFrontend.formatDisplayNumber(point.value):'—';}
  const percentage=(value,absolute=false)=>value==null?'—':`${absolute?'':value>0?'+':''}${window.MacroWatchFrontend.formatDisplayNumber(absolute?Math.abs(value):value,{maximumFractionDigits:1})}%`,duration=value=>value==null?'—':`${window.MacroWatchFrontend.formatDisplayNumber(value)}일`;
  function showCurrentAnchors(cycle){if(activeMode!=='current')return;const points=[['start',cycle.startDate],['peak',cycle.peakDate],['trough',cycle.troughDate]],confirmed=points.filter(([,date])=>date).length;$('historical-current-anchor-state').textContent=confirmed===3?'모두 확정':confirmed?`${confirmed}/3 확정`:'미확정';for(const [kind,date] of points)$(`historical-current-${kind}`).textContent=date||'미확정';const editor=$('historical-current-anchor-editor');editor.hidden=!isAdmin||!currentSource;if(isAdmin&&currentSource){for(const [kind,date] of points)$(`historical-current-${kind}-date`).value=date||'';$('historical-current-anchor-save-status').textContent='';}}
  function showCycle(item,cycle,metrics){const panel=$('historical-cycle-panel');panel.hidden=activeMode==='current';$('historical-cycle-state').textContent=cycle.status==='confirmed'?'CONFIRMED CYCLE':cycle.status==='in_progress'?'IN PROGRESS':'DRAFT';$('historical-cycle-name').textContent=item.name;$('historical-cycle-market').textContent=indexData.indices[cycle.indexCode]||cycle.indexCode;$('historical-search-range').textContent=`관찰 범위 ${item.searchStart} ~ ${item.searchEnd||'현재'}`;$('historical-cycle-description').textContent=item.summary;formatPoint('start',metrics.start);formatPoint('peak',metrics.peak);formatPoint('trough',metrics.trough);$('historical-rise').textContent=percentage(metrics.rise);$('historical-fall').textContent=percentage(metrics.fall);$('historical-rise-days').textContent=duration(metrics.riseDays);$('historical-fall-days').textContent=duration(metrics.fallDays);showCurrentAnchors(cycle);if(isAdmin){$('historical-cycle-editor').hidden=activeMode==='current';$('historical-start-date').value=cycle.startDate||'';$('historical-peak-date').value=cycle.peakDate||'';$('historical-trough-date').value=cycle.troughDate||'';$('historical-cycle-save-status').textContent='';}}
  const isCurrentCase=item=>Object.values(item.markets).some(cycle=>cycle.status!=='confirmed'),isHistoricalCase=item=>Object.values(item.markets).length>0&&Object.values(item.markets).every(cycle=>cycle.status==='confirmed');
  function rebuildCurrentModel(){currentSource=cases.filter(isCurrentCase).sort((a,b)=>b.order-a.order)[0]||null;if(currentSource){currentModel=currentSource;return currentModel;}const troughs=cases.filter(isHistoricalCase).flatMap(item=>Object.values(item.markets).map(cycle=>cycle.troughDate).filter(Boolean)),searchStart=troughs.sort().at(-1)||'1990-01-01',markets=Object.freeze(Object.fromEntries(Object.keys(indexData.indices).map(indexCode=>[indexCode,Object.freeze({caseCode:'__current_monitoring__',indexCode,startDate:null,peakDate:null,troughDate:null,status:'draft'})])));currentModel=Object.freeze({code:'__current_monitoring__',order:Number.MAX_SAFE_INTEGER,name:currentSettings.currentName,primaryIndex:'NASDAQ_COMPOSITE',comparisons:['SP500','KOSPI'],searchStart,searchEnd:null,summary:'과거 선행 지표의 최신 피봇을 상시 관찰합니다.',markets});return currentModel;}

  const caseIndexCodes=()=>Object.keys(indexData.indices);
  function renderCaseCycleRows(cycles={}){
    const root=$('historical-case-cycle-grid');if(!root)return;root.replaceChildren();
    for(const code of caseIndexCodes()){
      const cycle=cycles[code]||{},row=document.createElement('div');row.className='historical-case-cycle-row';row.dataset.caseCycleIndex=code;
      const title=document.createElement('strong');title.textContent=indexData.indices[code]||code;row.append(title);
      for(const [key,label] of [['startDate','START'],['peakDate','PEAK'],['troughDate','TROUGH']]){
        const field=document.createElement('label'),input=document.createElement('input');field.textContent=label;input.type='date';input.dataset.caseCycleField=key;input.value=cycle[key]||'';field.append(input);row.append(field);
      }
      root.append(row);
    }
  }
  function closeCaseModal(){$('historical-case-modal').hidden=true;$('historical-case-save-status').textContent='';$('historical-case-auto-status').textContent='';}
  function openCaseModal(item=null){
    if(!isAdmin)return;
    $('historical-case-modal-title').textContent=item?'과거 국면 수정':'과거 국면 추가';
    $('historical-case-code').value=item?.code||'';
    $('historical-case-name').value=item?.name||'';
    $('historical-case-primary-index').value=item?.primaryIndex||'NASDAQ_COMPOSITE';
    $('historical-case-search-start').value=item?.searchStart||'';
    $('historical-case-search-end').value=item?.searchEnd||'';
    $('historical-case-summary').value=item?.summary||'';
    renderCaseCycleRows(item?.markets||{});
    $('historical-case-save-status').textContent='';
    $('historical-case-auto-status').textContent='';
    $('historical-case-modal').hidden=false;
    $('historical-case-name').focus();
  }
  function caseCyclesFromForm(){
    return [...document.querySelectorAll('[data-case-cycle-index]')].map(row=>({
      index_code:row.dataset.caseCycleIndex,
      start_date:row.querySelector('[data-case-cycle-field="startDate"]')?.value||null,
      peak_date:row.querySelector('[data-case-cycle-field="peakDate"]')?.value||null,
      trough_date:row.querySelector('[data-case-cycle-field="troughDate"]')?.value||null,
    }));
  }
  function applyPreviewCycles(items){
    const byCode=new Map((items||[]).map(item=>[item.index_code,item]));
    for(const row of document.querySelectorAll('[data-case-cycle-index]')){
      const item=byCode.get(row.dataset.caseCycleIndex);if(!item)continue;
      row.querySelector('[data-case-cycle-field="startDate"]').value=item.start_date||'';
      row.querySelector('[data-case-cycle-field="peakDate"]').value=item.peak_date||'';
      row.querySelector('[data-case-cycle-field="troughDate"]').value=item.trough_date||'';
    }
  }
  async function previewHistoricalCase(){
    if(!isAdmin||!functionClient)return;
    const button=$('historical-case-auto-analyze'),output=$('historical-case-auto-status');
    const payload={action:'preview_historical_case',case_name:$('historical-case-name').value,primary_index_code:$('historical-case-primary-index').value,search_start:$('historical-case-search-start').value,search_end:$('historical-case-search-end').value};
    button.disabled=true;output.textContent='피봇 계산·요약 생성 중';
    try{const result=await functionClient.invoke('admin-control',payload);applyPreviewCycles(result.cycles);$('historical-case-summary').value=result.summary||'';output.textContent='자동 후보 생성 완료 · 확인 후 저장';}
    catch(error){output.textContent='자동 분석 오류: '+(error?.message||'알 수 없는 오류');}
    finally{button.disabled=false;}
  }
  async function reloadHistoricalCases(preferredCode=null){
    caseRepository=cycleData.createRepository(window.macroWatchSupabase);cases=await caseRepository.load();analysisCache.clear();rebuildCurrentModel();renderCaseList();updateModeAvailability();
    const historical=cases.filter(isHistoricalCase),target=historical.find(item=>item.code===preferredCode)||historical.find(item=>item.code===activeHistoricalCode)||historical[0];
    if(target){activeHistoricalCode=target.code;activeMode='history';await selectCase(target.code);}
  }
  async function saveHistoricalCase(event){
    event.preventDefault();if(!isAdmin||!functionClient)return;
    const button=$('historical-case-save'),output=$('historical-case-save-status');button.disabled=true;output.textContent='저장 중';
    try{
      const result=await functionClient.invoke('admin-control',{action:'save_historical_case',case_code:$('historical-case-code').value,case_name:$('historical-case-name').value,primary_index_code:$('historical-case-primary-index').value,search_start:$('historical-case-search-start').value,search_end:$('historical-case-search-end').value||null,cycle_summary:$('historical-case-summary').value,cycles:caseCyclesFromForm()});
      closeCaseModal();await reloadHistoricalCases(result.case_code);
    }catch(error){output.textContent='저장 오류: '+(error?.message||'알 수 없는 오류');}
    finally{button.disabled=false;}
  }
  function openDeleteCase(item){
    if(!isAdmin)return;deleteCaseCode=item.code;$('historical-case-delete-message').textContent='"'+item.name+'" 국면과 연결된 자동 피봇·AI 분석 결과를 삭제합니다. 관리자 변곡점 기록은 기본적으로 남습니다.';$('historical-case-delete-manual-pivots').checked=false;$('historical-case-delete-status').textContent='';$('historical-case-delete-modal').hidden=false;
  }
  async function confirmDeleteCase(){
    if(!deleteCaseCode||!functionClient)return;const button=$('historical-case-delete-confirm'),output=$('historical-case-delete-status');button.disabled=true;output.textContent='삭제 중';
    try{await functionClient.invoke('admin-control',{action:'delete_historical_case',case_code:deleteCaseCode,delete_manual_pivots:$('historical-case-delete-manual-pivots').checked});const removed=deleteCaseCode;deleteCaseCode=null;$('historical-case-delete-modal').hidden=true;if(activeHistoricalCode===removed)activeHistoricalCode=null;await reloadHistoricalCases();}
    catch(error){output.textContent='삭제 오류: '+(error?.message||'알 수 없는 오류');}
    finally{button.disabled=false;}
  }
  function renderCaseList(){const root=$('historical-case-list');root.replaceChildren();for(const item of cases.filter(isHistoricalCase)){const row=document.createElement('div');row.className='historical-case-row';const button=document.createElement('button');button.type='button';button.className='historical-case';button.dataset.historicalCase=item.code;const label=document.createElement('span'),badge=document.createElement('small');label.textContent=item.name;badge.textContent='확정';button.append(label,badge);button.addEventListener('click',()=>selectCase(item.code));row.append(button);if(isAdmin){const actions=document.createElement('span');actions.className='historical-case-actions';const edit=document.createElement('button'),del=document.createElement('button');edit.type=del.type='button';edit.innerHTML='<i class="fa-solid fa-pen"></i>';del.innerHTML='<i class="fa-solid fa-trash"></i>';edit.title='수정';del.title='삭제';del.className='is-delete';edit.addEventListener('click',()=>openCaseModal(item));del.addEventListener('click',()=>openDeleteCase(item));actions.append(edit,del);row.append(actions);}root.append(row);}}

  function activeCaseButton(){document.querySelectorAll('[data-historical-case]').forEach(button=>button.classList.toggle('is-active',button.dataset.historicalCase===activeCase?.code));}
  function focusCase(){if(!chart||!activeRows.length||!activeCase)return;const cycle=cycleData.marketCycle(activeCase,activeCode);const from=cycle.startDate||activeCase.searchStart;const to=cycle.troughDate || cycle.peakDate || activeRows.at(-1).time;chart.focus(from,to,.12);}
  function indicatorLoading(text='비교 지표 분석 중'){const root=$('historical-indicator-list');if(root){root.replaceChildren();const p=document.createElement('p');p.className='historical-indicator-empty';p.textContent=text;root.append(p);}}
  async function mapSeries(metadata,worker){const out=[];for(let i=0;i<metadata.length;i+=6){const batch=await Promise.all(metadata.slice(i,i+6).map(worker));out.push(...batch.filter(Boolean));}return out;}
  async function calculateIndicatorContext(){const key=`${activeMode}:${activeCase.code}:${activeCode}`;if(analysisCache.has(key))return analysisCache.get(key);const coverage=await indicatorRepository.loadCoverage(),catalog=indicatorRepository.catalog(activeCode).filter(item=>!hiddenIndicatorCodes.has(item.code)),cycle=cycleData.marketCycle(activeCase,activeCode),end=indicatorAnalysis.analysisEnd(activeCase,cycle),latest=activeRows.at(-1)?.time||end,displayRange=indicatorAnalysis.displayWindow(activeCase,cycle,latest);if(activeMode==='history'){const eligible=catalog.filter(item=>{const c=coverage.get(item.code);return c&&c.firstDate<=activeCase.searchStart&&c.lastDate>=end;}),loadFrom=displayRange.from<activeCase.searchStart?displayRange.from:activeCase.searchStart,loadTo=displayRange.to>end?displayRange.to:end,analyses=await mapSeries(eligible,async item=>{const rows=await indicatorRepository.load(item.code,loadFrom,loadTo),analysis=indicatorAnalysis.analyzeHistorical(item,rows,activeCase,cycle,activeRows),[storedPivots,manualPivots]=await Promise.all([indicatorRepository.loadStoredPivots(activeCase.code,activeCode,item.code),indicatorRepository.loadManualPivots(activeCase.code,activeCode,item.code)]);return Object.freeze({...analysis,storedPivots,manualPivots});}),value=Object.freeze({mode:'history',analyses,end,displayRange,cycle});analysisCache.set(key,value);return value;}const historicalCases=cases.filter(isHistoricalCase),usable=catalog.filter(item=>coverage.has(item.code)),historyStart=historicalCases.reduce((date,item)=>date<item.searchStart?date:item.searchStart,activeCase.searchStart),marketEntries=await Promise.all(Object.keys(indexData.indices).map(async code=>[code,await indexRepository.load(code)])),marketRowsByCode=Object.fromEntries(marketEntries);const history=await mapSeries(usable,async item=>{const rows=await indicatorRepository.load(item.code,historyStart,latest),c=coverage.get(item.code);for(const past of historicalCases){for(const pastCycle of Object.values(past.markets)){const pastEnd=indicatorAnalysis.analysisEnd(past,pastCycle);if(c.firstDate>past.searchStart||c.lastDate<pastEnd)continue;const pastWindow=indicatorAnalysis.displayWindow(past,pastCycle,pastEnd),pastRows=rows.filter(row=>row.time>=pastWindow.from&&row.time<=pastWindow.to),pastMarketRows=marketRowsByCode[pastCycle.indexCode]||[],analysis=indicatorAnalysis.analyzeHistorical(item,pastRows,past,pastCycle,pastMarketRows);if(analysis.meaningfulReferenceCount>0)return{item,rows,historicalScore:analysis.overallScore,meaningful:true};}}return{item,rows,historicalScore:0,meaningful:false};});const currentContext={item:activeCase,cycle,marketRows:marketRowsByCode[activeCode]||activeRows},rawAnalyses=history.filter(entry=>entry.meaningful).map(entry=>({...indicatorAnalysis.analyzeCurrent(entry.item,entry.rows,historyStart,latest,currentContext),historicalScore:entry.historicalScore})),analyses=indicatorAnalysis.applyCurrentSynergy(rawAnalyses),value=Object.freeze({mode:'current',analyses,end:latest,displayRange});analysisCache.set(key,value);return value;}
  function candidatesFor(context){if(context.mode==='history')return{items:context.analyses.filter(item=>(item.storedPivots?.length)||(item.manualPivots?.some(pivot=>!pivot.isDeleted))||(item.visible&&((item.overallScore!==null&&Number.isFinite(item.overallScore))||item.reviewPivots?.length))).sort(indicatorAnalysis.compareAnalyses),signal:null};const rank={market_relevant_confirmed:0,structural_only:1,candidate:2,watch:3,watching:4},items=[...context.analyses].sort((a,b)=>(rank[a.evidence?.signalState]??5)-(rank[b.evidence?.signalState]??5)||b.evidence.score-a.evidence.score||b.historicalScore-a.historicalScore||a.meta.title.localeCompare(b.meta.title,'ko'));return{items,signal:indicatorAnalysis.currentPivotProbability(items)};}
  function classifyStoredPivots(pivots,cycle){
    const refs=[['START',cycle?.startDate],['PEAK',cycle?.peakDate],['TROUGH',cycle?.troughDate]].filter(([,date])=>date);
    const days=(a,b)=>Math.round((Date.parse(`${a}T00:00:00Z`)-Date.parse(`${b}T00:00:00Z`))/86400000);
    const source=[...(pivots||[])];
    const selectedByReference=new Map();
    for(const [type,date] of refs){
      const window=indicatorAnalysis.relevanceWindow(date);
      const candidates=source.filter(pivot=>pivot.pivotDate>=window.from&&pivot.pivotDate<=window.to)
        .sort((a,b)=>Math.abs(days(a.pivotDate,date))-Math.abs(days(b.pivotDate,date))||a.pivotDate.localeCompare(b.pivotDate)||a.pivotOrder-b.pivotOrder);
      if(candidates.length)selectedByReference.set(type,candidates[0]);
    }
    return source.map(pivot=>{
      const selectedRefs=refs.filter(([type])=>selectedByReference.get(type)===pivot).map(([type,date])=>({type,date,offsetDays:days(pivot.pivotDate,date)}));
      const extended=refs.map(([type,date])=>({type,date,window:indicatorAnalysis.nearMissWindow(date),offsetDays:days(pivot.pivotDate,date)}))
        .filter(item=>pivot.pivotDate>=item.window.from&&pivot.pivotDate<=item.window.to)
        .sort((a,b)=>Math.abs(a.offsetDays)-Math.abs(b.offsetDays))[0]||null;
      const primary=selectedRefs[0]||extended;
      return Object.freeze({
        ...pivot,
        selectedReferences:Object.freeze(selectedRefs),
        referenceType:primary?.type||null,
        referenceDate:primary?.date||null,
        offsetDays:primary?.offsetDays??null,
        markerStatus:selectedRefs.length?'confirmed':extended?'near_miss':'reference_only',
        pivotReason:pivot.selectionReason
      });
    });
  }
  function mergeManualPivots(item,cycle){
    const manual=item.manualPivots||[],blocked=new Set(manual.map(pivot=>pivot.sourceDate)),active=manual.filter(pivot=>!pivot.isDeleted),occupied=new Set(manual.map(pivot=>pivot.pivotDate).filter(Boolean));
    const automatic=[...new Map((item.storedPivots||[]).filter(pivot=>!blocked.has(pivot.pivotDate)&&!occupied.has(pivot.pivotDate)).map(pivot=>[pivot.pivotDate,pivot])).values()];
    const merged=[...automatic,...active.map(pivot=>({
      pivotOrder:Number.MAX_SAFE_INTEGER,pivotDate:pivot.pivotDate,pivotValue:pivot.pivotValue,
      selectionReason:[pivot.reason,pivot.comment].filter(Boolean).join('\n'),
      pivotReason:[pivot.reason,pivot.comment].filter(Boolean).join('\n'),
      relationship:pivot.relationship,sourceDate:pivot.sourceDate,keyReference:pivot.keyReference,isManual:true
    }))].sort((a,b)=>a.pivotDate.localeCompare(b.pivotDate));
    const classified=classifyStoredPivots(merged,cycle),manualKeys=new Map(active.filter(pivot=>pivot.keyReference).map(pivot=>[pivot.keyReference,pivot.sourceDate]));
    const referenceDates={START:cycle?.startDate,PEAK:cycle?.peakDate,TROUGH:cycle?.troughDate};
    return classified.map(pivot=>{
      let selectedReferences=(pivot.selectedReferences||[]).filter(ref=>!manualKeys.has(ref.type)||pivot.sourceDate===manualKeys.get(ref.type));
      if(pivot.isManual&&pivot.keyReference){const date=referenceDates[pivot.keyReference];selectedReferences=[...selectedReferences.filter(ref=>ref.type!==pivot.keyReference),{type:pivot.keyReference,date,offsetDays:date?Math.round((Date.parse(pivot.pivotDate)-Date.parse(date))/86400000):null}];}
      const overridden=pivot.markerStatus==='confirmed'&&!selectedReferences.length;
      return Object.freeze({...pivot,selectedReferences:Object.freeze(selectedReferences),markerStatus:pivot.isManual&&!pivot.keyReference?(pivot.markerStatus==='reference_only'?'reference_only':'manual_standard'):overridden?'overridden_key':selectedReferences.length?'confirmed':pivot.markerStatus});
    });
  }
  function displayItem(item,context){let displayPivots=context.mode==='history'&&(item.storedPivots?.length||item.manualPivots?.length)?mergeManualPivots(item,context.cycle):[...(item.results||[]),...(item.nearMissPivots||[]),...(item.manualDisplayPivots||[])];if(context.mode==='current'){const latest=item.evidence?.pending?{pivotDate:item.evidence.pending.candidateDate,regimeBoundaryDate:item.evidence.pending.regimeBoundaryDate,referenceType:'CURRENT_STRUCTURAL',markerStatus:item.evidence.status}:item.evidence?.result||null;displayPivots=[...(item.confirmedReferences||[]),...(latest?[latest]:[])];}displayPivots=[...new Map(displayPivots.map(pivot=>[context.mode==='history'?pivot.pivotDate:`${pivot.markerStatus||''}:${pivot.referenceType||''}:${pivot.pivotDate}`,pivot])).values()];return{...item,displayRows:indicatorAnalysis.normalizeForDisplay(item.rows,context.displayRange.from,context.displayRange.to),displayPivots};}
  function rawValueAtDate(rows,date){
    const exact=rows.find(row=>row.time===date);
    if(exact)return exact.value;
    const right=rows.findIndex(row=>row.time>date),before=rows[right-1],after=rows[right];
    if(!before||!after)return null;
    const from=Date.parse(before.time),to=Date.parse(after.time),at=Date.parse(date);
    return before.value+(after.value-before.value)*(at-from)/(to-from);
  }
  function closeManualPivotModal(){manualPivotContext=null;$('historical-manual-pivot-modal').hidden=true;$('historical-manual-pivot-status').textContent='';}
  function openManualPivotModal(point){
    if(!isAdmin||activeMode!=='history'||!functionClient||!activeIndicatorContext)return;
    const item=activeIndicatorContext.analyses.find(candidate=>candidate.meta.code===point.code);
    if(!item)return;
    const manual=(item.manualPivots||[]).find(pivot=>!pivot.isDeleted&&pivot.pivotDate===point.date)
      ||(item.manualPivots||[]).find(pivot=>pivot.sourceDate===point.date||pivot.pivotDate===point.date);
    const automatic=(item.storedPivots||[]).find(pivot=>pivot.pivotDate===point.date);
    const existing=Boolean(manual&&!manual.isDeleted||automatic);
    manualPivotContext={caseCode:activeCase.code,indexCode:activeCode,seriesCode:point.code,sourceDate:manual?.sourceDate||point.date,item,existing};
    $('historical-manual-pivot-title').textContent=`${item.meta.title} · ${existing?'변곡점 수정':'변곡점 추가'}`;
    $('historical-manual-pivot-date').value=manual&&!manual.isDeleted?manual.pivotDate:point.date;
    $('historical-manual-pivot-relationship').value=manual&&!manual.isDeleted?manual.relationship||'':'';
    $('historical-manual-pivot-reason').value=manual&&!manual.isDeleted?manual.reason||'':'';
    $('historical-manual-pivot-comment').value=manual&&!manual.isDeleted?manual.comment||'':'';
    $('historical-manual-pivot-is-key').checked=Boolean(manual&&!manual.isDeleted&&manual.keyReference);
    $('historical-manual-pivot-reference').value=manual&&!manual.isDeleted?manual.keyReference||'':'';
    $('historical-manual-pivot-reference').disabled=!$('historical-manual-pivot-is-key').checked;
    $('historical-manual-pivot-delete').hidden=!existing;
    $('historical-manual-pivot-status').textContent='';
    $('historical-manual-pivot-modal').hidden=false;
    $('historical-manual-pivot-date').focus();
  }
  async function persistManualPivot(isDeleted){
    const context=manualPivotContext;
    if(!isAdmin||!context||!functionClient)return;
    const status=$('historical-manual-pivot-status'),date=$('historical-manual-pivot-date').value,
      keyReference=$('historical-manual-pivot-is-key').checked?$('historical-manual-pivot-reference').value:null,
      value=isDeleted?null:rawValueAtDate(context.item.rows,date);
    if(!isDeleted&&(!Number.isFinite(value)||!date||!$('historical-manual-pivot-relationship').value
      ||!$('historical-manual-pivot-reason').value||$('historical-manual-pivot-is-key').checked&&!keyReference)){
      status.textContent='날짜·관계·근거·기준점을 확인해 주세요.';return;
    }
    if(!isDeleted&&keyReference&&(context.item.manualPivots||[]).some(pivot=>!pivot.isDeleted&&pivot.keyReference===keyReference&&pivot.sourceDate!==context.sourceDate)){
      status.textContent=`이 지표의 ${keyReference} 핵심 변곡점이 이미 있습니다. 기존 지정을 관리자 화면에서 먼저 해제해 주세요.`;return;
    }
    const save=$('historical-manual-pivot-save'),del=$('historical-manual-pivot-delete');
    save.disabled=del.disabled=true;status.textContent='저장 중';
    try{
      await functionClient.invoke('admin-control',{
        action:'save_historical_indicator_manual_pivot',case_code:context.caseCode,index_code:context.indexCode,
        series_code:context.seriesCode,source_date:context.sourceDate,is_deleted:isDeleted,
        pivot_date:isDeleted?null:date,pivot_value:value,
        relationship:isDeleted?null:$('historical-manual-pivot-relationship').value,
        reason:isDeleted?null:$('historical-manual-pivot-reason').value,
        comment:isDeleted?null:$('historical-manual-pivot-comment').value,key_reference:isDeleted?null:keyReference
      });
      indicatorRepository.clearManualPivots(context.caseCode,context.indexCode,context.seriesCode);
      analysisCache.delete(`history:${context.caseCode}:${context.indexCode}`);
      closeManualPivotModal();
      if(activeCase?.code===context.caseCode&&activeCode===context.indexCode&&activeMode==='history')await refreshIndicators(requestToken);
    }catch(error){status.textContent=`저장 오류: ${error?.message||'알 수 없는 오류'}`;}
    finally{save.disabled=del.disabled=false;}
  }
  const timingLabel=result=>result.offsetDays===0?'기준점 당일':`기준점 ${Math.abs(result.offsetDays)}일 ${result.offsetDays<0?'전':'후'}`;
  const referenceOrder=['START','PEAK','TROUGH'];
  const regimeLabel=type=>({rising:'상승',falling:'하락',sideways:'횡보'}[type]||type);
  const transitionLabel=result=>`${regimeLabel(result.previousRegime)} → ${regimeLabel(result.nextRegime)} ${['rising','falling'].includes(result.previousRegime)&&['rising','falling'].includes(result.nextRegime)?'반전':'전환'}`;
  const relationshipLabel=value=>({positive:'정 관계',inverse:'역 관계',unclear:'정/역 관계 불명확'}[value]||value);
  const indicatorValue=(result,meta)=>`${window.MacroWatchFrontend.formatDisplayNumber(result.pivotValue,{maximumFractionDigits:meta.decimals})}${meta.unit==='%'?'%':` ${meta.unit}`}`;
  const anchorSummary=item=>referenceOrder.filter(type=>item.byReference?.[type]);
  const nearMissSummary=item=>referenceOrder.filter(type=>(item.nearMissPivots||[]).some(pivot=>pivot.referenceType===type));
  const pivotReasonFor=(item,result)=>{if(String(result?.pivotReason||'').trim())return String(result.pivotReason).trim();const date=String(result?.pivotDate||'').slice(0,10),pivot=(item?.pivots||[]).find(candidate=>String(candidate?.date||'').slice(0,10)===date);return String(pivot?.reason||'').trim();};
  function caseDetailRange(){
    if(!activeRows.length||!activeCase)return null;
    const cycle=cycleData.marketCycle(activeCase,activeCode),from=cycle.startDate||activeCase.searchStart,to=cycle.troughDate||cycle.peakDate||activeRows.at(-1)?.time;
    const startIndex=activeRows.findIndex(row=>row.time>=from),endIndex=activeRows.findLastIndex(row=>row.time<=to);
    if(startIndex<0||endIndex<startIndex)return null;
    const span=Math.max(1,endIndex-startIndex),context=Math.max(1,span*.12/(1-.24));
    return Object.freeze({
      from:activeRows[Math.floor(Math.max(0,startIndex-context))].time,
      to:activeRows[Math.ceil(Math.min(activeRows.length-1,endIndex+context))].time
    });
  }
  function appendAuxiliaryPivotReasons(root,item){
    const range=caseDetailRange(),items=[...(item?.displayPivots||[])].filter(pivot=>pivot.markerStatus!=='confirmed'&&(!range||(pivot.pivotDate>=range.from&&pivot.pivotDate<=range.to))),seen=new Set(),rows=[];
    for(const pivot of items){const key=`${pivot.pivotDate||''}:${pivot.pivotType||''}`;if(seen.has(key))continue;seen.add(key);rows.push(pivot);}
    if(!rows.length)return;
    const section=document.createElement('section'),title=document.createElement('strong'),grid=document.createElement('div');
    section.className='historical-ab-reason-section';title.className='historical-ab-reason-title';title.textContent='기타 피봇 판정 근거';grid.className='historical-ab-reason-grid';
    for(const pivot of rows){const card=document.createElement('article'),head=document.createElement('strong'),body=document.createElement('p');head.textContent=pivot.pivotDate||'날짜 없음';body.textContent=pivotReasonFor(item,pivot)||'근거 미저장';card.append(head,body);grid.append(card);}
    section.append(title,grid);root.append(section);
  }
  function closePivotReviewModal(){document.querySelector('.historical-pivot-review-modal')?.remove();}
  async function resolveDPivot(item,pivot,resolution,button,status){
    if(!isAdmin||!functionClient)return;button.disabled=true;status.textContent='저장 중';
    try{
      await functionClient.invoke('admin-control',{action:'resolve_historical_pivot_review',case_code:activeCase.code,index_code:activeCode,series_code:item.meta.code,pivot_date:pivot.date,resolution});
      window.MacroWatchHistoricalIndicators?.clearAiScores?.();analysisCache.clear();closePivotReviewModal();await render(activeCode);
    }catch(error){status.textContent='저장 오류: '+(error?.message||'알 수 없는 오류');button.disabled=false;}
  }
  function openPivotReviewModal(item,pivot){
    if(!isAdmin)return;closePivotReviewModal();
    const overlay=document.createElement('div'),card=document.createElement('div'),head=document.createElement('div'),title=document.createElement('strong'),close=document.createElement('button'),meta=document.createElement('p'),reason=document.createElement('p'),actions=document.createElement('div'),status=document.createElement('span');
    overlay.className='historical-pivot-review-modal';card.className='historical-pivot-review-card';head.className='historical-pivot-review-head';title.textContent=`D 피봇 검토 · ${item.meta.title}`;close.type='button';close.textContent='×';close.addEventListener('click',closePivotReviewModal);head.append(title,close);
    meta.className='historical-pivot-review-meta';meta.textContent=`${pivot.date} · ${pivot.direction||'neutral'} · ${pivot.type||'review'}`;reason.className='historical-pivot-review-reason';reason.textContent=String(pivot.reason||'').trim()||'판단 근거가 없습니다.';
    actions.className='historical-pivot-review-actions';for(const value of ['A','B','C','DELETE']){const button=document.createElement('button');button.type='button';button.dataset.resolution=value;button.textContent=value==='DELETE'?'삭제':`${value}로 확정`;if(value==='DELETE')button.className='is-delete';button.addEventListener('click',()=>resolveDPivot(item,pivot,value,button,status));actions.append(button);}
    status.className='historical-pivot-review-status';card.append(head,meta,reason,actions,status);overlay.append(card);overlay.addEventListener('click',event=>{if(event.target===overlay)closePivotReviewModal();});document.body.append(overlay);
  }
  function appendDReviews(root,item){
    const pivots=[...(item?.reviewPivots||[])].sort((a,b)=>String(a?.date||'').localeCompare(String(b?.date||'')));if(!pivots.length)return;
    const section=document.createElement('section'),title=document.createElement('strong'),grid=document.createElement('div');section.className='historical-d-review-section';title.className='historical-ab-reason-title';title.textContent='D · 판단 보류';grid.className='historical-d-review-grid';
    for(const pivot of pivots){const card=document.createElement('article'),head=document.createElement('strong'),body=document.createElement('p');head.textContent=`D · ${pivot.date||'날짜 없음'} · ${pivot.direction||'neutral'}`;body.textContent=String(pivot.reason||'').trim()||'근거 미저장';card.append(head,body);if(isAdmin){const button=document.createElement('button');button.type='button';button.textContent='판정하기';button.addEventListener('click',()=>openPivotReviewModal(item,pivot));card.append(button);}grid.append(card);}section.append(title,grid);root.append(section);
  }
  function allIndicatorMetadata(){
    const registry=window.MacroWatchEconomicSeriesRegistry?.allSeries||[];
    return registry.filter(item=>!['SP500','NASDAQ_COMPOSITE','KOSPI'].includes(item.code));
  }
  function closeHiddenIndicatorMenu(){const menu=$('historical-indicator-hidden-menu');if(menu)menu.hidden=true;}
  function renderHiddenIndicatorMenu(){
    const menu=$('historical-indicator-hidden-menu');if(!menu||!isAdmin)return;
    menu.replaceChildren();
    const byCode=new Map(allIndicatorMetadata().map(item=>[item.code,item]));
    const items=[...hiddenIndicatorCodes].map(code=>byCode.get(code)).filter(Boolean).sort((a,b)=>a.title.localeCompare(b.title,'ko'));
    if(!items.length){const p=document.createElement('p');p.className='historical-indicator-empty';p.textContent='삭제한 지표가 없습니다.';menu.append(p);return;}
    for(const item of items){const button=document.createElement('button');button.type='button';button.textContent=item.title;const icon=document.createElement('i');icon.className='fa-solid fa-plus';button.append(icon);button.addEventListener('click',()=>restoreIndicator(item.code));menu.append(button);}
  }
  async function setIndicatorHidden(code,hidden){
    if(!isAdmin||!indicatorRepository||!currentUser)return;
    await indicatorRepository.setHidden(code,hidden,currentUser.id);
    hiddenIndicatorCodes=new Set(await indicatorRepository.loadVisibility());
    analysisCache.clear();
    indicatorRepository.clearAnalysisData();
    if(hidden&&selection?.snapshot().selected===code)selection.clear();
    closeHiddenIndicatorMenu();
    if(activeCase)await render(activeCode);
  }
  async function deleteIndicator(code){
    if(!isAdmin)return;
    try{await setIndicatorHidden(code,true);}catch(error){console.error('[Historical indicator delete]',error);state('error','지표 삭제 설정을 저장하지 못했습니다.');}
  }
  async function restoreIndicator(code){
    if(!isAdmin)return;
    try{await setIndicatorHidden(code,false);}catch(error){console.error('[Historical indicator restore]',error);state('error','지표 복구 설정을 저장하지 못했습니다.');}
  }

  function renderIndicators(context){
    activeIndicatorContext=context;
    const group=candidatesFor(context);
    visibleIndicators=group.items;
    const snapshot=selection.reconcile(visibleIndicators),root=$('historical-indicator-list');
    root.replaceChildren();
    if(!visibleIndicators.length){
      const p=document.createElement('p');p.className='historical-indicator-empty';
      p.textContent=activeMode==='history'?'이 국면의 기준점과 관련된 확정 피봇 지표가 없습니다.':'과거 국면에서 의미 있었던 지표가 없습니다.';
      root.append(p);
    }else{
      if(activeMode==='current'){
        const p=document.createElement('p');p.className='historical-indicator-note';
        p.textContent='과거 국면에서 한 번이라도 의미 있었던 전체 지표를 매일 같은 기준으로 스캔합니다.';
        root.append(p);
      }
      for(const item of visibleIndicators){
        const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),name=document.createElement('span'),anchors=anchorSummary(item),nearMisses=nearMissSummary(item),signalState=item.evidence?.signalState;
        row.className='historical-indicator-row';
        input.type='radio';input.name='historical-indicator';input.value=item.meta.code;input.checked=snapshot.selected===item.meta.code;
        input.addEventListener('change',()=>{if(!input.checked)return;selection.select(item.meta.code);drawIndicators(context);focusCase();});
        name.textContent=item.meta.title;label.append(input,name);
        if(activeMode==='history'){
          const value=Number(item.overallScore);
          if(Number.isFinite(value)&&value>0){const score=document.createElement('em');score.className='historical-indicator-score';score.textContent=`${Math.round(value)}점`;label.append(score);}
          if(item.reviewPivots?.length){const review=document.createElement('em');review.className='historical-indicator-score is-review';review.textContent=`D검토 ${item.reviewPivots.length}`;label.append(review);}
        }else{
          const score=document.createElement('em');score.className='historical-indicator-score';
          score.textContent=signalState==='market_relevant_confirmed'?`시장 관련 ${Math.round(item.evidence.score)}점`:signalState==='structural_only'?`구조 유지 ${Math.round(item.evidence.score)}점`:signalState==='candidate'?`후보 ${Math.round(item.evidence.score)}점`:signalState==='watch'?`감시 ${Math.round(item.evidence.score)}점`:'신호 없음';
          label.append(score);
        }
        if(anchors.length||nearMisses.length){
          const badges=document.createElement('span');label.className='has-references';badges.className='historical-indicator-badges';
          for(const type of anchors){const badge=document.createElement('b');badge.className='historical-reference-badge';badge.dataset.reference=type;badge.textContent=type;badges.append(badge);}
          for(const type of nearMisses){const badge=document.createElement('b');badge.className='historical-reference-badge is-near-miss';badge.textContent=type;badges.append(badge);}
          label.append(badges);
        }
        row.append(label);
        if(isAdmin){
          const actions=document.createElement('span'),del=document.createElement('button');
          actions.className='historical-indicator-actions';del.type='button';del.className='is-delete';del.title='전체 국면에서 지표 삭제';del.setAttribute('aria-label',item.meta.title+' 삭제');
          del.innerHTML='<i class="fa-solid fa-trash"></i>';
          del.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();deleteIndicator(item.meta.code);});
          actions.append(del);row.append(actions);
        }
        root.append(row);
      }
    }
    const signal=$('historical-cycle-signal'),assessment=group.signal;
    signal.hidden=activeMode!=='current'||!assessment?.probability;
    if(!signal.hidden){
      $('historical-cycle-signal-title').textContent=`주가 피봇 가능성 ${assessment.probability}%`;
      $('historical-cycle-signal-text').textContent=`시장 관련 확정 ${assessment.marketRelevantCount}개 · 구조 신호 ${assessment.structuralOnlyCount}개 · 후보 ${assessment.candidateCount}개 · 감시 ${assessment.watchCount}개를 품질·지속성·시너지와 무효화에 따른 점수 회수까지 반영해 계산했습니다.`;
    }
    drawIndicators(context);
  }
  function drawIndicators(context){const selectedCode=selection.snapshot().selected,item=visibleIndicators.find(candidate=>candidate.meta.code===selectedCode),selected=item?[displayItem(item,context)]:[];chart?.setIndicators?.(selected);chart?.setIndicatorPointClick?.(isAdmin&&activeMode==='history'?openManualPivotModal:null);$('historical-indicator-clear').disabled=!selected.length;const legend=$('historical-indicator-legend');legend.replaceChildren();legend.hidden=!selected.length;if(selected.length){const label=document.createElement('span'),colors=chart?.indicatorColors?.()||new Map(),anchors=anchorSummary(selected[0]),nearMisses=nearMissSummary(selected[0]);label.style?.setProperty('--indicator-color',colors.get(selectedCode));label.textContent=`${selected[0].meta.title}${anchors.length?` · ${anchors.join('/')}`:''}`;legend.append(label);}renderDetail(selected[0]);}
  function clearIndicatorSelection(){if(!selection)return;selection.clear();document.querySelectorAll('input[name="historical-indicator"]').forEach(input=>{input.checked=false;});if(activeIndicatorContext)drawIndicators(activeIndicatorContext);}
  function renderDetail(item){const root=$('historical-indicator-detail'),hasStored=Boolean(item?.storedPivots?.length||item?.manualPivots?.length),hasAB=Array.isArray(item?.pivots)&&item.pivots.some(pivot=>['A','B','C'].includes(String(pivot?.grade||'').toUpperCase())),hasD=Boolean(item?.reviewPivots?.length);if(!item||activeMode==='history'&&!hasStored&&!item.results?.length&&!hasAB&&!hasD){root.hidden=true;root.replaceChildren();return;}root.hidden=false;root.replaceChildren();const heading=document.createElement('div');heading.className='historical-indicator-detail-heading';const title=document.createElement('strong'),metaLine=document.createElement('span');title.textContent=item.meta.title;metaLine.textContent=`${item.meta.category} · ${item.meta.frequencyLabel} · ${item.meta.unit} · 관측일 원자료 기준`;heading.append(title,metaLine);root.append(heading);if(activeMode==='current'){const evidence=item.evidence||{},grid=document.createElement('div'),card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p'),synergy=evidence.synergyGroup?.length?` · 시너지 +${evidence.synergyBonus}점 (${evidence.synergyGroup.map(peer=>peer.title).join(', ')})`:'';grid.className='historical-indicator-result-grid is-current';if(evidence.signalState==='market_relevant_confirmed'){const pivot=evidence.pivot||[...(item.confirmedReferences||[])].sort((a,b)=>b.score-a.score)[0],extreme=evidence.activeTrend;label.textContent='MARKET RELEVANT · 시장 기준점 관련 확정';body.textContent=`구조 경계 ${pivot.pivotDate} · 확인 ${pivot.confirmationDate}\n${regimeLabel(pivot.previousRegime)} → ${regimeLabel(pivot.nextRegime)} · 현재 극값 ${extreme.currentExtremeDate}\n기본 ${Math.round(evidence.baseScore)}점 · 현재 ${Math.round(evidence.score)}점`;}else if(evidence.status==='structural_only'){const pivot=evidence.pivot,extreme=evidence.activeTrend;label.textContent='STRUCTURAL ONLY · 구조 신호 유지';body.textContent=`구조 경계 ${pivot.pivotDate} · 확인 ${pivot.confirmationDate}\n${regimeLabel(pivot.previousRegime)} → ${regimeLabel(pivot.nextRegime)} · 현재 극값 ${extreme.currentExtremeDate}\n구조 품질 ${Math.round(evidence.signalQuality)}점 · 최신 구조 ${Math.round(evidence.provisionalBaseScore??evidence.baseScore)}점${synergy}${evidence.confirmedBaseScore?` · 시장 확정 ${Math.round(evidence.confirmedBaseScore)}점`:``} · 현재 ${Math.round(evidence.score)}점`;}else if(evidence.pending){const pending=evidence.pending;label.textContent=evidence.status==='candidate'?'CANDIDATE · 구조 피봇 후보':'WATCH · 조정 감시';body.textContent=`후보 경계 ${pending.candidateDate} · 감시 ${pending.monitoringDays}일\n현재 극값 ${pending.currentExtremeDate} · ${window.MacroWatchFrontend.formatDisplayNumber(pending.currentExtremeValue,{maximumFractionDigits:item.meta.decimals})} ${item.meta.unit}\n구조 품질 ${Math.round(pending.structuralQuality||0)}점 · 최신 후보 ${Math.round(evidence.provisionalBaseScore??evidence.baseScore)}점${synergy}${evidence.confirmedBaseScore?` · 시장 확정 ${Math.round(evidence.confirmedBaseScore)}점`:``} · 현재 ${Math.round(evidence.score)}점\n무효화 조건 ${pending.invalidationCondition}`;}else{card.classList.add('is-empty');label.textContent='WATCHING · 신호 없음';body.textContent='현재 확인 중인 추세 경계가 없습니다.';}card.append(label,body);grid.append(card);root.append(grid);const confirmed=anchorSummary(item);if(confirmed.length){const note=document.createElement('p');note.className='historical-indicator-note';note.textContent=`시장 기준점 관련 확정: ${confirmed.join(' · ')} · 각 기준점 -3개월~+1개월 안의 구조 피봇만 반영`;root.append(note);}if(evidence.invalidations?.length){const latestInvalidation=evidence.invalidations.at(-1),note=document.createElement('p');note.className='historical-indicator-note';note.textContent=`최근 무효화/교체: ${latestInvalidation.invalidationDate} · ${latestInvalidation.reason}`;root.append(note);}return;}if(hasStored){const grid=document.createElement('div');grid.className='historical-indicator-result-grid historical-pivot-detail-grid';for(const type of referenceOrder){const result=(item.displayPivots||[]).find(pivot=>(pivot.selectedReferences||[]).some(ref=>ref.type===type)),card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p');card.classList.toggle('is-empty',!result);if(result){const ref=(result.selectedReferences||[]).find(value=>value.type===type);label.textContent=`${type} · ${ref?.offsetDays===0?'기준점 당일':`기준점 ${Math.abs(ref?.offsetDays||0)}일 ${(ref?.offsetDays||0)<0?'전':'후'}`}`;body.textContent=`기준점 ${ref?.date||''}\n피봇점 ${result.pivotDate} · 수치 ${indicatorValue(result,item.meta)}${result.isManual?`\n${relationshipLabel(result.relationship)}`:''}`;card.append(label,body);const reasonBox=document.createElement('div'),reasonLabel=document.createElement('b'),reasonText=document.createElement('p');reasonBox.className='historical-pivot-reason';reasonLabel.textContent='피봇 판정 근거';reasonText.textContent=pivotReasonFor(item,result)||'근거 미저장';reasonBox.append(reasonLabel,reasonText);card.append(reasonBox);}else{label.textContent=`${type} · 공식 범위 내 피봇 없음`;body.textContent='기준점 -3개월 ~ +1개월 안에 저장된 피봇이 없습니다.';card.append(label,body);}grid.append(card);}root.append(grid);appendAuxiliaryPivotReasons(root,item);return;}const grid=document.createElement('div');grid.className='historical-indicator-result-grid historical-pivot-detail-grid';for(const type of referenceOrder){const result=item.byReference?.[type],card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p');card.classList.toggle('is-empty',!result);label.textContent=result?`${type} · ${timingLabel(result)} · 종합 점수 ${Math.round(result.score)}점`:`${type} · 의미 있는 피봇 없음`;body.textContent=result?`기준점 ${result.referenceDate} · ${transitionLabel(result)}\n피봇점 ${result.pivotDate} · 수치 ${indicatorValue(result,item.meta)} · 확정일 ${result.confirmationDate}\n피봇 유효성 ${Math.round(result.structuralScore)}점 · 피봇 타이밍 ${Math.round(result.timingScore)}점 · 추세 지속성 ${result.durationScore==null?'계산 대기':`${Math.round(result.durationScore)}점`}\n${relationshipLabel(result.relationship)} · 관계 신뢰도 ${Math.round(result.relationshipConfidence*100)}% · 관계 보너스 +${window.MacroWatchFrontend.formatDisplayNumber(result.relationshipBonus,{maximumFractionDigits:1})}`:'허용 탐색창 안에 확인된 구조 피봇이 없습니다.';card.append(label,body);if(result){const reason=pivotReasonFor(item,result),reasonBox=document.createElement('div'),reasonLabel=document.createElement('b'),reasonText=document.createElement('p');reasonBox.className='historical-pivot-reason';reasonLabel.textContent='피봇 판정 근거';reasonText.textContent=reason||'근거 미저장 · 재분석 필요';reasonBox.append(reasonLabel,reasonText);card.append(reasonBox);}grid.append(card);}root.append(grid);appendAuxiliaryPivotReasons(root,item);appendDReviews(root,item);}
  async function refreshIndicators(token){if(!indicatorRepository||!selection)return;indicatorLoading();try{const context=await calculateIndicatorContext();if(token!==requestToken)return;renderIndicators(context);}catch(error){if(token!==requestToken)return;indicatorLoading('비교 지표를 불러오지 못했습니다.');console.error('[Historical indicators]',error);}}
  async function render(code){const token=++requestToken;setMarket(code);meta.textContent=`${activeCase?.name||'Historical Case'} · ${indexData?.indices[code]||code}`;state('loading','사이클 데이터 불러오는 중');chart?.setIndicators?.([]);try{if(!indexData||!cycleData||!window.MacroWatchHistoricalChart||!window.MacroWatchFrontend)throw new Error('화면 모듈을 불러오지 못했습니다.');if(!chart)chart=window.MacroWatchHistoricalChart.create(host);chart.setData([]);const rows=await indexRepository.load(code);if(token!==requestToken)return;const activeCycle=cycleData.marketCycle(activeCase,code),metrics=cycleData.calculate(activeCase,activeCycle,rows);activeRows=rows;chart.setData(rows);if(!rows.length){state('empty','저장된 지수 데이터가 없습니다.');return;}chart.setCycle(cycleData.chartPoints(activeCycle,rows));focusCase();showCycle(activeCase,activeCycle,metrics);const count=window.MacroWatchFrontend.formatDisplayNumber(rows.length,{locale:'ko-KR'});state('ready',`${count}개 · ${rows[0].time} ~ ${rows.at(-1).time}`);await refreshIndicators(token);if(token===requestToken)focusCase();}catch(error){if(token!==requestToken)return;chart?.destroy();chart=null;activeRows=[];state('error','사이클 데이터를 불러오지 못했습니다. 다시 시도해 주세요.');console.error('[Historical Insight]',error);}}
  function selectCase(code){const item=code===currentModel?.code?currentModel:cases.find(candidate=>candidate.code===code);if(!item)return;const caseChanged=Boolean(activeCase&&activeCase.code!==item.code);if(caseChanged){clearIndicatorSelection();activeIndicatorContext=null;visibleIndicators=[];}if(isHistoricalCase(item))activeHistoricalCode=item.code;activeCase=item;activeCaseButton();return render(item.primaryIndex);}
  function updateModeAvailability(){const hasHistorical=cases.some(isHistoricalCase);for(const button of modeButtons)button.disabled=button.dataset.historicalMode==='history'&&!hasHistorical;}
  function closeCurrentNameEditor(){const form=$('historical-current-name-form');form.hidden=true;$('historical-current-name-status').textContent='';}
  async function saveAnchors(values,output){if(!isAdmin||!activeCase)return;const savedCode=activeCase.code;output.textContent='저장 중';try{const rows=await indexRepository.load(activeCode);cycleData.calculate(activeCase,{...cycleData.marketCycle(activeCase,activeCode),...values},rows);await caseRepository.save(savedCode,activeCode,values,currentUser.id);cases=await caseRepository.load();const updated=cases.find(item=>item.code===savedCode);analysisCache.clear();rebuildCurrentModel();renderCaseList();updateModeAvailability();if(isHistoricalCase(updated))activeHistoricalCode=updated.code;await setMode(isCurrentCase(updated)?activeMode:'history');output.textContent='저장 완료';}catch(error){output.textContent=`저장 오류: ${error?.message||'알 수 없는 오류'}`;}}
  function setMode(mode){const available=cases.filter(isHistoricalCase);if(mode==='history'&&!available.length)return;activeMode=mode;for(const button of modeButtons){const selected=button.dataset.historicalMode===mode;button.classList.toggle('is-active',selected);button.setAttribute('aria-selected',String(selected));}const currentMode=mode==='current';$('historical-stage').classList.toggle('is-current-mode',currentMode);$('historical-past-sidebar').hidden=currentMode;$('historical-current-sidebar').hidden=!currentMode;$('historical-toolbar-title').textContent=currentMode?'현재 국면 차트':'과거 국면 차트';closeCurrentNameEditor();const desired=currentMode?rebuildCurrentModel():available.find(item=>item.code===activeHistoricalCode)||available[0];if(currentMode){$('historical-current-case-name').textContent=desired.name;$('historical-current-case-state').textContent=currentSource?'진행 중':'상시 관찰';$('historical-current-name-edit').hidden=!isAdmin;}return selectCase(desired.code);}
  async function initialize(){try{if(!indexData||!cycleData||!window.MacroWatchFrontend)throw new Error('화면 모듈을 불러오지 못했습니다.');const client=window.macroWatchSupabase||window.MacroWatchFrontend.createSupabaseClient();if(!client)throw new Error('데이터 연결을 확인해 주세요.');window.macroWatchSupabase=client;indexRepository=indexData.createRepository(client);caseRepository=cycleData.createRepository(client);if(indicatorData)indicatorRepository=indicatorData.createRepository(client);const {data:authData,error:authError}=await client.auth.getSession();if(authError)throw authError;currentUser=authData.session?.user||null;if(!currentUser)throw new Error('로그인이 필요합니다.');const [{data:account,error:accountError},loadedCases,loadedSettings,hiddenCodes]=await Promise.all([client.from('user_accounts').select('is_admin').eq('user_id',currentUser.id).maybeSingle(),caseRepository.load(),caseRepository.loadCurrentSettings(),indicatorRepository?indicatorRepository.loadVisibility():Promise.resolve([])]);isAdmin=!accountError&&account?.is_admin===true;hiddenIndicatorCodes=new Set(hiddenCodes);functionClient=window.MacroWatchFrontend.createFunctionClient(client);$('historical-case-add').hidden=!isAdmin;$('historical-indicator-add').hidden=!isAdmin;cases=loadedCases;currentSettings=loadedSettings;rebuildCurrentModel();renderCaseList();const historicalCases=cases.filter(isHistoricalCase);updateModeAvailability();setMode(historicalCases.length?'history':'current');}catch(error){state('error','Historical Case를 불러오지 못했습니다. 다시 시도해 주세요.');console.error('[Historical Insight]',error);}}
  modeButtons.forEach(button=>button.addEventListener('click',()=>setMode(button.dataset.historicalMode)));marketButtons.forEach(button=>button.addEventListener('click',()=>activeCase&&render(button.dataset.historicalIndex)));
  $('historical-case-add').addEventListener('click',()=>openCaseModal());
  $('historical-case-modal-close').addEventListener('click',closeCaseModal);
  $('historical-case-cancel').addEventListener('click',closeCaseModal);
  $('historical-case-auto-analyze').addEventListener('click',previewHistoricalCase);
  $('historical-case-form').addEventListener('submit',saveHistoricalCase);
  $('historical-case-delete-cancel').addEventListener('click',()=>{deleteCaseCode=null;$('historical-case-delete-modal').hidden=true;});
  $('historical-case-delete-confirm').addEventListener('click',confirmDeleteCase);
  const manualReasonSelect=$('historical-manual-pivot-reason');
  manualReasonSelect.add(new Option('선택 근거를 고르세요',''));
  for(const reason of MANUAL_PIVOT_REASONS)manualReasonSelect.add(new Option(reason,reason));
  $('historical-manual-pivot-is-key').addEventListener('change',event=>{
    const reference=$('historical-manual-pivot-reference');reference.disabled=!event.target.checked;
    if(!event.target.checked)reference.value='';
  });
  $('historical-manual-pivot-close').addEventListener('click',closeManualPivotModal);
  $('historical-manual-pivot-cancel').addEventListener('click',closeManualPivotModal);
  $('historical-manual-pivot-form').addEventListener('submit',event=>{event.preventDefault();persistManualPivot(false);});
  $('historical-manual-pivot-delete').addEventListener('click',()=>{
    if(manualPivotContext?.existing&&window.confirm('이 관리자 변곡점을 삭제하시겠습니까? 자동 재실행으로 복원되지 않습니다.'))persistManualPivot(true);
  });
  $('historical-indicator-add').addEventListener('click',event=>{if(!isAdmin)return;event.stopPropagation();const menu=$('historical-indicator-hidden-menu');renderHiddenIndicatorMenu();menu.hidden=!menu.hidden;});
  $('historical-indicator-clear').addEventListener('click',clearIndicatorSelection);
  document.addEventListener('click',event=>{const menu=$('historical-indicator-hidden-menu'),button=$('historical-indicator-add');if(!menu?.hidden&&!menu.contains(event.target)&&event.target!==button)closeHiddenIndicatorMenu();});
  $('historical-cycle-form').addEventListener('submit',event=>{event.preventDefault();saveAnchors({startDate:$('historical-start-date').value,peakDate:$('historical-peak-date').value,troughDate:$('historical-trough-date').value},$('historical-cycle-save-status'));});
  $('historical-current-anchor-form').addEventListener('submit',event=>{event.preventDefault();saveAnchors({startDate:$('historical-current-start-date').value,peakDate:$('historical-current-peak-date').value,troughDate:$('historical-current-trough-date').value},$('historical-current-anchor-save-status'));});
  $('historical-current-name-edit').addEventListener('click',()=>{if(!isAdmin)return;const form=$('historical-current-name-form');$('historical-current-name-input').value=activeCase?.name||currentSettings.currentName;form.hidden=false;$('historical-current-name-input').focus();});
  $('historical-current-name-cancel').addEventListener('click',closeCurrentNameEditor);
  $('historical-current-name-form').addEventListener('submit',async event=>{event.preventDefault();if(!isAdmin)return;const output=$('historical-current-name-status'),input=$('historical-current-name-input');output.textContent='저장 중';try{const saved=await caseRepository.saveCurrentName(currentSource?.code||null,input.value,currentUser.id);if(currentSource){cases=await caseRepository.load();}else currentSettings={currentName:saved};analysisCache.clear();rebuildCurrentModel();activeCase=currentModel;$('historical-current-case-name').textContent=saved;meta.textContent=`${saved} · ${indexData.indices[activeCode]||activeCode}`;closeCurrentNameEditor();}catch(error){output.textContent=`저장 오류: ${error?.message||'알 수 없는 오류'}`;}});
  retry.addEventListener('click',()=>activeCase?render(activeCode):initialize());fullRange.addEventListener('click',()=>chart?.fit());caseRange.addEventListener('click',focusCase);window.addEventListener('pagehide',()=>{++requestToken;chart?.destroy();chart=null;});window.addEventListener('pageshow',event=>{if(event.persisted)activeCase?render(activeCode):initialize();});initialize();
})();
