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
for (const relative of fs.readdirSync(path.join(root, 'assets/js'), {recursive: true})) {
  if (!relative.endsWith('.js')) continue;
  const name = path.basename(relative);
  if (names.has(name)) throw new Error(`Duplicate legacy asset name: ${name}`);
  names.add(name);
  const dependency = name === 'dashboard-charts.js'
    ? read('assets/js/charts/analysis-chart-utils.js') + '\n'
    : '';
  fs.writeFileSync(path.join(site, name), dependency + read(path.join('assets/js', relative)));
}
fs.writeFileSync(path.join(site, 'styles.css'), read('assets/css/styles.css').replaceAll('../../images/', './images/'));
for (const name of ['CODE_STRUCTURE.md', 'HANDOFF.md', 'LIQUIDITY_SPEC.md', 'SECURITY.md']) {
  fs.copyFileSync(path.join(root, 'docs', name), path.join(site, name));
}
console.log(`Generated ${names.size} legacy scripts, stylesheet and document URLs in build output.`);
