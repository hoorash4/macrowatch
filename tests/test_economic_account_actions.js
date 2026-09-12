const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const economicHtml = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const indexHtml = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const capture = fs.readFileSync(path.join(root, 'assets/js/core/economic-supabase-capture.js'), 'utf8');
const auth = fs.readFileSync(path.join(root, 'assets/js/core/auth.js'), 'utf8');

assert.match(economicHtml, /id="profile-button"/);
assert.match(economicHtml, /id="admin-page-link"/);
assert.match(economicHtml, /id="logout-button"/);
assert.match(economicHtml, /auth\.js\?v=22/);
assert.doesNotMatch(economicHtml, /id="profile-modal"|id="account-delete-modal"|id="service-preparing-modal"/);
assert.match(indexHtml, /id="profile-modal"/);
assert.match(indexHtml, /id="account-delete-modal"/);
assert.match(indexHtml, /id="service-preparing-modal"/);
assert.doesNotMatch(economicHtml, /economic-profile-button|economic-admin-link|economic-logout-button|economic-account-actions|account-controls/);
assert.match(capture, /window\.macroWatchSupabase\s*=\s*client/);
assert.match(auth, /fetch\('index\.html', \{ cache: 'no-cache' \}\)/);
assert.match(auth, /getElementById\('profile-button'\)/);
assert.match(auth, /getElementById\('admin-page-link'\)/);
assert.match(auth, /getElementById\('logout-button'\)/);
assert.match(auth, /select\('is_admin'\)/);
assert.match(auth, /auth\.signOut\(\)/);
assert.match(auth, /function bindProfileEvents/);
assert.match(auth, /function bindAccountEvents/);

console.log('dashboard and economic charts use the same auth account actions: ok');
