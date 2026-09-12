// Add old public URLs to the build output only; canonical sources stay in assets/docs.
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const site = path.resolve(process.argv[2] || '_site');
if (site === root || !fs.existsSync(path.join(site, 'index.html'))) {
  throw new Error('Pass a built Pages directory, not the source directory.');
}
const read = name => fs.readFileSync(path.join(root, name), 'utf8');
const names = new Set();
const legacyDependencies = {
  'dashboard-charts.js': 'assets/js/charts/analysis-chart-utils.js',
  'auth.js': 'assets/js/core/frontend-core.js',
};
for (const relative of fs.readdirSync(path.join(root, 'assets/js'), {recursive: true})) {
  if (!relative.endsWith('.js')) continue;
  const name = path.basename(relative);
  if (names.has(name)) throw new Error(`Duplicate legacy asset name: ${name}`);
  names.add(name);
  const dependency = legacyDependencies[name]
    ? read(legacyDependencies[name]) + '\n'
    : '';
  fs.writeFileSync(path.join(site, name), dependency + read(path.join('assets/js', relative)));
}
fs.writeFileSync(path.join(site, 'styles.css'), read('assets/css/styles.css').replaceAll('../../images/', './images/'));

function removeSection(html, startMarker, nextMarker) {
  const start = html.indexOf(startMarker);
  const next = html.indexOf(nextMarker, start + startMarker.length);
  if (start < 0 || next < 0) throw new Error(`Pages account modal marker missing: ${startMarker}`);
  return html.slice(0, start) + html.slice(next);
}

function replaceBuiltFile(file, content) {
  const temp = `${file}.macrowatch-tmp`;
  fs.writeFileSync(temp, content);
  fs.renameSync(temp, file);
}

const indexPath = path.join(site, 'index.html');
let indexHtml = fs.readFileSync(indexPath, 'utf8');
indexHtml = removeSection(indexHtml, '<!-- ===== 공용 안내창: 서비스 준비 중 / 등록 완료 ===== -->', '<!-- ===== 지표 후보 선택창 ===== -->');
indexHtml = removeSection(indexHtml, '<!-- ===== 개인 설정창: 카카오톡 연결 / 회원 탈퇴 ===== -->', '<!-- ===== 회원 탈퇴 최종 확인창 ===== -->');
indexHtml = removeSection(indexHtml, '<!-- ===== 회원 탈퇴 최종 확인창 ===== -->', '<!-- ===== 카카오톡 알림 상태 변경 확인창 ===== -->');
const authScriptPattern = /(<script src="assets\/js\/core\/auth\.js\?v=\d+"><\/script>)/;
if (!authScriptPattern.test(indexHtml)) throw new Error('Main auth script tag not found in Pages output.');
indexHtml = indexHtml.replace(authScriptPattern, '<script src="assets/js/core/account-modal.js?v=3"></script>\n  $1');
replaceBuiltFile(indexPath, indexHtml);

const economicPath = path.join(site, 'economic-charts.html');
if (fs.existsSync(economicPath)) {
  const economicHtml = fs.readFileSync(economicPath, 'utf8').replace(/assets\/js\/core\/account-modal\.js\?v=\d+/, 'assets/js/core/account-modal.js?v=3');
  replaceBuiltFile(economicPath, economicHtml);
}

for (const name of ['CODE_STRUCTURE.md', 'HANDOFF.md', 'LIQUIDITY_SPEC.md', 'SECURITY.md']) {
  fs.copyFileSync(path.join(root, 'docs', name), path.join(site, name));
}
console.log(`Generated ${names.size} legacy scripts, stylesheet, canonical account modal wiring and document URLs in build output.`);
