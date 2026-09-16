(() => {
  'use strict';

  const FUNCTION_NAME='pivot-lab-engine';
  const DEBOUNCE_MS=280;
  const config=window.MACROWATCH_CONFIG;
  const registry=window.MacroWatchEconomicSeriesRegistry;
  if(!config?.supabaseUrl||!config?.supabasePublishableKey||!registry?.allSeries)throw new Error('Pivot Lab 초기화에 필요한 설정을 불러오지 못했습니다.');

  const client=window.supabase.createClient(config.supabaseUrl,config.supabasePublishableKey,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}});
  const elements={list:document.getElementById('series-list'),search:document.getElementById('search'),count:document.getElementById('series-count'),title:document.getElementById('series-title'),meta:document.getElementById('series-meta'),status:document.getElementById('status'),pointCount:document.getElementById('point-count'),pivotCount:document.getElementById('pivot-count'),canvas:document.getElementById('chart')};
  const catalog=[...registry.allSeries];
  const cache=new Map();
  let activeCode=null,chart=null,loadToken=0,recalcToken=0,recalcTimer=null;

  const setStatus=(text,error=false)=>{elements.status.textContent=text;elements.status.classList.toggle('error',error);};
  const formatValue=(value,decimals=2)=>Number(value).toLocaleString('ko-KR',{maximumFractionDigits:decimals,minimumFractionDigits:0});
  const cacheKey=(code,startDate,endDate)=>`${code}|${startDate||''}|${endDate||''}`;

  async function invokeAnalyzer({seriesCode,startDate=null,endDate=null,includePoints=false}){
    const {data,error}=await client.functions.invoke(FUNCTION_NAME,{body:{series_code:seriesCode,start_date:startDate,end_date:endDate,include_points:includePoints}});
    if(error)throw error;
    if(data?.error)throw new Error(data.error);
    return {...(data||{}),startDate:data?.start_date||startDate||data?.points?.[0]?.time||null,endDate:data?.end_date||endDate||data?.points?.at?.(-1)?.time||null};
  }

  function visibleDates(instance,points){
    const x=instance?.scales?.x;
    if(!x||!points.length)return {startDate:points[0]?.time||null,endDate:points.at(-1)?.time||null};
    const start=Math.max(0,Math.floor(Number.isFinite(x.min)?x.min:0));
    const end=Math.min(points.length-1,Math.ceil(Number.isFinite(x.max)?x.max:points.length-1));
    return {startDate:points[start]?.time||points[0]?.time||null,endDate:points[end]?.time||points.at(-1)?.time||null};
  }

  function applyPivots(result,meta,instance=chart){
    if(!instance)return;
    const pivots=Array.isArray(result?.pivots)?result.pivots:[];
    const highs=pivots.filter(item=>item.type==='high');
    const lows=pivots.filter(item=>item.type==='low');
    instance.data.datasets[1].data=pivots.map(item=>({x:item.date,y:item.value}));
    instance.data.datasets[2].data=highs.map(item=>({x:item.date,y:item.value}));
    instance.data.datasets[3].data=lows.map(item=>({x:item.date,y:item.value}));
    instance.update('none');
    elements.pivotCount.textContent=`피봇 ${pivots.length.toLocaleString('ko-KR')}개`;
    const d=result?.diagnostics||{};
    const parts=[`현재 화면 ${result?.startDate||'-'} ~ ${result?.endDate||'-'}`,`피봇 ${pivots.length}개`];
    if(Number.isFinite(d.major))parts.push(`주요 ${d.major}`);
    if(Number.isFinite(d.deviation))parts.push(`이격 ${d.deviation}`);
    if(Number.isFinite(d.sidewaysZones))parts.push(`횡보 ${d.sidewaysZones}`);
    setStatus(`백엔드 계산 · ${parts.join(' · ')}`);
  }

  async function recalc(meta,points,instance=chart){
    if(!instance||!points.length||!activeCode)return;
    const {startDate,endDate}=visibleDates(instance,points);
    const key=cacheKey(activeCode,startDate,endDate);
    const cached=cache.get(key);
    if(cached){applyPivots(cached,meta,instance);return;}

    const token=++recalcToken;
    setStatus(`현재 화면 ${startDate||'-'} ~ ${endDate||'-'} · 백엔드에서 피봇 계산 중…`);
    try{
      const result=await invokeAnalyzer({seriesCode:activeCode,startDate,endDate,includePoints:false});
      if(token!==recalcToken||activeCode!==meta.code)return;
      cache.set(key,result);
      applyPivots(result,meta,instance);
    }catch(error){
      if(token!==recalcToken)return;
      setStatus(error?.message||String(error),true);
    }
  }

  function scheduleRecalc(meta,points,instance=chart){
    clearTimeout(recalcTimer);
    recalcTimer=setTimeout(()=>recalc(meta,points,instance),DEBOUNCE_MS);
  }

  function draw(meta,points,initialResult){
    if(chart)chart.destroy();
    chart=new Chart(elements.canvas.getContext('2d'),{
      type:'line',
      data:{labels:points.map(row=>row.time),datasets:[
        {label:meta.title,data:points.map(row=>row.value),borderWidth:1.5,pointRadius:0,tension:0,borderColor:'#38a9f4'},
        {label:'Pivot path',data:[],borderWidth:3,pointRadius:0,tension:0,spanGaps:true,borderColor:'rgba(245,158,11,.68)'},
        {type:'scatter',label:'HIGH',data:[],pointRadius:6,pointHoverRadius:8,backgroundColor:'#f59e0b',borderColor:'#f59e0b'},
        {type:'scatter',label:'LOW',data:[],pointRadius:6,pointHoverRadius:8,backgroundColor:'#22c55e',borderColor:'#22c55e'}
      ]},
      options:{
        responsive:true,maintainAspectRatio:false,animation:false,interaction:{mode:'nearest',intersect:false},
        plugins:{
          legend:{display:false},
          tooltip:{callbacks:{label(ctx){return `${ctx.dataset.label}: ${formatValue(ctx.parsed.y,meta.decimals)}`;}}},
          zoom:{
            limits:{x:{min:'original',max:'original'}},
            pan:{enabled:true,mode:'x',onPanComplete:({chart:instance})=>scheduleRecalc(meta,points,instance)},
            zoom:{wheel:{enabled:true,speed:.08},pinch:{enabled:true},mode:'x',onZoomComplete:({chart:instance})=>scheduleRecalc(meta,points,instance)}
          }
        },
        scales:{x:{type:'category',grid:{display:false},ticks:{maxTicksLimit:12}},y:{grid:{color:'rgba(148,163,184,.12)'},ticks:{callback:value=>formatValue(value,meta.decimals)}}}
      }
    });
    applyPivots(initialResult,meta,chart);
    elements.canvas.ondblclick=()=>{chart?.resetZoom();scheduleRecalc(meta,points,chart);};
  }

  async function selectSeries(code){
    const meta=catalog.find(item=>item.code===code);if(!meta)return;
    const token=++loadToken;
    activeCode=code;recalcToken++;clearTimeout(recalcTimer);cache.clear();renderList(elements.search.value);
    elements.title.textContent=meta.title;elements.meta.textContent=`${meta.code} · ${meta.frequencyLabel} · ${meta.category} · ${meta.unit}`;
    elements.pointCount.textContent='데이터 불러오는 중';elements.pivotCount.textContent='피봇 계산 중';setStatus('백엔드에서 시계열과 피봇을 계산 중입니다.');
    try{
      const {data:{session}}=await client.auth.getSession();
      if(!session)throw new Error('로그인이 필요합니다. MacroWatch에 로그인한 뒤 이 페이지를 다시 열어 주세요.');
      const result=await invokeAnalyzer({seriesCode:code,includePoints:true});
      if(token!==loadToken||activeCode!==code)return;
      const points=Array.isArray(result?.points)?result.points.map(row=>({time:String(row.time),value:Number(row.value)})).filter(row=>Number.isFinite(row.value)):[];
      elements.pointCount.textContent=`데이터 ${points.length.toLocaleString('ko-KR')}개`;
      if(!points.length){setStatus('이 지표의 저장된 시계열이 없습니다.',true);elements.pivotCount.textContent='피봇 -';if(chart){chart.destroy();chart=null;}return;}
      cache.set(cacheKey(code,result.startDate,result.endDate),result);
      draw(meta,points,result);
    }catch(error){
      if(token!==loadToken)return;
      setStatus(error?.message||String(error),true);elements.pointCount.textContent='데이터 -';elements.pivotCount.textContent='피봇 -';
    }
  }

  function renderList(query=''){
    const q=String(query).trim().toLowerCase();
    const visible=catalog.filter(item=>!q||`${item.title} ${item.code} ${item.category}`.toLowerCase().includes(q));
    elements.list.replaceChildren(...visible.map(item=>{
      const button=document.createElement('button');button.type='button';button.className=`series-btn${item.code===activeCode?' active':''}`;
      const title=document.createElement('span');title.className='series-title';title.textContent=item.title;
      const sub=document.createElement('span');sub.className='series-sub';sub.textContent=`${item.code} · ${item.frequencyLabel} · ${item.category}`;
      button.append(title,sub);button.addEventListener('click',()=>selectSeries(item.code));return button;
    }));
  }

  elements.count.textContent=String(catalog.length);
  elements.search.addEventListener('input',event=>renderList(event.target.value));
  renderList();
})();
