(() => {
  'use strict';
  const utils = window.MacroWatchAnalysisChart;
  const states = new Map();
  const names = { pressure: '유동성 압력', capacity: '유동성 여력', environment: '현재 우호도', momentum: '개선·악화 방향' };
  const colors = { pressure: '#b4535d', capacity: '#2563a8', environment: '#2563a8', momentum: '#b7791f' };
  const DAY = 86400000;
  const HEIGHT = 280, MIN_VIEWPORT_WIDTH = 680, Y_AXIS_WIDTH = 52;
  const PADDING = { top: 28, right: 24, bottom: 42, left: 12 };
  const scale = (value, min, max, targetMin, targetMax) => max === min ? (targetMin + targetMax) / 2 : targetMin + ((value - min) / (max - min)) * (targetMax - targetMin);

  function domainFor(points, metrics, includeNeutral = false) {
    const values = points.flatMap(point => metrics.map(metric => point[metric])).filter(Number.isFinite);
    if (includeNeutral) values.push(50);
    const min = Math.min(...values), max = Math.max(...values), padding = Math.max(4, (max - min) * .125);
    const lower = Math.max(0, min - padding), upper = Math.min(100, max + padding);
    const step = Math.max(1, utils.niceStep(Math.max(upper - lower, 4) / 4));
    return { min: Math.floor(lower / step) * step, max: Math.ceil(upper / step) * step, step };
  }

  function render(card, state) {
    const host = card.querySelector('[data-liquidity-charts]');
    const isUS = card.dataset.liquidityCountry === 'US';
    const metrics = isUS ? ['environment', 'momentum'] : ['pressure', 'capacity'];
    const [firstMetric, secondMetric] = metrics;
    if (!state.rows.length) { host.textContent = '아직 저장된 유동성 자료가 없습니다.'; return; }
    const byDate = new Map();
    state.rows.forEach(row => {
      const point = byDate.get(row.observation_date) || { observation_date: row.observation_date, timestamp: Date.parse(row.observation_date) };
      point[row.metric] = Number(row.score); byDate.set(row.observation_date, point);
    });
    const points = [...byDate.values()].sort((a,b)=>a.timestamp-b.timestamp);
    const first = points[0].timestamp, last = points.at(-1).timestamp;
    const viewport = Math.max(MIN_VIEWPORT_WIDTH, (host.clientWidth || MIN_VIEWPORT_WIDTH) - Y_AXIS_WIDTH);
    const width = state.years === 'max' ? viewport : Math.max(viewport, viewport * ((last - first) / (Number(state.years) * 365.25 * DAY)));
    points.forEach(point => { point.x = scale(point.timestamp, first, last, PADDING.left, width - PADDING.right); });
    const pathFor = (key, domain) => utils.monotonePath(points.filter(p=>Number.isFinite(p[key])).map(p=>({x:p.x,y:scale(p[key],domain.min,domain.max,HEIGHT-PADDING.bottom,PADDING.top)})));
    const initial = domainFor(points, metrics, isUS);
    const y = (value, domain) => scale(value,domain.min,domain.max,HEIGHT-PADDING.bottom,PADDING.top);
    const axis = Array.from({length:5},(_,i)=>initial.min+(initial.max-initial.min)*i/4).map((value,index)=>`<text data-liquidity-y-label="${index}" x="${Y_AXIS_WIDTH-8}" y="${y(value,initial)+3}" text-anchor="end" fill="#64748b" font-size="11">${value.toFixed(0)}</text>`).join('');
    const grids = Array.from({length:5},(_,i)=>initial.min+(initial.max-initial.min)*i/4).map((value,index)=>`<line data-liquidity-y-grid="${index}" x1="${PADDING.left}" x2="${width-PADDING.right}" y1="${y(value,initial)}" y2="${y(value,initial)}" stroke="#e2e8f0"/>`).join('');
    const years = Array.from({length:new Date(last).getUTCFullYear()-new Date(first).getUTCFullYear()+1},(_,i)=>new Date(first).getUTCFullYear()+i).map(year=>{
      const timestamp=Date.UTC(year,0,1); if(timestamp<first||timestamp>last) return '';
      return `<line x1="${scale(timestamp,first,last,PADDING.left,width-PADDING.right)}" x2="${scale(timestamp,first,last,PADDING.left,width-PADDING.right)}" y1="${PADDING.top}" y2="${HEIGHT-PADDING.bottom}" stroke="#e2e8f0" stroke-dasharray="3 4"/><text x="${scale(timestamp,first,last,PADDING.left,width-PADDING.right)}" y="${HEIGHT-8}" text-anchor="middle" fill="#64748b" font-size="11">${year}</text>`;
    }).join('');
    const latest=metrics.map(metric=>state.rows.filter(row=>row.metric===metric).at(-1)).filter(Boolean);
    host.innerHTML=`<div class="policy-expectation-chart-layout"><svg class="policy-expectation-y-axis" viewBox="0 0 ${Y_AXIS_WIDTH} ${HEIGHT}" aria-hidden="true">${axis}</svg><div class="policy-expectation-chart-frame"><svg class="policy-expectation-chart-svg" style="width:${width}px;background:#fff" viewBox="0 0 ${width} ${HEIGHT}" role="img" aria-label="${isUS ? '미국 주식시장 자금환경 월별 추이' : '유동성 압력과 여력 월별 추이'}">${years}${grids}${isUS ? `<line data-liquidity-neutral x1="${PADDING.left}" x2="${width-PADDING.right}" y1="${y(50,initial)}" y2="${y(50,initial)}" stroke="#94a3b8" stroke-dasharray="4 4"/>` : ''}<path data-liquidity-pressure d="${pathFor(firstMetric,initial)}" fill="none" stroke="${colors[firstMetric]}" stroke-width="2.5"/><path data-liquidity-capacity d="${pathFor(secondMetric,initial)}" fill="none" stroke="${colors[secondMetric]}" stroke-width="2.5"/><line data-liquidity-cursor x1="0" x2="0" y1="${PADDING.top}" y2="${HEIGHT-PADDING.bottom}" class="policy-expectation-cursor"/><text data-liquidity-value text-anchor="middle" y="16" fill="#334155" font-size="12"></text><text data-liquidity-date text-anchor="middle" y="${HEIGHT-PADDING.bottom+14}" class="policy-expectation-cursor-detail"></text></svg></div></div><div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs"><span style="color:${colors[firstMetric]}">${names[firstMetric]} ${Number(latest.find(r=>r.metric===firstMetric)?.score).toFixed(1)}</span><span style="color:${colors[secondMetric]}">${names[secondMetric]} ${Number(latest.find(r=>r.metric===secondMetric)?.score).toFixed(1)}</span><span class="text-slate-500">${isUS ? '월별 점수 · 우호도는 높을수록 상대적으로 우호적 · 방향은 50 초과 개선, 50 미만 악화' : '월별 점수 · 압력이 높을수록 자금조달 긴장, 여력이 높을수록 자금 기반이 풍부합니다.'}</span></div>`;
    const frame=host.querySelector('.policy-expectation-chart-frame'), svg=host.querySelector('.policy-expectation-chart-svg');
    const lines={[firstMetric]:host.querySelector('[data-liquidity-pressure]'),[secondMetric]:host.querySelector('[data-liquidity-capacity]')};
    const axisLabels=[...host.querySelectorAll('[data-liquidity-y-label]')], axisGrids=[...host.querySelectorAll('[data-liquidity-y-grid]')];
    let animationFrame=null;
    const updateVisibleScale=()=>{
      animationFrame=null;
      const visible=points.filter(point=>point.x>=frame.scrollLeft&&point.x<=frame.scrollLeft+frame.clientWidth);
      if(!visible.length) return;
      const domain=domainFor(visible, metrics, isUS);
      const neutral=host.querySelector('[data-liquidity-neutral]');
      if(neutral) { neutral.setAttribute('y1',y(50,domain)); neutral.setAttribute('y2',y(50,domain)); }
      metrics.forEach(key=>lines[key].setAttribute('d',pathFor(key,domain)));
      axisLabels.forEach((label,index)=>{
        const value=domain.min+(domain.max-domain.min)*index/4;
        label.textContent=value.toFixed(0); label.setAttribute('y',y(value,domain)+3);
      });
      axisGrids.forEach((grid,index)=>{
        const value=domain.min+(domain.max-domain.min)*index/4, py=y(value,domain);
        grid.setAttribute('y1',py);grid.setAttribute('y2',py);
      });
    };
    frame.addEventListener('scroll',()=>{if(animationFrame===null)animationFrame=window.requestAnimationFrame(updateVisibleScale);},{passive:true});
    // 차트가 숨겨진 패널에서 먼저 그려지면 첫 프레임에는 폭이 0일 수 있다.
    // 실제 폭이 확정된 뒤 한 번만 최신 구간(오른쪽 끝)으로 맞춘다.
    const positionAtLatest = () => {
      if (!frame.clientWidth || !frame.scrollWidth) return false;
      frame.scrollLeft = Math.max(0, frame.scrollWidth - frame.clientWidth);
      window.requestAnimationFrame(updateVisibleScale);
      return true;
    };
    window.requestAnimationFrame(() => {
      if (positionAtLatest()) return;
      const observer = new ResizeObserver(() => {
        if (positionAtLatest()) observer.disconnect();
      });
      observer.observe(frame);
    });
    const cursor=host.querySelector('[data-liquidity-cursor]'), value=host.querySelector('[data-liquidity-value]'), dateLabel=host.querySelector('[data-liquidity-date]');
    frame.addEventListener('pointermove',event=>{
      const bounds=svg.getBoundingClientRect(),pointerX=(event.clientX-bounds.left)/bounds.width*width;
      const nearest=points.reduce((closest,point)=>Math.abs(point.x-pointerX)<Math.abs(closest.x-pointerX)?point:closest);
      cursor.setAttribute('x1',nearest.x);cursor.setAttribute('x2',nearest.x);cursor.classList.add('is-visible');
      value.setAttribute('x',nearest.x);dateLabel.setAttribute('x',nearest.x);
      value.textContent=metrics.map(metric=>`${names[metric]} ${Number.isFinite(nearest[metric])?nearest[metric].toFixed(1):'미발표'}`).join(' · ');
      dateLabel.textContent=nearest.observation_date.slice(0,7); value.setAttribute('visibility','visible'); dateLabel.classList.add('is-visible');
    });
    frame.addEventListener('pointerleave',()=>{cursor.classList.remove('is-visible');value.setAttribute('visibility','hidden');dateLabel.classList.remove('is-visible');});
  }
  async function load({supabaseClient}) {
    if (!supabaseClient) return;
    await Promise.all([...document.querySelectorAll('[data-liquidity-country]')].map(async card => {
      const country = card.dataset.liquidityCountry;
      const state = states.get(country) || {rows:[],years:2}; states.set(country,state);
      const {data,error} = await utils.loadAllRows((from,to)=>supabaseClient.from('liquidity_indices')
        .select('observation_date,metric,score,frequency,sample_count,is_warmup').eq('country',country).eq('method_version', country === 'US' ? 'us-equity-environment-v1' : 'liquidity-monthly-v2')
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

