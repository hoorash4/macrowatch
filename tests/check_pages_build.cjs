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
for(const name of ['index.html','admin.html']) {
  for(const [,url] of read(path.join(site,name)).matchAll(/<(?:script|link)\b[^>]*?(?:src|href)="([^"]+)"/g)) {
    if(/^(?:https?:|\/\/|#)/.test(url))continue;
    assert.ok(fs.statSync(path.join(site,url.split('?')[0])).isFile(),`${name}: ${url}`);
  }
}
console.log('Built Pages entries and all compatibility assets verified.');
