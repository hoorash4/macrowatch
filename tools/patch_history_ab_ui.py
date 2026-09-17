from pathlib import Path
import re

js_path = Path('assets/js/historical-insight/historical-insight.js')
js = js_path.read_text()
needle = "name.textContent=item.meta.title;label.append(input,name);if(activeMode==='current'){"
replacement = "name.textContent=item.meta.title;label.append(input,name);if(activeMode==='history'&&Number(item.overallScore)>0){const score=document.createElement('em');score.className='historical-indicator-score';score.textContent=`${Math.round(item.overallScore)}점`;label.append(score);}if(activeMode==='current'){"
if needle in js:
    js = js.replace(needle, replacement, 1)
elif "activeMode==='history'&&Number(item.overallScore)>0" not in js:
    raise SystemExit('historical score anchor not found')

helper_anchor = "  const pivotReasonFor=(item,result)=>{const date=String(result?.pivotDate||'').slice(0,10),pivot=(item?.pivots||[]).find(candidate=>String(candidate?.date||'').slice(0,10)===date&&['A','B'].includes(String(candidate?.grade||'').toUpperCase()));return String(pivot?.reason||'').trim();};\n"
helper = helper_anchor + "  function appendRemainingABReasons(root,item){const matched=new Set((item?.results||[]).map(result=>String(result?.pivotDate||'').slice(0,10))),pivots=(item?.pivots||[]).filter(pivot=>['A','B'].includes(String(pivot?.grade||'').toUpperCase())&&!matched.has(String(pivot?.date||'').slice(0,10))).sort((a,b)=>String(a?.date||'').localeCompare(String(b?.date||'')));if(!pivots.length)return;const section=document.createElement('section'),title=document.createElement('strong'),grid=document.createElement('div');section.className='historical-ab-reason-section';title.className='historical-ab-reason-title';title.textContent='나머지 A/B 판정 근거';grid.className='historical-ab-reason-grid';for(const pivot of pivots){const card=document.createElement('article'),head=document.createElement('strong'),body=document.createElement('p'),grade=String(pivot?.grade||'').toUpperCase();head.textContent=`${grade} · ${pivot.date||'날짜 없음'} · ${pivot.direction||'neutral'} · ${pivot.type||'pivot'}`;body.textContent=String(pivot.reason||'').trim()||'근거 미저장 · 재분석 필요';card.append(head,body);grid.append(card);}section.append(title,grid);root.append(section);}\n"
if helper_anchor in js and 'function appendRemainingABReasons' not in js:
    js = js.replace(helper_anchor, helper, 1)

old = "grid.append(card);}root.append(grid);}\n  async function refreshIndicators"
new = "grid.append(card);}root.append(grid);appendRemainingABReasons(root,item);}\n  async function refreshIndicators"
if old in js:
    js = js.replace(old, new, 1)
elif 'appendRemainingABReasons(root,item);' not in js:
    raise SystemExit('historical detail tail not found')
js_path.write_text(js)

css_path = Path('assets/css/historical-insight.css')
css = css_path.read_text()
extra = '''\n\n/* Remaining A/B audit reasons under matched pivot details. */\n.historical-ab-reason-section { margin-top: 14px; padding-top: 13px; border-top: 1px solid var(--theme-border); }\n.historical-ab-reason-title { display:block; margin-bottom:8px; color:var(--theme-text-secondary); font-size:11px; font-weight:850; letter-spacing:.02em; }\n.historical-ab-reason-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }\n.historical-ab-reason-grid article { padding:10px 12px; border:1px solid var(--theme-border); border-radius:8px; background:var(--theme-surface); }\n.historical-ab-reason-grid article > strong { display:block; color:var(--theme-text-primary); font-size:11px; line-height:1.45; }\n.historical-ab-reason-grid article > p { margin:5px 0 0; color:var(--theme-text-secondary); font-size:11px; line-height:1.6; }\n@media(max-width:850px){.historical-ab-reason-grid{grid-template-columns:1fr}}\n'''
if 'Remaining A/B audit reasons under matched pivot details.' not in css:
    css += extra
css_path.write_text(css)

html_path = Path('historical-insight.html')
html = html_path.read_text()
html = re.sub(r'assets/css/historical-insight\.css\?v=\d+', 'assets/css/historical-insight.css?v=25', html)
html = re.sub(r'assets/js/historical-insight/historical-insight\.js\?v=\d+', 'assets/js/historical-insight/historical-insight.js?v=33', html)
html_path.write_text(html)

wf_path = Path('.github/workflows/historical-pivot-ai-backfill.yml')
wf = wf_path.read_text().splitlines()
if len(wf) > 1 and wf[1].startswith('# rerun'):
    wf[1] = '# rerun US10Y2Y after pivot-schema-v4 deployment'
else:
    wf.insert(1, '# rerun US10Y2Y after pivot-schema-v4 deployment')
wf_path.write_text('\n'.join(wf) + '\n')
