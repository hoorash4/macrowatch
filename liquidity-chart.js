(() => {
  'use strict';
  const utils = window.MacroWatchAnalysisChart;
  const states = new Map();
  const names = { pressure: '유동성 압력', capacity: '유동성 여력' };
  const colors = { pressure: '#b4535d', capacity: '#2563a8' };
  const DAY = 86400000;
  function render(card, state) {
    const host = card.querySelector('[data-liquidity-charts]');
    if (!state.rows.length) { host.textContent = '아직 저장된 유동성 자료가 없습니다.'; return; }
    const rows = state.rows;
    const end = Math.max(...rows.map(r=>Date.parse(r.observation_date)));
    const start = Math.min(...rows.map(r=>Date.parse(r.observation_date)));
    const width = utils.timelineWidth(900,start,end,state.years), height = 280;
    const left=38,right=15,top=32,bottom=32;
    const x = d=>left+(Date.parse(d)-start)/Math.max(DAY,end-start)*(width-left-right);
    const y = value=>top+(100-value)/100*(height-top-bottom);
    const grid = [0,25,50,75,100].map(v=>`<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" stroke="#e2e8f0"/><text x="${left-8}" y="${y(v)+4}" text-anchor="end" fill="#64748b" font-size="11">${v}</text>`).join('');
    const tickCount=Math.max(5,Math.ceil(width/180));
    const ticks=Array.from({length:tickCount},(_,i)=>{
      const d=new Date(start+(end-start)*i/(tickCount-1)).toISOString().slice(0,10);
      return `<text x="${x(d)}" y="${height-7}" text-anchor="${i===0?'start':i===tickCount-1?'end':'middle'}" fill="#64748b" font-size="11">${d.slice(0,7)}</text>`;
    }).join('');
    const paths=['pressure','capacity'].map(metric=>{
      const points=[]; let previous=null;
      rows.filter(r=>r.metric===metric).forEach(r=>{
        const time=Date.parse(r.observation_date);
        if(previous!==null && time-previous>45*DAY) points.push({x:NaN,y:NaN});
        points.push({x:x(r.observation_date),y:y(Number(r.score))}); previous=time;
      });
      return `<path d="${utils.monotonePath(points)}" fill="none" stroke="${colors[metric]}" stroke-width="2.5"/>`;
    }).join('');
    const latest=['pressure','capacity'].map(metric=>{
      const r=rows.filter(row=>row.metric===metric).at(-1);
      return r ? `<span style="color:${colors[metric]}">${names[metric]} ${Number(r.score).toFixed(1)} · ${r.observation_date.slice(0,7)}</span>` : '';
    }).join(' ');
    host.innerHTML=`<div class="flex flex-wrap gap-x-5 gap-y-1 text-sm mb-2">${latest}</div><svg viewBox="0 0 ${width} ${height}" style="height:${height}px;display:block;background:#fff" role="img" aria-label="유동성 압력과 여력 월별 추이">${grid}${ticks}${paths}<line data-cursor y1="${top}" y2="${height-bottom}" stroke="#64748b" visibility="hidden"/><text data-cursor-value y="17" font-size="12" fill="#334155" visibility="hidden"></text><text data-cursor-date y="${height-3}" font-size="12" fill="#334155" visibility="hidden"></text></svg><p class="text-xs text-slate-500 mt-2">월별 점수 · 압력이 높을수록 자금조달 긴장, 여력이 높을수록 자금 기반이 풍부합니다.</p>`;
    const svg=host.querySelector('svg');
    utils.scrollableSvg(svg,width,900);
    const dates=[...new Set(rows.map(r=>r.observation_date))].sort();
    svg.addEventListener('pointermove',event=>{
      const rect=svg.getBoundingClientRect(), px=(event.clientX-rect.left)/rect.width*width;
      const nearest=dates.reduce((a,b)=>Math.abs(x(a)-px)<Math.abs(x(b)-px)?a:b);
      const cursorX=x(nearest), anchor=cursorX<left+140?'start':cursorX>width-right-140?'end':'middle';
      const line=svg.querySelector('[data-cursor]');
      line.setAttribute('x1',cursorX); line.setAttribute('x2',cursorX); line.setAttribute('visibility','visible');
      const value=svg.querySelector('[data-cursor-value]'),dateLabel=svg.querySelector('[data-cursor-date]');
      [value,dateLabel].forEach(label=>{label.setAttribute('x',cursorX);label.setAttribute('text-anchor',anchor);label.setAttribute('visibility','visible');});
      value.textContent=['pressure','capacity'].map(metric=>{
        const row=rows.find(r=>r.metric===metric && r.observation_date===nearest);
        return `${metric==='pressure'?'압력':'여력'} ${row?Number(row.score).toFixed(1):'미발표'}`;
      }).join(' · ');
      dateLabel.textContent=nearest.slice(0,7);
    });
    svg.addEventListener('pointerleave',()=>svg.querySelectorAll('[data-cursor],[data-cursor-value],[data-cursor-date]').forEach(el=>el.setAttribute('visibility','hidden')));
  }
  async function load({supabaseClient}) {
    if (!supabaseClient) return;
    await Promise.all([...document.querySelectorAll('[data-liquidity-country]')].map(async card => {
      const country = card.dataset.liquidityCountry;
      const state = states.get(country) || {rows:[],years:2}; states.set(country,state);
      const {data,error} = await utils.loadAllRows((from,to)=>supabaseClient.from('liquidity_indices')
        .select('observation_date,metric,score,frequency,sample_count,is_warmup').eq('country',country).eq('method_version','liquidity-monthly-v2')
        .order('observation_date').order('metric').range(from,to));
      if (error) { card.querySelector('[data-liquidity-charts]').textContent='유동성 자료를 불러오지 못했습니다. 다시 로그인하거나 잠시 후 새로고침해 주세요.'; return; }
      state.rows=(data||[]).filter(r=>r.score!==null && Number.isFinite(Number(r.score)) && Number.isFinite(Date.parse(r.observation_date)));
      
      render(card,state);
      const controls=card.querySelector('[data-liquidity-ranges]');
      if (!controls.dataset.bound) {
        controls.dataset.bound='true';
        controls.addEventListener('click',event=>{
          const button=event.target.closest('[data-years]'); if (!button) return;
          state.years=button.dataset.years;
          controls.querySelectorAll('[data-years]').forEach(b=>{ b.classList.toggle('is-active',b===button); b.setAttribute('aria-pressed',String(b===button)); });
          render(card,state);
        });
      }
    }));
  }
  window.MacroWatchDashboard?.registerLoader(load);
})();

