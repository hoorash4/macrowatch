const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const site = path.resolve(process.argv[2] || '_site');
const read = file => fs.readFileSync(file, 'utf8').replaceAll('\r\n', '\n');
for (const name of fs.readdirSync('assets/js', {recursive:true}).filter(name => name.endsWith('.js'))) {
  const canonical = read(path.join('assets/js',name)).trim();
  const served = read(path.join(site,path.basename(name)));
  assert.ok(served.includes(canonical), `Alias differs: ${name}`);
  new vm.Script(served, {filename:path.basename(name)});
}
assert.equal(read(path.join(site,'styles.css')).trim(), read('assets/css/styles.css').replaceAll('../../images/', './images/').trim());
const dashboard = read(path.join(site, 'dashboard-charts.js'));
assert.ok(dashboard.startsWith(read('assets/js/charts/analysis-chart-utils.js')));
assert.ok(read(path.join(site, 'auth.js')).startsWith(read('assets/js/core/frontend-core.js')));
for (const name of ['CODE_STRUCTURE.md', 'HANDOFF.md', 'LIQUIDITY_SPEC.md', 'SECURITY.md']) {
  assert.equal(read(path.join(site, name)), read(path.join('docs', name)));
}
for(const name of ['index.html','admin.html','economic-charts.html']) {
  for(const [,url] of read(path.join(site,name)).matchAll(/<(?:script|link)\b[^>]*?(?:src|href)="([^"]+)"/g)) {
    if(/^(?:https?:|\/\/|#)/.test(url))continue;
    assert.ok(fs.statSync(path.join(site,url.split('?')[0])).isFile(),`${name}: ${url}`);
  }
}

const builtIndex = read(path.join(site, 'index.html'));
assert.doesNotMatch(builtIndex, /<!-- ===== 개인 설정창: 카카오톡 연결 \/ 회원 탈퇴 ===== -->/);
assert.doesNotMatch(builtIndex, /<!-- ===== 회원 탈퇴 최종 확인창 ===== -->/);
assert.doesNotMatch(builtIndex, /<!-- ===== 공용 안내창: 서비스 준비 중 \/ 등록 완료 ===== -->/);
const accountModalIndex = builtIndex.indexOf('assets/js/core/account-modal.js?v=3');
const mainAuthIndex = builtIndex.indexOf('assets/js/core/auth.js');
assert.ok(accountModalIndex >= 0 && mainAuthIndex > accountModalIndex, 'main must load canonical account modal before auth.js');

const builtEconomic = read(path.join(site, 'economic-charts.html'));
assert.match(builtEconomic, /assets\/js\/core\/account-modal\.js\?v=3/);
assert.match(builtEconomic, /assets\/js\/core\/auth-chart\.js\?v=1/);

console.log('Built Pages entries, canonical account modal wiring and all compatibility assets verified.');
