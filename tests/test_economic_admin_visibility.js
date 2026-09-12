'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');

assert.match(html, /id="admin-page-link"[\s\S]*hidden/);
assert.match(html, /window\.macroWatchSupabase\s*\|\|\s*window\.MacroWatchFrontend\.createSupabaseClient\(\)/);
assert.match(html, /client\.auth\.getSession\(\)/);
assert.match(html, /from\('user_accounts'\)\.select\('is_admin'\)\.eq\('user_id', userId\)\.maybeSingle\(\)/);
assert.match(html, /data\?\.is_admin===true\) link\.hidden = false/);

console.log('economic admin link uses the shared site session independently of profile modal loading: ok');
