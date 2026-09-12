'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');
const chartAuth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');
const accountModal = fs.readFileSync('assets/js/core/account-modal.js', 'utf8');
const accountState = fs.readFileSync('assets/js/core/account-state.js', 'utf8');

for (const id of ['profile-button', 'admin-page-link', 'logout-button']) {
  assert.match(html, new RegExp(`id="${id}"`));
}

assert.doesNotMatch(html, /assets\/js\/core\/auth\.js/);
assert.match(html, /assets\/js\/core\/account-modal\.js\?v=3/);
assert.match(html, /assets\/js\/core\/account-state\.js\?v=1/);
assert.match(html, /assets\/js\/core\/auth-chart\.js\?v=2/);
assert.ok(html.indexOf('account-state.js?v=1') < html.indexOf('auth-chart.js?v=2'));

assert.match(accountModal, /window\.MacroWatchAccountModal\s*=\s*\{ ensure \}/);
for (const id of ['profile-modal', 'account-delete-modal', 'service-preparing-modal', 'profile-username', 'password-change-form', 'email-alert-address']) {
  assert.match(accountModal, new RegExp(`id="${id}"`));
}

assert.match(accountState, /select\('username,theme_preference'\)/);
assert.match(accountState, /invoke\('kakao-auth', 'status'\)/);
assert.match(accountState, /invoke\('notification-settings', 'status'\)/);
assert.match(accountState, /Promise\.allSettled/);
assert.match(accountState, /MacroWatchTheme\?\.applyPreference/);
assert.match(accountState, /MacroWatchTheme\?\.savePreference/);

assert.match(chartAuth, /MacroWatchAccountState\.create\(client\)/);
assert.match(chartAuth, /await accountState\.refresh\(\)/);
assert.match(chartAuth, /accountState\.bindTheme\(\)/);
assert.match(chartAuth, /client\.auth\.getSession\(\)/);
assert.match(chartAuth, /getElementById\('logout-button'\)[\s\S]*signOut\(\{ scope: 'local' \}\)[\s\S]*location\.replace\('index\.html'\)/);
assert.match(chartAuth, /getElementById\('profile-button'\)[\s\S]*openProfile/);
assert.match(chartAuth, /MacroWatchAccountModal\.ensure\(\)/);

console.log('economic account controls load shared profile, theme, Kakao and email state: ok');
