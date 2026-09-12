const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const indexHtml = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const legacyHtml = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const dashboard = fs.readFileSync(path.join(root, 'assets/js/dashboard/script.js'), 'utf8');

assert.match(indexHtml, /data-dashboard-view="economic"/);
assert.match(indexHtml, /data-dashboard-panel="economic"/);
assert.match(indexHtml, /id="profile-button"/);
assert.match(indexHtml, /id="admin-page-link"/);
assert.match(indexHtml, /id="logout-button"/);
assert.match(dashboard, /economic: '#economic-charts'/);
assert.match(legacyHtml, /index\.html#economic-charts/);
assert.doesNotMatch(legacyHtml, /profile-button|admin-page-link|logout-button|auth\.js|economic-supabase-capture/);
assert.ok(!fs.existsSync(path.join(root, 'assets/js/core/economic-supabase-capture.js')));
console.log('economic charts are one dashboard menu, not a separate app: ok');
