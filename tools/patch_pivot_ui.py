from pathlib import Path

js_path = Path('assets/js/historical-insight/historical-insight.js')
js = js_path.read_text()

start = js.find('  function appendPivotReasons(')
end = js.find('  function renderIndicators(', start)
if start != -1 and end != -1:
    js = js[:start] + js[end:]

anchor = "  const nearMissSummary=item=>referenceOrder.filter(type=>(item.nearMissPivots||[]).some(pivot=>pivot.referenceType===type));\n"
helper = anchor + "  const pivotReasonFor=(item,result)=>{const date=String(result?.pivotDate||'').slice(0,10),pivot=(item?.pivots||[]).find(candidate=>String(candidate?.date||'').slice(0,10)===date&&['A','B'].includes(String(candidate?.grade||'').toUpperCase()));return String(pivot?.reason||'').trim();};\n"
if anchor in js and 'const pivotReasonFor=' not in js:
    js = js.replace(anchor, helper, 1)

old_loop_start = "const grid=document.createElement('div');grid.className='historical-indicator-result-grid';for(const type of referenceOrder){"
old_loop_end = "root.append(grid);appendPivotReasons(root,item);}"
s = js.find(old_loop_start)
e = js.find(old_loop_end, s)
if s == -1 or e == -1:
    raise SystemExit('historical detail loop not found')
e += len(old_loop_end)
replacement = """const grid=document.createElement('div');grid.className='historical-indicator-result-grid historical-pivot-detail-grid';for(const type of referenceOrder){const result=item.byReference?.[type],card=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('p');card.classList.toggle('is-empty',!result);label.textContent=result?`${type} · ${timingLabel(result)} · 종합 점수 ${Math.round(result.score)}점`:`${type} · 의미 있는 피봇 없음`;body.textContent=result?`기준점 ${result.referenceDate} · ${transitionLabel(result)}\\n피봇점 ${result.pivotDate} · 수치 ${indicatorValue(result,item.meta)} · 확정일 ${result.confirmationDate}\\n피봇 유효성 ${Math.round(result.structuralScore)}점 · 피봇 타이밍 ${Math.round(result.timingScore)}점 · 추세 지속성 ${result.durationScore==null?'계산 대기':`${Math.round(result.durationScore)}점`}\\n${relationshipLabel(result.relationship)} · 관계 신뢰도 ${Math.round(result.relationshipConfidence*100)}% · 관계 보너스 +${window.MacroWatchFrontend.formatDisplayNumber(result.relationshipBonus,{maximumFractionDigits:1})}`:'허용 탐색창 안에 확인된 구조 피봇이 없습니다.';card.append(label,body);if(result){const reason=pivotReasonFor(item,result);if(reason){const reasonBox=document.createElement('div'),reasonLabel=document.createElement('b'),reasonText=document.createElement('p');reasonBox.className='historical-pivot-reason';reasonLabel.textContent='판정 근거';reasonText.textContent=reason;reasonBox.append(reasonLabel,reasonText);card.append(reasonBox);}}grid.append(card);}root.append(grid);}"""
js = js[:s] + replacement + js[e:]

for stale in ["'0점 · 범위 근접'", '`0점 · 범위 근접`', ' · 범위 근접']:
    js = js.replace(stale, "''" if stale.startswith(("'", '`')) else '')

js_path.write_text(js)

css_path = Path('assets/css/historical-insight.css')
css = css_path.read_text()
extra = '''\n\n/* Pivot detail readability: reasons live with the confirmed colored pivot cards. */\n.historical-pivot-detail-grid article { display:flex; flex-direction:column; gap:8px; }\n.historical-pivot-detail-grid article > strong { font-size:13px; line-height:1.4; }\n.historical-pivot-detail-grid article > p { margin:0; color:var(--theme-text-secondary); line-height:1.65; white-space:pre-line; }\n.historical-pivot-reason { margin-top:3px; padding:10px 12px; border:1px solid rgba(192,38,211,.16); border-radius:8px; background:rgba(192,38,211,.045); }\n.historical-pivot-reason b { display:block; margin-bottom:4px; color:#a21caf; font-size:10px; font-weight:850; letter-spacing:.04em; }\n.historical-pivot-reason p { margin:0; color:var(--theme-text-primary); font-size:12px; line-height:1.6; }\nhtml[data-theme="dark"] .historical-pivot-reason { border-color:rgba(216,180,254,.2); background:rgba(192,38,211,.09); }\nhtml[data-theme="dark"] .historical-pivot-reason b { color:#e879f9; }\n'''
if 'Pivot detail readability: reasons live with the confirmed colored pivot cards.' not in css:
    css += extra
css_path.write_text(css)

html_path = Path('historical-insight.html')
html = html_path.read_text()
html = html.replace('assets/css/historical-insight.css?v=22', 'assets/css/historical-insight.css?v=23')
html = html.replace('assets/js/historical-insight/historical-insight.js?v=28', 'assets/js/historical-insight/historical-insight.js?v=29')
html_path.write_text(html)
