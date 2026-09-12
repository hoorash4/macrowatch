const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'assets/css/economic-charts.css'), 'utf8');

assert.equal((html.match(/id="profile-modal"/g) || []).length, 1);
assert.equal((html.match(/id="account-delete-modal"/g) || []).length, 1);
assert.match(html, /data-dashboard-view="economic"/);
assert.match(html, /data-mobile-dashboard-view="economic"/);
assert.match(html, /id="economic-dashboard-panel"[^>]*data-dashboard-panel="economic"/);
assert.match(css, /body\.dashboard-economic-view #app-shell\{--dashboard-content-max-width:1540px\}/);
assert.match(css, /#app-shell \.mobile-bottom-nav\{grid-template-columns:repeat\(6,minmax\(0,1fr\)\)\}/);
console.log('economic dashboard is one main menu, reuses the one account modal, and preserves its layout: ok');
