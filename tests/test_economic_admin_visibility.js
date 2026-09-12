'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');
const chartAuth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');

assert.match(html, /id="admin-page-link"[\s\S]*hidden/);
assert.doesNotMatch(html, /client\.auth\.getSession\(\)|from\('user_accounts'\)\.select\('is_admin'\)/);
assert.match(chartAuth, /client\.auth\.getSession\(\)/);
assert.match(chartAuth, /from\('user_accounts'\)\.select\('is_admin'\)\.eq\('user_id', userId\)\.maybeSingle\(\)/);
assert.match(chartAuth, /data\?\.is_admin\s*===\s*true\) link\.hidden = false/);

console.log('economic admin link is owned by chart auth and uses the shared site session: ok');
