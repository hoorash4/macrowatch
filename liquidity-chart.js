(() => {
  'use strict';
  const utils = window.MacroWatchAnalysisChart;
  const states = new Map();
  const names = { pressure: '유동성 압력', capacity: '유동성 여력' };
  const colors = { pressure: '#b4535d', capacity: '#2563a8' };
  const DAY = 86400000;
  function withTrend(rows) {
    return rows.map((row, i) => {
      const window = rows.slice(Math.max(0, i - 2), i + 1);
      const consecutive = window.length === 3 && window.every((r, j) => {
        const d = new Date(row.observation_date);
        d.setUTCMonth(d.getUTCMonth() - 2 + j);
        return r.observation_date.slice(0, 7) === d.toISOString().slice(0, 7);
      });
      return {...row, trend: consecutive ? window.reduce((sum,r)=>sum+Number(r.score),0)/3 : null};
    });
  }
  function visible(rows, years, end) {
    if (years === 'max') return rows;
    const start = new Date(end); start.setUTCFullYear(start.getUTCFullYear() - Number(years));
    return rows.filter(r => Date.parse(r.observation_date) >= start.getTime());
  }
  function render(card, state) {
    const host = card.querySelector('[data-liquidity-charts]');
    if (!state.rows.length) { host.textContent = '아직 저장된 유동성 자료가 없습니다.'; return; }
    const end = Math.max(...state.rows.map(r => Date.parse(r.observation_date)));
    const all = visible(state.rows, state.years, end);
    const start = state.years === 'max' ? Math.min(...all.map(r => Date.parse(r.observation_date))) : (() => {
      const d = new Date(end); d.setUTCFullYear(d.getUTCFullYear() - Number(state.years)); return d.getTime();
    })();
    const width = 900, height = 240, left = 38, right = 15, top = 15, bottom = 30;
    const x = d => left + (Date.parse(d) - start) / Math.max(DAY, end - start) * (width - left - right);
    const y = value => top + (100 - value) / 100 * (height - top - bottom);
    host.innerHTML = ['pressure', 'capacity'].map(metric => {
      const rows = all.filter(r => r.metric === metric);
      const last = state.rows.filter(r => r.metric === metric).at(-1);
      if (!last) return `<p>${names[metric]} 자료가 없습니다.</p>`;
      const frequency = { D: '일별', W: '주간', M: '월별' }[last.frequency];
      const labelDate = last.frequency === 'M' ? last.observation_date.slice(0, 7) : last.observation_date;
      const grids = [0,25,50,75,100].map(v => `<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" stroke="#dce5ef"/><text x="${left-8}" y="${y(v)+4}" text-anchor="end" fill="#64748b" font-size="11">${v}</text>`).join('');
      const ticks = Array.from({length:5}, (_,i) => {
        const d = new Date(start+(end-start)*i/4).toISOString().slice(0,10);
        return `<text x="${x(d)}" y="${height-7}" text-anchor="${i===0?'start':i===4?'end':'middle'}" fill="#64748b" font-size="11">${d.slice(0,7)}</text>`;
      }).join('');
      // Break genuinely missing periods instead of drawing across source outages.
      const points = [], trendPoints = []; let previous = null;
      rows.forEach(r => {
        const time = Date.parse(r.observation_date), maxGap = r.frequency === 'M' ? 45 : r.frequency === 'W' ? 15 : 10;
        if (previous !== null && time - previous > maxGap * DAY) {
          points.push({x:NaN,y:NaN}); trendPoints.push({x:NaN,y:NaN});
        }
        trendPoints.push({x:x(r.observation_date), y:r.trend === null ? NaN : y(r.trend)});
        points.push({x:x(r.observation_date),y:y(Number(r.score))}); previous = time;
      });
      const stale = end - Date.parse(last.observation_date) > ({D:10,W:21,M:120}[last.frequency])*DAY;
      return `<div class="mt-4"><div class="flex flex-wrap justify-between gap-2 text-sm"><h3 style="color:${colors[metric]}">${names[metric]}</h3><span class="text-slate-500">${labelDate} · ${last.trend === null ? "3개월 표본 대기" : last.trend.toFixed(1)+" / 100 · 3개월 평균"}${stale?' · 갱신 지연':''}</span></div><svg data-metric="${metric}" viewBox="0 0 ${width} ${height}" style="width:100%;min-height:150px" role="img" aria-label="${names[metric]} ${frequency} 추이"><title>${names[metric]}: ${labelDate}, ${Number(last.score).toFixed(1)}점</title>${grids}${ticks}<path d="${utils.monotonePath(points)}" fill="none" stroke="${colors[metric]}" stroke-width="1.3" opacity="0.3"/><path d="${utils.monotonePath(trendPoints)}" fill="none" stroke="${colors[metric]}" stroke-width="3"/><line data-cursor x1="0" x2="0" y1="${top}" y2="${height-bottom}" stroke="#64748b" visibility="hidden"/></svg><p class="text-xs text-slate-500">옅은 선: 월별 점수 · 굵은 선: 최근 3개월 평균</p><p data-detail="${metric}" class="text-xs text-slate-500" style="min-height:1.3em">${rows.some(r=>r.is_warmup)?'초기 표본이 적은 구간 포함 · 날짜를 가리키면 표본 수 표시':'날짜를 가리키면 점수와 표본 수 표시'}</p></div>`;
    }).join('');
    host.querySelectorAll('svg[data-metric]').forEach(svg => {
      const rows = all.filter(r => r.metric === svg.dataset.metric);
      svg.addEventListener('pointermove', event => {
        if (!rows.length) return;
        const rect = svg.getBoundingClientRect(), px = (event.clientX-rect.left)/rect.width*width;
        const nearest = rows.reduce((a,b)=>Math.abs(x(a.observation_date)-px)<Math.abs(x(b.observation_date)-px)?a:b);
        const line = svg.querySelector('[data-cursor]');
        line.setAttribute('x1',x(nearest.observation_date)); line.setAttribute('x2',x(nearest.observation_date)); line.setAttribute('visibility','visible');
        host.querySelector(`[data-detail="${svg.dataset.metric}"]`).textContent = `${nearest.frequency==='M'?nearest.observation_date.slice(0,7):nearest.observation_date} · 월별 ${Number(nearest.score).toFixed(1)}점 · 3개월 평균 ${nearest.trend === null ? "대기" : nearest.trend.toFixed(1)+"점"} · 표본 ${nearest.sample_count}개${nearest.is_warmup?' · 초기 표본 부족':''}`;
      });
      svg.addEventListener('pointerleave',()=>svg.querySelector('[data-cursor]').setAttribute('visibility','hidden'));
    });
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
      state.rows=['pressure','capacity'].flatMap(metric=>withTrend(state.rows.filter(r=>r.metric===metric)));
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

