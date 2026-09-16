(() => {
  'use strict';

  const config=window.MACROWATCH_CONFIG;
  const registry=window.MacroWatchEconomicSeriesRegistry;
  const engine=window.PivotLabEngine;
  if(!config?.supabaseUrl||!config?.supabasePublishableKey||!registry?.allSeries||!engine?.detect)throw new Error('Pivot Lab 초기화에 필요한 설정을 불러오지 못했습니다.');

  const client=window.supabase.createClient(config.supabaseUrl,config.supabasePublishableKey,{auth:{persistSession:true,autoRefreshToken:true,detectSessionInUrl:true}});
  const elements={list:document.getElementById('series-list'),search:document.getElementById('search'),count:document.getElementById('series-count'),title:document.getElementById('series-title'),meta:document.getElementById('series-meta'),status:document.getElementById('status'),pointCount:document.getElementById('point-count'),pivotCount:document.getElementById('pivot-count'),canvas:document.getElementById('chart')};
  const catalog=[...registry.allSeries];
  let activeCode=null,chart=null,loadToken=0;

  const setStatus=(text,error=false)=>{elements.status.textContent=text;elements.status.classList.toggle('error',error);};
  const formatValue=(value,decimals=2)=>Number(value).toLocaleString('ko-KR',{maximumFractionDigits:decimals,minimumFractionDigits:0});

  async function fetchSeries(code){
    const rows=[];const pageSize=1000;
    for(let from=0;;from+=pageSize){
      const {data,error}=await client.from('economic_chart_series_points').select('observation_date,value').eq('series_code',code).order('observation_date',{ascending:true}).range(from,from+pageSize-1);
      if(error)throw error;
      rows.push(...(data||[]));
      if(!data||data.length<pageSize)break;
    }
    return rows.map(row=>({time:String(row.observation_date).slice(0,10),value:Number(row.value)})).filter(row=>Number.isFinite(row.value));
  }

  function applyPivots(result,meta,instance=chart){
    if(!instance)return;
    const pivots=Array.isArray(result?.pivots)?result.pivots:[];
    const path=Array.isArray(result?.path)&&result.path.length?result.path:pivots;
    const highs=pivots.filter(item=>item.type==='high');
    const lows=pivots.filter(item=>item.type==='low');
    instance.data.datasets[1].data=path.map(item=>({x:item.date,y:item.value}));
    instance.data.datasets[2].data=highs.map(item=>({x:item.date,y:item.value}));
    instance.data.datasets[3].data=lows.map(item=>({x:item.date,y:item.value}));
    instance.update('none');
    elements.pivotCount.textContent=`피봇 ${pivots.length.toLocaleString('ko-KR')}개`;
    const d=result?.diagnostics||{};
    const parts=[`고정 스케일 1회 계산`,`피봇 ${pivots.length}개`];
    if(Number.isFinite(d.major))parts.push(`주요 ${d.major}`);
    if(Number.isFinite(d.deviation))parts.push(`이격 ${d.deviation}`);
    if(Number.isFinite(d.sidewaysZones))parts.push(`횡보 ${d.sidewaysZones}`);
    if(Number.isFinite(d.sampled)&&Number.isFinite(d.raw))parts.push(`계산 ${d.sampled}/${d.raw}`);
    if(d.engineVersion)parts.push(d.engineVersion);
    setStatus(parts.join(' · '));
  }

  function draw(meta,points,result){
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
          zoom:{limits:{x:{min:'original',max:'original'}},pan:{enabled:true,mode:'x'},zoom:{wheel:{enabled:true,speed:.08},pinch:{enabled:true},mode:'x'}}
        },
        scales:{x:{type:'category',grid:{display:false},ticks:{maxTicksLimit:12}},y:{grid:{color:'rgba(148,163,184,.12)'},ticks:{callback:value=>formatValue(value,meta.decimals)}}}
      }
    });
    applyPivots(result,meta,chart);
    elements.canvas.ondblclick=()=>chart?.resetZoom();
  }

  async function selectSeries(code){
    const meta=catalog.find(item=>item.code===code);if(!meta)return;
    const token=++loadToken;activeCode=code;renderList(elements.search.value);
    elements.title.textContent=meta.title;elements.meta.textContent=`${meta.code} · ${meta.frequencyLabel} · ${meta.category} · ${meta.unit}`;
    elements.pointCount.textContent='데이터 불러오는 중';elements.pivotCount.textContent='피봇 계산 중';setStatus('전체 기간 데이터를 불러온 뒤 브라우저에서 한 번만 계산합니다.');
    try{
      const {data:{session}}=await client.auth.getSession();
      if(!session)throw new Error('로그인이 필요합니다. MacroWatch에 로그인한 뒤 이 페이지를 다시 열어 주세요.');
      const points=await fetchSeries(code);
      if(token!==loadToken||activeCode!==code)return;
      elements.pointCount.textContent=`데이터 ${points.length.toLocaleString('ko-KR')}개`;
      if(!points.length){setStatus('이 지표의 저장된 시계열이 없습니다.',true);elements.pivotCount.textContent='피봇 -';if(chart){chart.destroy();chart=null;}return;}
      const started=performance.now();
      const result=engine.detect(points);
      const elapsed=Math.round(performance.now()-started);
      draw(meta,points,result);
      setStatus(`${elements.status.textContent} · ${elapsed.toLocaleString('ko-KR')}ms`);
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