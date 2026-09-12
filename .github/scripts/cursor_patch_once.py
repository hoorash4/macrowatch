from pathlib import Path

replacements = {
    'assets/js/charts/equity-bond-attractiveness-chart.js': [
        ('<text data-value text-anchor="middle" y="16" fill="#334155" font-size="11"></text><text data-date text-anchor="middle" y="${HEIGHT - BOTTOM + 15}" class="policy-expectation-cursor-detail"></text>',
         '<text data-value text-anchor="middle" y="16" class="analysis-chart-cursor-text analysis-chart-cursor-value" visibility="hidden"></text><text data-date text-anchor="middle" y="${HEIGHT - BOTTOM + 12}" class="analysis-chart-cursor-text analysis-chart-cursor-date" visibility="hidden"></text>'),
        ("      value.setAttribute('x', x); value.textContent = rows.map(row => `${LABELS[row.country]} ${utils.formatChartNumber(row.score, { maximumFractionDigits: 1 })}`).join(' · '); value.setAttribute('visibility', 'visible');\n      dateLabel.setAttribute('x', x); dateLabel.textContent = nearestDate; dateLabel.classList.add('is-visible');\n",
         "      value.textContent = rows.map(row => `${LABELS[row.country]} ${utils.formatChartNumber(row.score, { maximumFractionDigits: 1 })}`).join(' · '); value.setAttribute('visibility', 'visible');\n      dateLabel.textContent = nearestDate; dateLabel.setAttribute('visibility', 'visible');\n      utils.positionCursorText(value, x, frame); utils.positionCursorText(dateLabel, x, frame);\n"),
        ("    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); value.setAttribute('visibility', 'hidden'); dateLabel.classList.remove('is-visible'); });",
         "    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); value.setAttribute('visibility', 'hidden'); dateLabel.setAttribute('visibility', 'hidden'); });"),
    ],
    'assets/js/charts/liquidity-chart.js': [
        ('<text data-liquidity-value text-anchor="middle" y="16" fill="#334155" font-size="12"></text><text data-liquidity-date text-anchor="middle" y="${HEIGHT-PADDING.bottom+14}" class="policy-expectation-cursor-detail"></text>',
         '<text data-liquidity-value text-anchor="middle" y="${PADDING.top + 12}" class="analysis-chart-cursor-text analysis-chart-cursor-value" visibility="hidden"></text><text data-liquidity-date text-anchor="middle" y="${HEIGHT-PADDING.bottom+12}" class="analysis-chart-cursor-text analysis-chart-cursor-date" visibility="hidden"></text>'),
        ("      value.setAttribute('x',nearest.x);dateLabel.setAttribute('x',nearest.x);\n      value.textContent=metrics.map(metric=>`${names[metric]} ${Number.isFinite(nearest[metric])?utils.formatChartNumber(nearest[metric], { maximumFractionDigits: 1 }):'미발표'}`).join(' · ');\n      dateLabel.textContent=nearest.observation_date; value.setAttribute('visibility','visible'); dateLabel.classList.add('is-visible');\n",
         "      value.textContent=metrics.map(metric=>`${names[metric]} ${Number.isFinite(nearest[metric])?utils.formatChartNumber(nearest[metric], { maximumFractionDigits: 1 }):'미발표'}`).join(' · ');\n      dateLabel.textContent=nearest.observation_date; value.setAttribute('visibility','visible'); dateLabel.setAttribute('visibility','visible');\n      utils.positionCursorText(value,nearest.x,frame);utils.positionCursorText(dateLabel,nearest.x,frame);\n"),
        ("    frame.addEventListener('pointerleave',()=>{cursor.classList.remove('is-visible');value.setAttribute('visibility','hidden');dateLabel.classList.remove('is-visible');});",
         "    frame.addEventListener('pointerleave',()=>{cursor.classList.remove('is-visible');value.setAttribute('visibility','hidden');dateLabel.setAttribute('visibility','hidden');});"),
    ],
    'assets/js/charts/korea-foreign-flow-chart.js': [
        ('<text data-korea-foreign-flow-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text>',
         '<text data-korea-foreign-flow-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 12}" class="analysis-chart-cursor-text analysis-chart-cursor-date" visibility="hidden"></text>'),
        ("      cursor.setAttribute('x1', nearest.x); cursor.setAttribute('x2', nearest.x); detail.setAttribute('x', nearest.x);\n      const [, month, day] = String(nearest.observation_date).split('-').map(Number); detail.textContent = `${month}월 ${day}일`;\n      cursorValue.textContent = chartUtils.cursorValueText(nearest, PROFILE.cursorSeries); cursorValue.setAttribute('visibility', 'visible'); chartUtils.positionCursorText(cursorValue, nearest.x, frame);\n      cursor.classList.add('is-visible'); detail.classList.add('is-visible');\n",
         "      cursor.setAttribute('x1', nearest.x); cursor.setAttribute('x2', nearest.x);\n      const [, month, day] = String(nearest.observation_date).split('-').map(Number); detail.textContent = `${month}월 ${day}일`; detail.setAttribute('visibility', 'visible');\n      cursorValue.textContent = chartUtils.cursorValueText(nearest, PROFILE.cursorSeries); cursorValue.setAttribute('visibility', 'visible'); chartUtils.positionCursorText(cursorValue, nearest.x, frame); chartUtils.positionCursorText(detail, nearest.x, frame);\n      cursor.classList.add('is-visible');\n"),
        ("    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.classList.remove('is-visible');\n      cursorValue.setAttribute('visibility', 'hidden'); });",
         "    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.setAttribute('visibility', 'hidden');\n      cursorValue.setAttribute('visibility', 'hidden'); });"),
    ],
    'assets/js/charts/em-capacity-chart.js': [
        ('<text data-em-capacity-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 14}" class="policy-expectation-cursor-detail"></text>',
         '<text data-em-capacity-detail text-anchor="middle" y="${HEIGHT - PADDING.bottom + 12}" class="analysis-chart-cursor-text analysis-chart-cursor-date" visibility="hidden"></text>'),
        ("      detail.setAttribute('x', nearest.x); detail.textContent = formatMonthDay(nearest.observation_date);\n      cursorValue.textContent = chartUtils.cursorValueText(nearest, PROFILE.cursorSeries); cursorValue.setAttribute('visibility', 'visible'); chartUtils.positionCursorText(cursorValue, nearest.x, frame);\n      cursor.classList.add('is-visible'); detail.classList.add('is-visible');\n",
         "      detail.textContent = formatMonthDay(nearest.observation_date); detail.setAttribute('visibility', 'visible');\n      cursorValue.textContent = chartUtils.cursorValueText(nearest, PROFILE.cursorSeries); cursorValue.setAttribute('visibility', 'visible'); chartUtils.positionCursorText(cursorValue, nearest.x, frame); chartUtils.positionCursorText(detail, nearest.x, frame);\n      cursor.classList.add('is-visible');\n"),
        ("    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.classList.remove('is-visible');\n      cursorValue.setAttribute('visibility', 'hidden'); });",
         "    frame.addEventListener('pointerleave', () => { cursor.classList.remove('is-visible'); detail.setAttribute('visibility', 'hidden');\n      cursorValue.setAttribute('visibility', 'hidden'); });"),
    ],
}

for filename, rules in replacements.items():
    path = Path(filename)
    text = path.read_text(encoding='utf-8')
    for old, new in rules:
        assert old in text, f'{filename}: expected cursor fragment not found'
        text = text.replace(old, new, 1)
    path.write_text(text, encoding='utf-8')

test_path = Path('tests/test_dashboard.js')
tests = test_path.read_text(encoding='utf-8')
marker = "test('공통 커서 글자는 보이는 플롯 폭 안으로 이동한다', () => {"
assert marker in tests
addition = r'''

test('분석 차트의 위아래 커서 텍스트는 공통 위치 보정을 우회하지 않는다', () => {
  const files = [
    'assets/js/charts/equity-bond-attractiveness-chart.js',
    'assets/js/charts/liquidity-chart.js',
    'assets/js/charts/korea-foreign-flow-chart.js',
    'assets/js/charts/em-capacity-chart.js',
    'assets/js/charts/policy-chart.js',
    'assets/js/charts/policy-expectation-chart.js',
  ];
  for (const filename of files) {
    const source = fs.readFileSync(path.join(__dirname, '..', filename), 'utf8');
    assert.match(source, /analysis-chart-cursor-text analysis-chart-cursor-value/);
    assert.match(source, /analysis-chart-cursor-text analysis-chart-cursor-date/);
    const positioned = (source.match(/positionCursorText\(/g) || []).length;
    assert.ok(positioned >= 2, `${filename} 위·아래 커서 모두 공통 위치 보정을 사용해야 한다`);
  }
  const attractiveness = fs.readFileSync(path.join(__dirname, '..', 'assets/js/charts/equity-bond-attractiveness-chart.js'), 'utf8');
  const liquidity = fs.readFileSync(path.join(__dirname, '..', 'assets/js/charts/liquidity-chart.js'), 'utf8');
  const foreignFlow = fs.readFileSync(path.join(__dirname, '..', 'assets/js/charts/korea-foreign-flow-chart.js'), 'utf8');
  const emCapacity = fs.readFileSync(path.join(__dirname, '..', 'assets/js/charts/em-capacity-chart.js'), 'utf8');
  assert.doesNotMatch(attractiveness, /(?:value|dateLabel)\.setAttribute\('x',\s*x\)/);
  assert.doesNotMatch(liquidity, /(?:value|dateLabel)\.setAttribute\('x',\s*nearest\.x\)/);
  assert.doesNotMatch(foreignFlow, /detail\.setAttribute\('x',\s*nearest\.x\)/);
  assert.doesNotMatch(emCapacity, /detail\.setAttribute\('x',\s*nearest\.x\)/);
});
'''
tests = tests.replace(marker, addition + '\n' + marker, 1)
test_path.write_text(tests, encoding='utf-8')
