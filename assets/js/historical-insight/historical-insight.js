(() => {
  'use strict';
  const $=id=>document.getElementById(id),host=$('historical-chart-host');if(!host)return;
  const status=$('historical-chart-status'),meta=$('historical-chart-meta'),message=$('historical-chart-message'),retry=$('historical-chart-retry'),fullRange=$('historical-chart-full-range'),caseRange=$('historical-chart-case-range');
  const marketButtons=[...document.querySelectorAll('[data-historical-index]')],modeButtons=[...document.querySelectorAll('[data-historical-mode]')];
  const indexData=window.MacroWatchHistoricalData,cycleData=window.MacroWatchHistoricalCycles,indicatorData=window.MacroWatchHistoricalIndicators,indicatorAnalysis=window.MacroWatchHistoricalIndicatorAnalysis,selectionApi=window.MacroWatchHistoricalIndicatorSelection;
  let activeCode='NASDAQ_COMPOSITE',activeCase=null,activeMode='history',activeHistoricalCode=null,cases=[],currentSettings={currentName:'현재 국면 관찰 중'},currentSource=null,currentModel=null,requestToken=0,indexRepository,caseRepository,indicatorRepository,chart,activeRows=[],currentUser=null,isAdmin=false,functionClient=null,deleteCaseCode=null;
  let analysisCache=new Map(),visibleIndicators=[],selection=selectionApi?.create(),activeIndicatorContext=null,hiddenIndicatorCodes=new Set();
  let manualPivotContext=null,expandedHistoricalCode=null,manualReasonLoadError='';

  async function loadManualReasonPresets(){
    const select=$('historical-manual-pivot-reason');
    try{
      const result=await functionClient.invoke('admin-control',{action:'list_historical_pivot_reason_presets'});
      select.replaceChildren(new Option('선택 근거를 고르세요',''));
      for(const item of result.items||[])select.add(new Option(item.phrase,item.phrase));
      manualReasonLoadError='';
    }catch(error){manualReasonLoadError=`근거 문구 조회 실패: ${error?.message||'알 수 없는 오류'}`;}
  }

  function state(kind,text){host.dataset.state=kind;host.setAttribute('aria-busy',String(kind==='loading'));status.textContent=text;message.textContent=kind==='ready'?'':text;message.hidden=kind==='ready';retry.hidden=kind!=='error';fullRange.disabled=caseRange.disabled=kind!=='ready';}
  function setMarket(code){activeCode=code;for(const button of marketButtons){const selected=button.dataset.historicalIndex===code;button.classList.toggle('is-active',selected);button.setAttribute('aria-selected',String(selected));}}
  function formatPoint(kind,point){const card=document.querySelector(`[data-cycle-point="${kind}"]`);if(!card)return;card.querySelector('strong').textContent=point?point.time:'미확정';card.querySelector('span').textContent=point?window.MacroWatchFrontend.formatDisplayNumber(point.value):'—';}
  const percentage=(value,absolute=false)=>value==null?'—':`${absolute?'':value>0?'+':''}${window.MacroWatchFrontend.formatDisplayNumber(absolute?Math.abs(value):value,{maximumFractionDigits:1})}%`,duration=value=>value==null?'—':`${window.MacroWatchFrontend.formatDisplayNumber(value)}일`;
  function showCurrentAnchors(cycle){if(activeMode!=='current')return;const points=[['start',cycle.startDate],['peak',cycle.peakDate],['trough',cycle.troughDate]],confirmed=points.filter(([,date])=>date).length;$('historical-current-anchor-state').textContent=confirmed===3?'모두 확정':confirmed?`${confirmed}/3 확정`:'미확정';for(const [kind,date] of points)$(`historical-current-${kind}`).textContent=date||'미확정';const editor=$('historical-current-anchor-editor');editor.hidden=!isAdmin||!currentSource;if(isAdmin&&currentSource){for(const [kind,date] of points)$(`historical-current-${kind}-date`).value=date||'';$('historical-current-anchor-save-status').textContent='';}}
  function showCycle(item,cycle,metrics){const panel=$('historical-cycle-panel');panel.hidden=activeMode==='current';$('historical-cycle-state').textContent=cycle.status==='confirmed'?'CONFIRMED CYCLE':cycle.status==='in_progress'?'IN PROGRESS':'DRAFT';$('historical-cycle-name').textContent=item.name;$('historical-cycle-market').textContent=indexData.indices[cycle.indexCode]||cycle.indexCode;$('historical-search-range').textContent=`관찰 범위 ${item.searchStart} ~ ${item.searchEnd||'현재'}`;formatPoint('start',metrics.start);formatPoint('peak',metrics.peak);formatPoint('trough',metrics.trough);const expanded=[...document.querySelectorAll('[data-historical-case-expanded]')].find(node=>node.dataset.historicalCaseExpanded===item.code);if(expanded){expanded.querySelector('[data-cycle-summary]').textContent=item.summary;expanded.querySelector('[data-cycle-rise]').textContent=percentage(metrics.rise);expanded.querySelector('[data-cycle-fall]').textContent=percentage(metrics.fall);expanded.querySelector('[data-cycle-rise-days]').textContent=duration(metrics.riseDays);expanded.querySelector('[data-cycle-fall-days]').textContent=duration(metrics.fallDays);}showCurrentAnchors(cycle);if(isAdmin){$('historical-cycle-editor').hidden=activeMode==='current';$('historical-start-date').value=cycle.startDate||'';$('historical-peak-date').value=cycle.peakDate||'';$('historical-trough-date').value=cycle.troughDate||'';$('historical-cycle-save-status').textContent='';}}
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
  function renderCaseList(){const root=$('historical-case-list');root.replaceChildren();for(const item of cases.filter(isHistoricalCase)){const row=document.createElement('div');row.className='historical-case-row';const button=document.createElement('button');button.type='button';button.className='historical-case';button.dataset.historicalCase=item.code;button.setAttribute('aria-expanded','false');const label=document.createElement('span'),badge=document.createElement('small');label.textContent=item.name;badge.textContent='확정';button.append(label,badge);button.addEventListener('click',()=>{if(activeCase?.code===item.code&&expandedHistoricalCode===item.code){expandedHistoricalCode=null;activeCaseButton();return;}expandedHistoricalCode=item.code;selectCase(item.code);});row.append(button);if(isAdmin){const actions=document.createElement('span');actions.className='historical-case-actions';const edit=document.createElement('button'),del=document.createElement('button');edit.type=del.type='button';edit.innerHTML='<i class="fa-solid fa-pen"></i>';del.innerHTML='<i class="fa-solid fa-trash"></i>';edit.title='수정';del.title='삭제';del.className='is-delete';edit.addEventListener('click',()=>openCaseModal(item));del.addEventListener('click',()=>openDeleteCase(item));actions.append(edit,del);row.append(actions);}const expanded=document.createElement('div');expanded.className='historical-case-expanded';expanded.dataset.historicalCaseExpanded=item.code;expanded.hidden=true;expanded.innerHTML='<div class="historical-case-overview"><span>국면 요약</span><p class="historical-cycle-description" data-cycle-summary></p></div><div class="historical-cycle-performance" aria-label="선택 시장 사이클 성과"><article class="is-rise"><span>상승 구간</span><strong data-cycle-rise>—</strong><small>상승 기간 <b data-cycle-rise-days>—</b></small></article><article class="is-fall"><span>하락 구간</span><strong data-cycle-fall>—</strong><small>하락 기간 <b data-cycle-fall-days>—</b></small></article></div>';row.append(expanded);root.append(row);}}

  function activeCaseButton(){document.querySelectorAll('[data-historical-case]').forEach(button=>{const selected=button.dataset.historicalCase===activeCase?.code,expanded=selected&&button.dataset.historicalCase===expandedHistoricalCode;button.classList.toggle('is-active',selected);button.setAttribute('aria-expanded',String(expanded));button.closest('.historical-case-row').querySelector('[data-historical-case-expanded]').hidden=!expanded;});}
  function focusCase(){if(!chart||!activeRows.length||!activeCase)return;const cycle=cycleData.marketCycle(activeCase,activeCode);const from=cycle.startDate||activeCase.searchStart;const to=cycle.troughDate || cycle.peakDate || activeRows.at(-1).time;chart.focus(from,to,.12);}
  function indicatorLoading(text='비교 지표 분석 중'){const root=$('historical-indicator-list');if(root){root.replaceChildren();const p=document.createElement('p');p.className='historical-indicator-empty';p.textContent=text;root.append(p);}}
  async function mapSeries(metadata,worker){const out=[];for(let i=0;i<metadata.length;i+=6){const batch=await Promise.all(metadata.slice(i,i+6).map(worker));out.push(...batch.filter(Boolean));}return out;}
  async function rebuildHistoricalScores(caseCode,indexCode,seriesCodes){
    for(let i=0;i<seriesCodes.length;i+=6)await functionClient.invoke('historical-score-rebuild',{
      case_code:caseCode,index_code:indexCode,series_codes:seriesCodes.slice(i,i+6)
    });
    indicatorRepository.clearScoreRows(caseCode,indexCode);
  }
  async function calculateIndicatorContext(){const key=`${activeMode}:${activeCase.code}:${activeCode}`;if(analysisCache.has(key))return analysisCache.get(key);const coverage=await indicatorRepository.loadCoverage(),catalog=indicatorRepository.catalog(activeCode).filter(item=>!hiddenIndicatorCodes.has(item.code)),cycle=cycleData.marketCycle(activeCase,activeCode),end=indicatorAnalysis.analysisEnd(activeCase,cycle),latest=activeRows.at(-1)?.time||end,displayRange=indicatorAnalysis.displayWindow(activeCase,cycle,latest);if(activeMode==='history'){const eligible=catalog.filter(item=>{const c=coverage.get(item.code);return c&&c.firstDate<=activeCase.searchStart&&c.lastDate>=end;}),loadFrom=displayRange.from<activeCase.searchStart?displayRange.from:activeCase.searchStart,loadTo=displayRange.to>end?displayRange.to:end;let scores=await indicatorRepository.loadScoreRows(activeCase.code,activeCode);const stale=eligible.filter(item=>scores.get(item.code)?.scoring_version!==indicatorData.SCORE_VERSION).map(item=>item.code);if(stale.length&&isAdmin){await rebuildHistoricalScores(activeCase.code,activeCode,stale);scores=await indicatorRepository.loadScoreRows(activeCase.code,activeCode);}const analyses=await mapSeries(eligible,async item=>{const rows=await indicatorRepository.load(item.code,loadFrom,loadTo),analysis=indicatorData.aiAnalysis(scores.get(item.code),item,rows),[storedPivots,manualPivots]=await Promise.all([indicatorRepository.loadStoredPivots(activeCase.code,activeCase.pivotSourceIndex,item.code),indicatorRepository.loadManualPivots(activeCase.code,activeCode,item.code)]);return Object.freeze({...analysis,storedPivots,manualPivots});}),value=Object.freeze({mode:'history',analyses,end,displayRange,cycle,indexRows:activeRows});analysisCache.set(key,value);return value;}const usable=catalog.filter(item=>coverage.has(item.code)),historyStart=activeCase.searchStart,marketRows=activeRows,rawAnalyses=await mapSeries(usable,async item=>{const rows=await indicatorRepository.load(item.code,historyStart,latest);return indicatorAnalysis.analyzeCurrent(item,rows,historyStart,latest,{item:activeCase,cycle,marketRows});}),analyses=indicatorAnalysis.applyCurrentSynergy(rawAnalyses),value=Object.freeze({mode:'current',analyses,end:latest,displayRange});analysisCache.set(key,value);return value;}
  function candidatesFor(context){if(context.mode==='history')return{items:[...context.analyses].sort((a,b)=>(b.byReference?.LIST?.score??0)-(a.byReference?.LIST?.score??0)||(b.byReference?.LIST?.extraDarkTieBreak??0)-(a.byReference?.LIST?.extraDarkTieBreak??0)),signal:null};const rank={market_relevant_confirmed:0,structural_only:1,candidate:2,watch:3,watching:4},items=[...context.analyses].sort((a,b)=>(rank[a.evidence?.signalState]??5)-(rank[b.evidence?.signalState]??5)||b.evidence.score-a.evidence.score||a.meta.title.localeCompare(b.meta.title,'ko'));return{items,signal:indicatorAnalysis.currentPivotProbability(items)};}
  async function topIndicatorTotals(caseCode){
    const indexCodes=['SP500','NASDAQ_COMPOSITE','KOSPI'];
    const scoreMaps=await Promise.all(indexCodes.map(code=>indicatorRepository.loadScoreRows(caseCode,code)));
    return indicatorRepository.catalog('KOSPI').filter(item=>!hiddenIndicatorCodes.has(item.code)).map(meta=>{
      let score=0,hasScore=false;
      for(const rows of scoreMaps){
        const row=rows.get(meta.code);
        if(row?.scoring_version!==indicatorData.SCORE_VERSION)continue;
        const value=Number(row.by_reference?.LIST?.score);
        if(!Number.isFinite(value))continue;
        score+=value;hasScore=true;
      }
      return hasScore?{meta,score}:null;
    }).filter(Boolean).sort((a,b)=>b.score-a.score);
  }
  function renderTopIndicators(items){
    const panel=$('historical-cycle-top-indicators'),list=$('historical-cycle-top-list');
    list.replaceChildren();
    for(const [index,item] of items.slice(0,3).entries()){
      const row=document.createElement('li'),rank=document.createElement('b'),name=document.createElement('span'),score=document.createElement('strong');
      rank.textContent=String(index+1).padStart(2,'0');name.textContent=item.meta.title;score.textContent=`총 ${item.score}점`;
      row.append(rank,name,score);list.append(row);
    }
    panel.hidden=!list.children.length;
  }
  function manualPivotDirectionMatches(pivot,type,pivots,rows,cycle,indexRows=[]){
    if(!pivot.isManual||!['positive','inverse'].includes(pivot.relationship)||pivot.keyReference)return true;
    if(pivot.designatedReference&&pivot.designatedReference!==type)return false;
    if(!rows?.length)return true;
    const dayDistance=(a,b)=>Math.abs(Date.parse(`${a}T00:00:00Z`)-Date.parse(`${b}T00:00:00Z`));
    const owner=pivot.designatedReference
      ||[['START',cycle.startDate],['PEAK',cycle.peakDate],['TROUGH',cycle.troughDate]]
        .filter(([,date])=>date).sort((a,b)=>dayDistance(a[1],pivot.pivotDate)-dayDistance(b[1],pivot.pivotDate))[0]?.[0]||type;
    const trough=cycle.troughDate,bufferEnd=trough?new Date(`${trough}T00:00:00Z`):null;
    if(bufferEnd){bufferEnd.setUTCDate(1);bufferEnd.setUTCMonth(bufferEnd.getUTCMonth()+24);bufferEnd.setUTCDate(Math.min(Number(trough.slice(8,10)),new Date(Date.UTC(bufferEnd.getUTCFullYear(),bufferEnd.getUTCMonth()+1,0)).getUTCDate()));}
    const end=owner==='START'?cycle.peakDate:owner==='PEAK'?cycle.troughDate:bufferEnd?.toISOString().slice(0,10);
    const next=pivots.find(candidate=>candidate.pivotDate>(owner==='TROUGH'?cycle.troughDate:pivot.pivotDate)&&candidate.pivotDate<=end);
    const to=next?.pivotDate||(owner==='TROUGH'?[...rows].reverse().find(row=>row.time<=end)?.time:end);
    if(!to||to<=pivot.pivotDate)return false;
    const fromValue=rawValueAtDate(rows,pivot.pivotDate),toValue=rawValueAtDate(rows,to);
    if(!Number.isFinite(fromValue)||!Number.isFinite(toValue))return false;
    let direction=Math.sign(toValue-fromValue);
    if(!direction){const previous=[...pivots].reverse().find(candidate=>candidate.pivotDate<pivot.pivotDate);direction=previous?Math.sign(fromValue-previous.pivotValue):0;}
    if(!direction)return true;
    const startIndex=indexRows.find(row=>row.time===cycle.troughDate);
    const endIndex=[...indexRows].reverse().find(row=>row.time<=to);
    const expected=owner==='TROUGH'&&indexRows.length
      ?startIndex&&endIndex&&endIndex.time>cycle.troughDate?Math.sign(endIndex.value-startIndex.value):0
      :owner==='PEAK'?-1:1;
    if(!expected)return false;
    return direction===(pivot.relationship==='positive'?expected:-expected);
  }
  function classifyStoredPivots(pivots,cycle,rows=[],indexRows=[]){
    const refs=[['START',cycle?.startDate],['PEAK',cycle?.peakDate],['TROUGH',cycle?.troughDate]].filter(([,date])=>date);
    const days=(a,b)=>Math.round((Date.parse(`${a}T00:00:00Z`)-Date.parse(`${b}T00:00:00Z`))/86400000);
    const source=[...(pivots||[])];
    const selectedByReference=new Map();
    for(const [type,date] of refs){
      const window=indicatorAnalysis.relevanceWindow(date);
      const candidates=source.filter(pivot=>pivot.pivotDate>=window.from&&pivot.pivotDate<=window.to
        &&(!pivot.designatedReference||pivot.designatedReference===type)
        &&manualPivotDirectionMatches(pivot,type,source,rows,cycle,indexRows))
        .sort((a,b)=>Number(Boolean(b.isManual))-Number(Boolean(a.isManual))||Math.abs(days(a.pivotDate,date))-Math.abs(days(b.pivotDate,date))||a.pivotDate.localeCompare(b.pivotDate)||a.pivotOrder-b.pivotOrder);
      if(candidates.length)selectedByReference.set(type,candidates[0]);
    }
    return source.map(pivot=>{
      const selectedRefs=refs.filter(([type])=>selectedByReference.get(type)===pivot).map(([type,date])=>({type,date,offsetDays:days(pivot.pivotDate,date)}));
      const extendedRefs=pivot.designatedReference?refs.filter(([type])=>type===pivot.designatedReference):refs;
      const extended=extendedRefs.map(([type,date])=>({type,date,window:indicatorAnalysis.nearMissWindow(date),offsetDays:days(pivot.pivotDate,date)}))
        .filter(item=>pivot.pivotDate>=item.window.from&&pivot.pivotDate<=item.window.to
          &&manualPivotDirectionMatches(pivot,item.type,source,rows,cycle,indexRows))
        .sort((a,b)=>Math.abs(a.offsetDays)-Math.abs(b.offsetDays))[0]||null;
      const refDate=pivot.designatedReference?cycle?.[`${pivot.designatedReference.toLowerCase()}Date`]:null;
      const designatedPrimary=pivot.designatedReference&&refDate?{type:pivot.designatedReference,date:refDate,offsetDays:days(pivot.pivotDate,refDate)}:null;
      const primary=selectedRefs[0]||extended||designatedPrimary;
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
  function autoPivotRows(item){return(item.pivots||[]).filter(pivot=>['A','B','C'].includes(String(pivot.grade||'').toUpperCase())).map((pivot,index)=>({pivotOrder:index,pivotDate:String(pivot.date).slice(0,10),pivotValue:Number(pivot.value),pivotType:String(pivot.type||''),selectionReason:String(pivot.reason||'')}));}
  function mergeManualPivots(item,cycle,indexRows=[]){
    const manual=item.manualPivots||[],blocked=new Set(manual.map(pivot=>pivot.sourceDate)),active=manual,occupied=new Set(manual.map(pivot=>pivot.pivotDate).filter(Boolean));
    const automatic=[...new Map((item.storedPivots?.length?item.storedPivots:autoPivotRows(item)).filter(pivot=>!blocked.has(pivot.pivotDate)&&!occupied.has(pivot.pivotDate)).map(pivot=>[pivot.pivotDate,pivot])).values()];
    const merged=[...automatic,...active.map(pivot=>({
      pivotOrder:Number.MAX_SAFE_INTEGER,pivotDate:pivot.pivotDate,pivotValue:pivot.pivotValue,
      selectionReason:[pivot.reason,pivot.comment].filter(Boolean).join('\n'),
      pivotReason:[pivot.reason,pivot.comment].filter(Boolean).join('\n'),
      relationship:pivot.relationship,sourceDate:pivot.sourceDate,keyReference:pivot.keyReference,
      designatedReference:pivot.designatedReference||null,
      isVerified:Boolean(pivot.isVerified),
      keySuppressed:pivot.keySuppressed||Boolean(pivot.designatedReference&&!pivot.keyReference),isManual:true
    }))].sort((a,b)=>a.pivotDate.localeCompare(b.pivotDate));
    const classified=classifyStoredPivots(merged,cycle,item.rows,indexRows),manualKeys=new Map(active.filter(pivot=>pivot.keyReference).map(pivot=>[pivot.keyReference,pivot.sourceDate]));
    const referenceDates={START:cycle?.startDate,PEAK:cycle?.peakDate,TROUGH:cycle?.troughDate};
    return classified.map(pivot=>{
      let selectedReferences=(pivot.selectedReferences||[]).filter(ref=>!pivot.keySuppressed&&(!manualKeys.has(ref.type)||pivot.sourceDate===manualKeys.get(ref.type)));
      if(pivot.isManual&&pivot.keyReference){const date=referenceDates[pivot.keyReference];selectedReferences=[{type:pivot.keyReference,date,offsetDays:date?Math.round((Date.parse(pivot.pivotDate)-Date.parse(date))/86400000):null}];}
      const overridden=pivot.markerStatus==='confirmed'&&!selectedReferences.length;
      let markerStatus;
      if(pivot.isManual&&pivot.keyReference){
        markerStatus='confirmed';
      }else if(pivot.isManual&&pivot.isVerified){
        markerStatus='verified';
      }else if(pivot.isManual){
        markerStatus=selectedReferences.length?'confirmed':pivot.markerStatus==='reference_only'?'reference_only':'manual_standard';
      }else{
        markerStatus=overridden?'overridden_key':selectedReferences.length?'confirmed':pivot.markerStatus;
      }
      return Object.freeze({...pivot,selectedReferences:Object.freeze(selectedReferences),markerStatus});
    });
  }
  function effectivePivots(item,context){let pivots=context.mode==='history'?(item.storedPivots?.length||item.manualPivots?.length?mergeManualPivots(item,context.cycle,context.indexRows||[]):classifyStoredPivots(autoPivotRows(item),context.cycle,item.rows,context.indexRows||[])):[...(item.results||[]),...(item.nearMissPivots||[])];if(context.mode==='current'){const latest=item.evidence?.pending?{pivotDate:item.evidence.pending.candidateDate,regimeBoundaryDate:item.evidence.pending.regimeBoundaryDate,referenceType:'CURRENT_STRUCTURAL',markerStatus:item.evidence.status}:item.evidence?.result||null;pivots=[...(item.confirmedReferences||[]),...(latest?[latest]:[])];}return[...new Map(pivots.map(pivot=>[context.mode==='history'?pivot.pivotDate:`${pivot.markerStatus||''}:${pivot.referenceType||''}:${pivot.pivotDate}`,pivot])).values()];}
  function displayItem(item,context){return{...item,displayRows:indicatorAnalysis.normalizeForDisplay(item.rows,context.displayRange.from,context.displayRange.to),displayPivots:effectivePivots(item,context)};}
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
    const targetDate=String(point.date||'').slice(0,10);
    const manual=(item.manualPivots||[]).find(pivot=>String(pivot.pivotDate||'').slice(0,10)===targetDate);
    const automatic=(item.storedPivots?.length?item.storedPivots:autoPivotRows(item)).find(pivot=>String(pivot.pivotDate||'').slice(0,10)===targetDate);
    const classified=effectivePivots(item,activeIndicatorContext).find(pivot=>String(pivot.pivotDate||'').slice(0,10)===targetDate);
    const designatedReference=manual?.designatedReference||manual?.keyReference||classified?.designatedReference||classified?.referenceType||classified?.selectedReferences?.[0]?.type||'';
    const isKey=manual?.keySuppressed?false:Boolean(manual?.keyReference||classified?.markerStatus==='confirmed');
    const isVerified=!isKey&&Boolean(manual?.isVerified||classified?.markerStatus==='verified');
    const existing=Boolean(manual||automatic);
    manualPivotContext={caseCode:activeCase.code,indexCode:activeCode,seriesCode:point.code,sourceDate:manual?.sourceDate||targetDate,item,existing,keyTouched:false,keyDecision:manual?.keyReference?'manual_on':manual?.isVerified?'manual_verified':manual?.designatedReference?'manual_ref':manual?.keySuppressed?'manual_off':'auto',isKey,isVerified,designatedReference};
    $('historical-manual-pivot-title').textContent=`${item.meta.title} · ${existing?'변곡점 수정':'변곡점 추가'}`;
    $('historical-manual-pivot-date').value=manual?manual.pivotDate:point.date;
    $('historical-manual-pivot-relationship').value=manual?manual.relationship||'':'';
    const reasonSelect=$('historical-manual-pivot-reason'),savedReason=manual?manual.reason||'':'';
    reasonSelect.querySelector?.('option[data-legacy-reason]')?.remove();
    if(savedReason&&reasonSelect.options&&![...reasonSelect.options].some(option=>option.value===savedReason)){
      const option=new Option(savedReason,savedReason);option.dataset.legacyReason='';reasonSelect.add(option);
    }
    reasonSelect.value=savedReason;
    $('historical-manual-pivot-comment').value=manual?manual.comment||'':'';
    $('historical-manual-pivot-is-key').checked=isKey;
    $('historical-manual-pivot-is-verified').checked=isVerified;
    $('historical-manual-pivot-reference').value=designatedReference;
    $('historical-manual-pivot-reference').disabled=false;
    $('historical-manual-pivot-delete').hidden=!existing;
    $('historical-manual-pivot-status').textContent=manualReasonLoadError;
    $('historical-manual-pivot-modal').hidden=false;
    $('historical-manual-pivot-date').focus();
  }
  async function persistManualPivot(isDeleted){
    const context=manualPivotContext;
    if(!isAdmin||!context||!functionClient)return;
    const status=$('historical-manual-pivot-status'),date=$('historical-manual-pivot-date').value,
      isKey=$('historical-manual-pivot-is-key').checked,
      isVerified=$('historical-manual-pivot-is-verified').checked,
      selectedRef=$('historical-manual-pivot-reference').value||'',
      value=isDeleted?null:rawValueAtDate(context.item.rows,date);
    if(!isDeleted&&(!date||!Number.isFinite(value)||(isKey&&!selectedRef))){
      status.textContent='날짜와 해당 날짜의 지표값을 확인해 주세요. 핵심 변곡점을 선택했다면 지수 기준점도 지정해 주세요.';return;
    }
    if(!isDeleted&&isKey&&selectedRef&&(context.item.manualPivots||[]).some(pivot=>pivot.keyReference===selectedRef&&pivot.sourceDate!==context.sourceDate)){
      status.textContent=`이 지표의 ${selectedRef} 핵심 변곡점이 이미 있습니다. 기존 지정을 관리자 화면에서 먼저 해제해 주세요.`;return;
    }
    let sendKeyReference;
    if(isDeleted){
      sendKeyReference=null;
    }else if(context.keyTouched){
      sendKeyReference=isKey?selectedRef:isVerified?(selectedRef?`${selectedRef}_VERIFIED`:'VERIFIED'):selectedRef?`${selectedRef}_REF`:null;
    }else if(context.keyDecision==='manual_off'){
      sendKeyReference=null;
    }else if(context.keyDecision==='manual_on'||(context.isKey&&context.designatedReference)){
      sendKeyReference=context.designatedReference;
    }else if(context.keyDecision==='manual_verified'||context.isVerified){
      sendKeyReference=context.designatedReference?`${context.designatedReference}_VERIFIED`:'VERIFIED';
    }else if(context.keyDecision==='manual_ref'||(!context.isKey&&context.designatedReference)){
      sendKeyReference=`${context.designatedReference}_REF`;
    }else{
      sendKeyReference='AUTO';
    }
    const save=$('historical-manual-pivot-save'),del=$('historical-manual-pivot-delete');
    save.disabled=del.disabled=true;status.textContent='저장 중';
    try{
      await functionClient.invoke('admin-control',{
        action:'save_historical_indicator_manual_pivot',case_code:context.caseCode,index_code:context.indexCode,
        series_code:context.seriesCode,source_date:context.sourceDate,is_deleted:isDeleted,
        pivot_date:isDeleted?null:date,pivot_value:value,
        relationship:isDeleted?null:$('historical-manual-pivot-relationship').value||null,
        reason:isDeleted?null:$('historical-manual-pivot-reason').value||null,
        comment:isDeleted?null:$('historical-manual-pivot-comment').value||null,
        key_reference:sendKeyReference
      });
      indicatorRepository.clearManualPivots(context.caseCode,context.indexCode,context.seriesCode);
      indicatorRepository.clearStoredPivots?.(context.caseCode,context.indexCode,context.seriesCode);
      for(const code of Object.keys(indexData.indices)){
        indicatorRepository.clearScoreRows(context.caseCode,code);
        indicatorRepository.clearStoredPivots?.(context.caseCode,code,context.seriesCode);
      }
      for(const code of Object.keys(indexData.indices))analysisCache.delete(`history:${context.caseCode}:${code}`);
      try{for(const code of Object.keys(indexData.indices))await rebuildHistoricalScores(context.caseCode,code,[context.seriesCode]);}
      catch(error){status.textContent=`변곡점은 저장됐지만 점수 갱신 실패: ${error?.message||'알 수 없는 오류'}`;return;}
      closeManualPivotModal();
      if(activeCase?.code===context.caseCode&&activeCode===context.indexCode&&activeMode==='history')await refreshIndicators(requestToken);
    }catch(error){status.textContent=`저장 오류: ${error?.message||'알 수 없는 오류'}`;}
    finally{save.disabled=del.disabled=false;}
  }
  const referenceOrder=['START','PEAK','TROUGH'];
  const regimeLabel=type=>({rising:'상승',falling:'하락',sideways:'횡보'}[type]||type);
  const relationshipLabel=value=>({positive:'정 관계',inverse:'역 관계',unclear:'정/역 관계 불명확'}[value]||value);
  const indicatorValue=(result,meta)=>`${window.MacroWatchFrontend.formatDisplayNumber(result.pivotValue,{maximumFractionDigits:meta.decimals})}${meta.unit==='%'?'%':` ${meta.unit}`}`;
  const anchorSummary=item=>referenceOrder.filter(type=>item.byReference?.[type]);
  const nearMissSummary=item=>referenceOrder.filter(type=>(item.nearMissPivots||[]).some(pivot=>pivot.referenceType===type));
  function indicatorBadgeSummary(item,context){
    if(context.mode!=='history')return{anchors:anchorSummary(item),nearMisses:nearMissSummary(item)};
    const pivots=effectivePivots(item,context),darkStatuses=new Set(['near_miss','overridden_key','manual_standard']);
    const anchors=referenceOrder.filter(type=>pivots.some(pivot=>{
      const references=Array.isArray(pivot.selectedReferences)?pivot.selectedReferences.map(ref=>ref.type):[pivot.referenceType];
      return !darkStatuses.has(pivot.markerStatus)&&pivot.markerStatus!=='reference_only'&&pivot.markerStatus!=='verified'&&references.includes(type);
    }));
    const dates={START:context.cycle?.startDate,PEAK:context.cycle?.peakDate,TROUGH:context.cycle?.troughDate};
    const nearMisses=referenceOrder.filter(type=>{
      if(!dates[type])return false;
      const window=indicatorAnalysis.nearMissWindow(dates[type]);
      return pivots.some(pivot=>{
        if(!darkStatuses.has(pivot.markerStatus))return false;
        if(pivot.pivotDate<window.from||pivot.pivotDate>window.to)return false;
        const targetRef=pivot.designatedReference||pivot.referenceType;
        return targetRef===type;
      });
    });
    return{anchors,nearMisses};
  }
  const pivotReasonFor=(item,result)=>{if(String(result?.pivotReason||'').trim())return String(result.pivotReason).trim();const date=String(result?.pivotDate||'').slice(0,10),pivot=(item?.pivots||[]).find(candidate=>String(candidate?.date||'').slice(0,10)===date);return String(pivot?.reason||'').trim();};
  function closePivotReviewModal(){document.querySelector('.historical-pivot-review-modal')?.remove();}
  async function resolveDPivot(item,pivot,resolution,button,status){
    if(!isAdmin||!functionClient)return;button.disabled=true;status.textContent='저장 중';
    try{
      await functionClient.invoke('admin-control',{action:'resolve_historical_pivot_review',case_code:activeCase.code,index_code:activeCode,series_code:item.meta.code,pivot_date:pivot.date,resolution});
      await rebuildHistoricalScores(activeCase.code,activeCode,[item.meta.code]);
      indicatorRepository.clearScoreRows(activeCase.code,activeCode);analysisCache.clear();closePivotReviewModal();await render(activeCode);
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
      p.textContent=activeMode==='history'?'이 국면에 표시할 지표 데이터가 없습니다.':'과거 국면에서 의미 있었던 지표가 없습니다.';
      root.append(p);
    }else{
      if(activeMode==='current'){
        const p=document.createElement('p');p.className='historical-indicator-note';
        p.textContent='과거 국면에서 한 번이라도 의미 있었던 전체 지표를 매일 같은 기준으로 스캔합니다.';
        root.append(p);
      }
      for(const item of visibleIndicators){
        const row=document.createElement('div'),label=document.createElement('label'),input=document.createElement('input'),name=document.createElement('span'),{anchors,nearMisses}=indicatorBadgeSummary(item,context),signalState=item.evidence?.signalState;
        row.className='historical-indicator-row';
        input.type='radio';input.name='historical-indicator';input.value=item.meta.code;input.checked=snapshot.selected===item.meta.code;
        input.addEventListener('change',()=>{if(!input.checked)return;selection.select(item.meta.code);drawIndicators(context);focusCase();});
        name.textContent=item.meta.title;label.append(input,name);
        if(activeMode==='history'){
          if(item.reviewPivots?.length){const review=document.createElement('small');review.textContent=`D검토 ${item.reviewPivots.length}`;label.append(review);}
          const score=document.createElement('em');score.className='historical-indicator-score';score.textContent=`${item.byReference?.LIST?.score??0}점`;label.append(score);
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
  function drawIndicators(context){const selectedCode=selection.snapshot().selected,item=visibleIndicators.find(candidate=>candidate.meta.code===selectedCode),selected=item?[displayItem(item,context)]:[];chart?.setIndicators?.(selected);chart?.setIndicatorPointClick?.(isAdmin&&activeMode==='history'?openManualPivotModal:null);$('historical-indicator-clear').disabled=!selected.length;const legend=$('historical-indicator-legend');legend.replaceChildren();legend.hidden=!selected.length;if(selected.length){const label=document.createElement('span'),colors=chart?.indicatorColors?.()||new Map(),{anchors}=indicatorBadgeSummary(selected[0],context);label.style?.setProperty('--indicator-color',colors.get(selectedCode));label.textContent=`${selected[0].meta.title}${anchors.length?` · ${anchors.join('/')}`:''}`;legend.append(label);}if(context.mode==='history')renderHistoricalPivotScores(selected[0],context);else renderDetail(selected[0]);}
  function renderHistoricalPivotScores(item,context){
    const root=$('historical-indicator-detail');
    if(!item){root.hidden=true;root.replaceChildren();return;}
    root.hidden=false;root.replaceChildren();
    const primary=document.createElement('div'),heading=document.createElement('div'),title=document.createElement('strong'),metaLine=document.createElement('span'),grid=document.createElement('div');
    primary.className='historical-indicator-primary';
    heading.className='historical-indicator-detail-heading';title.textContent=item.meta.title;
    metaLine.textContent=`${item.meta.category} · ${item.meta.frequencyLabel} · ${item.meta.unit} · 관측일 원자료 기준`;
    heading.append(title,metaLine);primary.append(heading);
    grid.className='historical-indicator-result-grid historical-pivot-detail-grid';
    const dates={START:context.cycle.startDate,PEAK:context.cycle.peakDate,TROUGH:context.cycle.troughDate};
    function appendCard(target,type,score,isDark){
      const result=score;
      const card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p');
      const hasPivot=Boolean(score?.pivotDate),offset=score?.offsetDays;
      card.classList.toggle('is-empty',!hasPivot);
      card.classList.toggle('is-dark',Boolean(isDark));
      label.textContent=`${type} · ${hasPivot?(offset===0?'기준점 당일':`기준점 ${Math.abs(offset)}일 ${offset<0?'전':'후'}`):'기준점 피봇 없음'} · ${relationshipLabel(score?.relationship||'unclear')} · 종합 점수 ${score?.score??0}점`;
      body.textContent=hasPivot?`기준점 ${dates[type]}\n피봇점 ${score.pivotDate} · 수치 ${indicatorValue(score,item.meta)}\n변곡 시의성 ${score.timelinessScore}점 · 관계 적합성 ${score.relationshipSuitabilityScore}점 · 추세 지속성 ${score.continuityScore}점`
        :`기준점 ${dates[type]}\n변곡 시의성 ${score?.timelinessScore??0}점 · 관계 적합성 ${score?.relationshipSuitabilityScore??0}점 · 추세 지속성 ${score?.continuityScore??0}점`;
      card.append(label,body);
      if(hasPivot){const box=document.createElement('div'),reasonLabel=document.createElement('b'),reasonText=document.createElement('p');box.className='historical-pivot-reason';reasonLabel.textContent='피봇 판정 근거';reasonText.textContent=pivotReasonFor(item,result)||'근거 미저장';box.append(reasonLabel,reasonText);card.append(box);}
      target.append(card);
    }
    function appendVerifiedCard(target,pivot){
      const card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p');
      card.classList.add('is-verified');
      const refType=pivot.designatedReference,refDate=refType&&dates[refType]?dates[refType]:null;
      const offset=refDate?Math.round((Date.parse(pivot.pivotDate)-Date.parse(refDate))/86400000):null;
      const timingText=offset!==null?(offset===0?'기준점 당일':`기준점 ${Math.abs(offset)}일 ${offset<0?'전':'후'}`):'';
      label.textContent=refType?`${refType} 확인 · ${timingText} · ${relationshipLabel(pivot.relationship||'unclear')}`
        :`확인 변곡점 · ${pivot.pivotDate} · ${relationshipLabel(pivot.relationship||'unclear')}`;
      body.textContent=refDate?`기준점 ${refDate}\n피봇점 ${pivot.pivotDate} · 수치 ${indicatorValue(pivot,item.meta)}`
        :`피봇점 ${pivot.pivotDate} · 수치 ${indicatorValue(pivot,item.meta)}`;
      card.append(label,body);
      const reason=pivotReasonFor(item,pivot)||pivot.pivotReason||pivot.selectionReason;
      if(reason){
        const box=document.createElement('div'),reasonLabel=document.createElement('b'),reasonText=document.createElement('p');
        box.className='historical-pivot-reason';reasonLabel.textContent='피봇 판정 근거';reasonText.textContent=reason;
        box.append(reasonLabel,reasonText);card.append(box);
      }
      target.append(card);
    }
    referenceOrder.forEach(type=>appendCard(grid,type,item.byReference?.[type]?.markerStatus==='confirmed'?item.byReference[type]:null,false));
    primary.append(grid);root.append(primary);
    const darkPivots=item.byReference?.LIST?.darkPivots||[];
    if(darkPivots.length){
      const darkHeading=document.createElement('strong'),darkGrid=document.createElement('div');
      darkHeading.className='historical-pivot-dark-heading';darkHeading.textContent='준핵심 변곡점';
      const darkDesc=document.createElement('p');
      darkDesc.className='historical-pivot-dark-desc';
      darkDesc.textContent='지수 기준점과 타이밍은 다소 차이가 있으나, 시장의 방향성을 조기에 예고했거나 사후에 추세를 확증해 준 의미 있는 변곡점입니다.';
      darkGrid.className='historical-indicator-result-grid historical-pivot-detail-grid';
      darkPivots.forEach(score=>appendCard(darkGrid,score.referenceType,score,true));
      root.append(darkHeading,darkDesc,darkGrid);
    }
    const verifiedPivots=(item.displayPivots||(typeof effectivePivots==='function'?effectivePivots(item,context):[])||[]).filter(pivot=>pivot.markerStatus==='verified');
    if(verifiedPivots.length){
      const verifiedHeading=document.createElement('strong'),verifiedDesc=document.createElement('p'),verifiedGrid=document.createElement('div');
      verifiedHeading.className='historical-pivot-verified-heading';verifiedHeading.textContent='확인 변곡점';
      verifiedDesc.className='historical-pivot-verified-desc';
      verifiedDesc.textContent='핵심 기준점은 아니지만, 시장의 추세를 최종 확인시켜 주었거나 전환 신호의 신뢰성을 분명하게 확증해 준 주요 변곡점입니다.';
      verifiedGrid.className='historical-indicator-result-grid historical-pivot-detail-grid';
      verifiedPivots.forEach(pivot=>appendVerifiedCard(verifiedGrid,pivot));
      root.append(verifiedHeading,verifiedDesc,verifiedGrid);
    }
    if(!item.storedPivots?.length&&!item.manualPivots?.length)appendDReviews(root,item);
  }
  function clearIndicatorSelection(){if(!selection)return;selection.clear();document.querySelectorAll('input[name="historical-indicator"]').forEach(input=>{input.checked=false;});if(activeIndicatorContext)drawIndicators(activeIndicatorContext);}
  function renderDetail(item){const root=$('historical-indicator-detail');if(!item){root.hidden=true;root.replaceChildren();return;}root.hidden=false;root.replaceChildren();const heading=document.createElement('div');heading.className='historical-indicator-detail-heading';const title=document.createElement('strong'),metaLine=document.createElement('span');title.textContent=item.meta.title;metaLine.textContent=`${item.meta.category} · ${item.meta.frequencyLabel} · ${item.meta.unit} · 관측일 원자료 기준`;heading.append(title,metaLine);root.append(heading);const evidence=item.evidence||{},grid=document.createElement('div'),card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p'),synergy=evidence.synergyGroup?.length?` · 시너지 +${evidence.synergyBonus}점 (${evidence.synergyGroup.map(peer=>peer.title).join(', ')})`:'';grid.className='historical-indicator-result-grid is-current';if(evidence.signalState==='market_relevant_confirmed'){const pivot=evidence.pivot||[...(item.confirmedReferences||[])].sort((a,b)=>b.score-a.score)[0],extreme=evidence.activeTrend;label.textContent='MARKET RELEVANT · 시장 기준점 관련 확정';body.textContent=`구조 경계 ${pivot.pivotDate} · 확인 ${pivot.confirmationDate}\n${regimeLabel(pivot.previousRegime)} → ${regimeLabel(pivot.nextRegime)} · 현재 극값 ${extreme.currentExtremeDate}\n기본 ${Math.round(evidence.baseScore)}점 · 현재 ${Math.round(evidence.score)}점`;}else if(evidence.status==='structural_only'){const pivot=evidence.pivot,extreme=evidence.activeTrend;label.textContent='STRUCTURAL ONLY · 구조 신호 유지';body.textContent=`구조 경계 ${pivot.pivotDate} · 확인 ${pivot.confirmationDate}\n${regimeLabel(pivot.previousRegime)} → ${regimeLabel(pivot.nextRegime)} · 현재 극값 ${extreme.currentExtremeDate}\n구조 품질 ${Math.round(evidence.signalQuality)}점 · 최신 구조 ${Math.round(evidence.provisionalBaseScore??evidence.baseScore)}점${synergy}${evidence.confirmedBaseScore?` · 시장 확정 ${Math.round(evidence.confirmedBaseScore)}점`:``} · 현재 ${Math.round(evidence.score)}점`;}else if(evidence.pending){const pending=evidence.pending;label.textContent=evidence.status==='candidate'?'CANDIDATE · 구조 피봇 후보':'WATCH · 조정 감시';body.textContent=`후보 경계 ${pending.candidateDate} · 감시 ${pending.monitoringDays}일\n현재 극값 ${pending.currentExtremeDate} · ${window.MacroWatchFrontend.formatDisplayNumber(pending.currentExtremeValue,{maximumFractionDigits:item.meta.decimals})} ${item.meta.unit}\n구조 품질 ${Math.round(pending.structuralQuality||0)}점 · 최신 후보 ${Math.round(evidence.provisionalBaseScore??evidence.baseScore)}점${synergy}${evidence.confirmedBaseScore?` · 시장 확정 ${Math.round(evidence.confirmedBaseScore)}점`:``} · 현재 ${Math.round(evidence.score)}점\n무효화 조건 ${pending.invalidationCondition}`;}else{card.classList.add('is-empty');label.textContent='WATCHING · 신호 없음';body.textContent='현재 확인 중인 추세 경계가 없습니다.';}card.append(label,body);grid.append(card);root.append(grid);const confirmed=anchorSummary(item);if(confirmed.length){const note=document.createElement('p');note.className='historical-indicator-note';note.textContent=`시장 기준점 관련 확정: ${confirmed.join(' · ')} · 각 기준점 -3개월~+1개월 안의 구조 피봇만 반영`;root.append(note);}if(evidence.invalidations?.length){const latestInvalidation=evidence.invalidations.at(-1),note=document.createElement('p');note.className='historical-indicator-note';note.textContent=`최근 무효화/교체: ${latestInvalidation.invalidationDate} · ${latestInvalidation.reason}`;root.append(note);}return;}
  async function refreshIndicators(token){
    if(!indicatorRepository||!selection)return;
    indicatorLoading();
    try{
      const context=await calculateIndicatorContext();
      if(token!==requestToken)return;
      renderIndicators(context);
      if(context.mode==='history'){
        try{
          const totals=await topIndicatorTotals(activeCase.code);
          if(token===requestToken)renderTopIndicators(totals);
        }catch(error){
          if(token===requestToken)console.error('[Historical top indicators]',error);
        }
      }
    }catch(error){
      if(token!==requestToken)return;
      indicatorLoading('비교 지표를 불러오지 못했습니다.');
      console.error('[Historical indicators]',error);
    }
  }
  async function render(code){const token=++requestToken;setMarket(code);$('historical-cycle-top-indicators').hidden=true;meta.textContent=`${activeCase?.name||'Historical Case'} · ${indexData?.indices[code]||code}`;state('loading','사이클 데이터 불러오는 중');chart?.setIndicators?.([]);try{if(!indexData||!cycleData||!window.MacroWatchHistoricalChart||!window.MacroWatchFrontend)throw new Error('화면 모듈을 불러오지 못했습니다.');if(!chart)chart=window.MacroWatchHistoricalChart.create(host);chart.setData([]);const rows=await indexRepository.load(code);if(token!==requestToken)return;const activeCycle=cycleData.marketCycle(activeCase,code),metrics=cycleData.calculate(activeCase,activeCycle,rows);activeRows=rows;chart.setData(rows);if(!rows.length){state('empty','저장된 지수 데이터가 없습니다.');return;}chart.setCycle(cycleData.chartPoints(activeCycle,rows));focusCase();showCycle(activeCase,activeCycle,metrics);const count=window.MacroWatchFrontend.formatDisplayNumber(rows.length,{locale:'ko-KR'});state('ready',`${count}개 · ${rows[0].time} ~ ${rows.at(-1).time}`);await refreshIndicators(token);if(token===requestToken)focusCase();}catch(error){if(token!==requestToken)return;chart?.destroy();chart=null;activeRows=[];state('error','사이클 데이터를 불러오지 못했습니다. 다시 시도해 주세요.');console.error('[Historical Insight]',error);}}
  function selectCase(code){const item=code===currentModel?.code?currentModel:cases.find(candidate=>candidate.code===code);if(!item)return;const caseChanged=Boolean(activeCase&&activeCase.code!==item.code);if(caseChanged){clearIndicatorSelection();activeIndicatorContext=null;visibleIndicators=[];}if(isHistoricalCase(item)){activeHistoricalCode=item.code;if(caseChanged||expandedHistoricalCode===null)expandedHistoricalCode=item.code;}activeCase=item;activeCaseButton();return render(item.primaryIndex);}
  function updateModeAvailability(){const hasHistorical=cases.some(isHistoricalCase);for(const button of modeButtons)button.disabled=button.dataset.historicalMode==='history'&&!hasHistorical;}
  function closeCurrentNameEditor(){const form=$('historical-current-name-form');form.hidden=true;$('historical-current-name-status').textContent='';}
  async function saveAnchors(values,output){if(!isAdmin||!activeCase)return;const savedCode=activeCase.code;output.textContent='저장 중';try{const rows=await indexRepository.load(activeCode);cycleData.calculate(activeCase,{...cycleData.marketCycle(activeCase,activeCode),...values},rows);await caseRepository.save(savedCode,activeCode,values,currentUser.id);cases=await caseRepository.load();const updated=cases.find(item=>item.code===savedCode);analysisCache.clear();rebuildCurrentModel();renderCaseList();updateModeAvailability();if(isHistoricalCase(updated))activeHistoricalCode=updated.code;await setMode(isCurrentCase(updated)?activeMode:'history');output.textContent='저장 완료';}catch(error){output.textContent=`저장 오류: ${error?.message||'알 수 없는 오류'}`;}}
  function setMode(mode){const available=cases.filter(isHistoricalCase);if(mode==='history'&&!available.length)return;activeMode=mode;for(const button of modeButtons){const selected=button.dataset.historicalMode===mode;button.classList.toggle('is-active',selected);button.setAttribute('aria-selected',String(selected));}const currentMode=mode==='current';$('historical-stage').classList.toggle('is-current-mode',currentMode);$('historical-past-sidebar').hidden=currentMode;$('historical-current-sidebar').hidden=!currentMode;$('historical-toolbar-title').textContent=currentMode?'현재 국면 차트':'과거 국면 차트';closeCurrentNameEditor();const desired=currentMode?rebuildCurrentModel():available.find(item=>item.code===activeHistoricalCode)||available[0];if(currentMode){$('historical-current-case-name').textContent=desired.name;$('historical-current-case-state').textContent=currentSource?'진행 중':'상시 관찰';$('historical-current-name-edit').hidden=!isAdmin;}return selectCase(desired.code);}
  async function initialize(){try{if(!indexData||!cycleData||!window.MacroWatchFrontend)throw new Error('화면 모듈을 불러오지 못했습니다.');const client=window.macroWatchSupabase||window.MacroWatchFrontend.createSupabaseClient();if(!client)throw new Error('데이터 연결을 확인해 주세요.');window.macroWatchSupabase=client;indexRepository=indexData.createRepository(client);caseRepository=cycleData.createRepository(client);if(indicatorData)indicatorRepository=indicatorData.createRepository(client);const {data:authData,error:authError}=await client.auth.getSession();if(authError)throw authError;currentUser=authData.session?.user||null;if(!currentUser)throw new Error('로그인이 필요합니다.');const [{data:account,error:accountError},loadedCases,loadedSettings,hiddenCodes]=await Promise.all([client.from('user_accounts').select('is_admin').eq('user_id',currentUser.id).maybeSingle(),caseRepository.load(),caseRepository.loadCurrentSettings(),indicatorRepository?indicatorRepository.loadVisibility():Promise.resolve([])]);isAdmin=!accountError&&account?.is_admin===true;hiddenIndicatorCodes=new Set(hiddenCodes);functionClient=window.MacroWatchFrontend.createFunctionClient(client);if(isAdmin)await loadManualReasonPresets();$('historical-case-add').hidden=!isAdmin;$('historical-indicator-add').hidden=!isAdmin;cases=loadedCases;currentSettings=loadedSettings;rebuildCurrentModel();renderCaseList();const historicalCases=cases.filter(isHistoricalCase);updateModeAvailability();setMode(historicalCases.length?'history':'current');}catch(error){state('error','Historical Case를 불러오지 못했습니다. 다시 시도해 주세요.');console.error('[Historical Insight]',error);}}
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
  $('historical-manual-pivot-is-key').addEventListener('change',e=>{
    if(e.target.checked)$('historical-manual-pivot-is-verified').checked=false;
    if(manualPivotContext)manualPivotContext.keyTouched=true;
  });
  $('historical-manual-pivot-is-verified').addEventListener('change',e=>{
    if(e.target.checked)$('historical-manual-pivot-is-key').checked=false;
    if(manualPivotContext)manualPivotContext.keyTouched=true;
  });
  $('historical-manual-pivot-reference').addEventListener('change',()=>{
    if(manualPivotContext)manualPivotContext.keyTouched=true;
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
