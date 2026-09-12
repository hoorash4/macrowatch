const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const capture = fs.readFileSync(path.join(root, 'assets/js/core/economic-supabase-capture.js'), 'utf8');
const shared = fs.readFileSync(path.join(root, 'assets/js/core/account-controls-main.js'), 'utf8');

assert.match(html, /id="profile-button"/);
assert.match(html, /id="admin-page-link"/);
assert.match(html, /id="logout-button"/);
assert.match(html, /id="profile-modal"/);
assert.match(html, /id="account-delete-modal"/);
assert.match(html, /account-controls\.js\?v=\d+/);
assert.doesNotMatch(html, /economic-profile-button|economic-admin-link|economic-logout-button|economic-account-actions/);
assert.match(capture, /window\.macroWatchSupabase\s*=\s*client/);
assert.match(shared, /getElementById\('profile-button'\)/);
assert.match(shared, /getElementById\('admin-page-link'\)/);
assert.match(shared, /getElementById\('logout-button'\)/);
assert.match(shared, /select\('is_admin'\)/);
assert.match(shared, /auth\.signOut\(\)/);

console.log('economic charts reuse dashboard account controls: ok');
