const fs = require('node:fs');
const assert = require('node:assert/strict');

const source = fs.readFileSync('assets/js/charts/economic-charts.js', 'utf8');

assert.match(source, /r\?\.\[dk\]!=null/);
assert.match(source, /r\?\.\[vk\]!=null/);
assert.match(source, /String\(r\[vk\]\)\.trim\(\)!==''/);
assert.doesNotMatch(
  source,
  /data\.map\(r=>\(\{time:String\(r\[dk\]\)\.slice\(0,10\),value:Number\(r\[vk\]\)\}\)\)/,
  'missing numeric values must not be converted with Number(null) -> 0',
);

console.log('economic chart missing-value handling contract ok');
