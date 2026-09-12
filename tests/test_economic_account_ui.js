const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'assets/css/economic-charts.css'), 'utf8');

assert.equal((html.match(/id="profile-modal"/g) || []).length, 1);
assert.equal((html.match(/id="account-delete-modal"/g) || []).length, 1);
assert.match(html, /id="economic-dashboard-panel"[^>]*data-dashboard-panel="economic"/);
assert.match(css, /body\.dashboard-economic-view #app-shell\{max-width:1600px\}/);
console.log('economic dashboard reuses the one main account modal and preserves its own layout: ok');
