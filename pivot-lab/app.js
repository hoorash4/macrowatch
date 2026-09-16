(() => {
  'use strict';

  const config=window.MACROWATCH_CONFIG;
  const registry=window.MacroWatchEconomicSeriesRegistry;
  if(!config?.supabaseUrl||!config?.supabasePublishableKey||!registry?.allSeries)throw new Error('Pivot Lab 초기화에 필요한 설정을 불러오지 못했습니다.');

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

  function draw(meta,points){
    if(chart)chart.destroy();
    chart=new Chart(elements.canvas.getContext('2d'),{
      type:'line',
      data:{labels:points.map(row=>row.time),datasets:[
        {label:meta.title,data:points.map(row=>row.value),borderWidth:1.5,pointRadius:0,pointHoverRadius:4,tension:0,borderColor:'rgba(56,169,244,.95)'}
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
    elements.canvas.ondblclick=()=>chart?.resetZoom();
  }

  async function selectSeries(code){
    const meta=catalog.find(item=>item.code===code);if(!meta)return;
    const token=++loadToken;activeCode=code;renderList(elements.search.value);
    elements.title.textContent=meta.title;elements.meta.textContent=`${meta.code} · ${meta.frequencyLabel} · ${meta.category} · ${meta.unit}`;
    elements.pointCount.textContent='데이터 불러오는 중';elements.pivotCount.textContent='원시값';setStatus('저장된 원시 시계열을 그대로 불러옵니다.');
    try{
      const {data:{session}}=await client.auth.getSession();
      if(!session)throw new Error('로그인이 필요합니다. MacroWatch에 로그인한 뒤 이 페이지를 다시 열어 주세요.');
      const points=await fetchSeries(code);
      if(token!==loadToken||activeCode!==code)return;
      elements.pointCount.textContent=`데이터 ${points.length.toLocaleString('ko-KR')}개`;
      if(!points.length){setStatus('이 지표의 저장된 시계열이 없습니다.',true);if(chart){chart.destroy();chart=null;}return;}
      draw(meta,points);
      setStatus('가공 없이 저장된 원시 시계열만 표시 중');
    }catch(error){
      if(token!==loadToken)return;
      setStatus(error?.message||String(error),true);elements.pointCount.textContent='데이터 -';elements.pivotCount.textContent='원시값';
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