const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const capture = fs.readFileSync(path.join(root, 'assets/js/core/economic-supabase-capture.js'), 'utf8');
const actions = fs.readFileSync(path.join(root, 'assets/js/core/economic-account-actions.js'), 'utf8');

assert.match(html, /id="economic-profile-button"/);
assert.match(html, /id="economic-admin-link"/);
assert.match(html, /id="economic-logout-button"/);
assert.match(html, /economic-supabase-capture\.js\?v=1[\s\S]*economic-charts\.js\?v=12[\s\S]*economic-account-actions\.js\?v=1/);
assert.match(capture, /window\.macroWatchEconomicSupabase\s*=\s*client/);
assert.match(capture, /window\.supabase\.createClient\s*=\s*original/);
assert.match(actions, /const client = window\.macroWatchEconomicSupabase/);
assert.match(actions, /fetch\('index\.html'/);
assert.match(actions, /getElementById\('profile-modal'\)/);
assert.match(actions, /select\('is_admin'\)/);
assert.match(actions, /auth\.signOut\(\{ scope: 'local' \}\)/);
assert.doesNotMatch(actions, /sessionStorage\.setItem\('macrowatch\.open-profile'/);
assert.doesNotMatch(actions, /profileButton[\s\S]{0,300}location\.(?:href|replace)[\s\S]{0,80}index\.html/);

console.log('economic account actions contracts: ok');
