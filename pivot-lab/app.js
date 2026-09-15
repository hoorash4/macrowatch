(() => {
  'use strict';

  const SOURCE_TABLE='economic_chart_series_points';
  const PAGE_SIZE=1000;
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

  async function fetchAllPoints(code){
    const rows=[];
    for(let from=0;;from+=PAGE_SIZE){
      const {data,error}=await client.from(SOURCE_TABLE).select('observation_date,value').eq('series_code',code).order('observation_date',{ascending:true}).range(from,from+PAGE_SIZE-1);
      if(error)throw error;
      rows.push(...(data||[]));
      if(!data||data.length<PAGE_SIZE)break;
    }
    return rows.map(row=>({time:String(row.observation_date).slice(0,10),value:Number(row.value)})).filter(row=>Number.isFinite(row.value));
  }

  function visibleBounds(instance,points){
    const x=instance?.scales?.x;
    if(!x)return {start:0,end:points.length-1};
    const start=Number.isFinite(x.min)?Math.floor(x.min):0;
    const end=Number.isFinite(x.max)?Math.ceil(x.max):points.length-1;
    return {start:Math.max(0,start),end:Math.min(points.length-1,end)};
  }

  function refreshPivots(meta,points,instance=chart){
    if(!instance||!points.length)return;
    const bounds=visibleBounds(instance,points);
    const result=engine.detect(points,{startIndex:bounds.start,endIndex:bounds.end});
    const highs=result.pivots.filter(item=>item.type==='high');
    const lows=result.pivots.filter(item=>item.type==='low');
    const path=result.pivots.map(item=>({x:item.date,y:item.value}));

    instance.data.datasets[1].data=path;
    instance.data.datasets[2].data=highs.map(item=>({x:item.date,y:item.value}));
    instance.data.datasets[3].data=lows.map(item=>({x:item.date,y:item.value}));
    instance.update('none');

    elements.pivotCount.textContent=`피봇 ${result.pivots.length.toLocaleString('ko-KR')}개`;
    const threshold=formatValue(result.threshold,meta.decimals);
    setStatus(`현재 화면 ${result.startDate||'-'} ~ ${result.endDate||'-'} · 탐지반경 ${result.radius}포인트 · 유효변화 ${threshold}${meta.unit?` ${meta.unit}`:''} · 피봇 ${result.pivots.length}개`);
  }

  function scheduleRefresh(meta,points,instance){
    requestAnimationFrame(()=>refreshPivots(meta,points,instance));
  }

  function draw(meta,points){
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
            pan:{enabled:true,mode:'x',onPanComplete:({chart:instance})=>scheduleRefresh(meta,points,instance)},
            zoom:{wheel:{enabled:true,speed:.08},pinch:{enabled:true},mode:'x',onZoomComplete:({chart:instance})=>scheduleRefresh(meta,points,instance)}
          }
        },
        scales:{x:{type:'category',grid:{display:false},ticks:{maxTicksLimit:12}},y:{grid:{color:'rgba(148,163,184,.12)'},ticks:{callback:value=>formatValue(value,meta.decimals)}}}
      }
    });
    elements.canvas.ondblclick=()=>{
      chart?.resetZoom();
      scheduleRefresh(meta,points,chart);
    };
    scheduleRefresh(meta,points,chart);
  }

  async function selectSeries(code){
    const meta=catalog.find(item=>item.code===code);if(!meta)return;
    const token=++loadToken;activeCode=code;renderList(elements.search.value);
    elements.title.textContent=meta.title;elements.meta.textContent=`${meta.code} · ${meta.frequencyLabel} · ${meta.category} · ${meta.unit}`;
    elements.pointCount.textContent='데이터 불러오는 중';elements.pivotCount.textContent='피봇 계산 중';setStatus('전체 시계열을 불러오는 중입니다.');
    try{
      const {data:{session}}=await client.auth.getSession();
      if(!session)throw new Error('로그인이 필요합니다. MacroWatch에 로그인한 뒤 이 페이지를 다시 열어 주세요.');
      const points=await fetchAllPoints(code);
      if(token!==loadToken)return;
      elements.pointCount.textContent=`데이터 ${points.length.toLocaleString('ko-KR')}개`;
      if(!points.length){setStatus('이 지표의 저장된 시계열이 없습니다.',true);elements.pivotCount.textContent='피봇 -';if(chart){chart.destroy();chart=null;}return;}
      draw(meta,points);
    }catch(error){if(token!==loadToken)return;setStatus(error?.message||String(error),true);elements.pointCount.textContent='데이터 -';elements.pivotCount.textContent='피봇 -';}
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
