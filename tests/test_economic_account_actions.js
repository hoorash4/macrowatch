'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');
const chartAuth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');
const accountModal = fs.readFileSync('assets/js/core/account-modal.js', 'utf8');

for (const id of ['profile-button', 'admin-page-link', 'logout-button']) {
  assert.match(html, new RegExp(`id="${id}"`));
}

assert.doesNotMatch(html, /assets\/js\/core\/auth\.js/);
assert.match(html, /assets\/js\/core\/account-modal\.js\?v=4/);
assert.match(html, /assets\/js\/core\/account-modal-data\.js\?v=2/);
assert.match(html, /assets\/js\/core\/auth-chart\.js\?v=2/);
assert.doesNotMatch(html, /window\.macroWatchSupabase[\s\S]*is_admin/);

assert.match(accountModal, /window\.MacroWatchAccountModal\s*=\s*\{ ensure \}/);
for (const id of ['profile-modal', 'account-delete-modal', 'service-preparing-modal', 'profile-username', 'password-change-form', 'email-alert-address']) {
  assert.match(accountModal, new RegExp(`id="${id}"`));
}

assert.match(chartAuth, /client\.auth\.getSession\(\)/);
assert.match(chartAuth, /getElementById\('logout-button'\)[\s\S]*signOut\(\{ scope: 'local' \}\)[\s\S]*location\.replace\('index\.html'\)/);
assert.match(chartAuth, /MacroWatchAccountModal\.bindTrigger\([\s\S]*getElementById\('profile-button'\)/);
assert.match(chartAuth, /MacroWatchAccountModal\.ensure\(\)/);
assert.doesNotMatch(chartAuth, /function loadKakaoStatus|function loadEmailStatus|password-change-form/);

console.log('economic account controls use chart-specific auth and a reusable modal source: ok');
