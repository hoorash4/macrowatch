'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const index = fs.readFileSync('index.html', 'utf8');
const economic = fs.readFileSync('economic-charts.html', 'utf8');
const mainAuth = fs.readFileSync('assets/js/core/auth.js', 'utf8');
const chartAuth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');
const modalData = fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8');

for (const html of [index, economic]) {
  assert.match(html, /account-modal\.js\?v=4[\s\S]*account-modal-data\.js\?v=2/);
}
assert.doesNotMatch(economic, /economic-account-modal-bridge/);
assert.doesNotMatch(index, /id="profile-modal"|id="account-delete-modal"|id="service-preparing-modal"/);

for (const auth of [mainAuth, chartAuth]) {
  assert.match(auth, /MacroWatchAccountModal\.bindTrigger\(/);
  assert.doesNotMatch(auth, /password-change-form|kakao-connection-status|email-alert-status|account-delete-confirm/);
}

assert.match(modalData, /password-change-form/);
assert.match(modalData, /invoke\('kakao-auth', 'status'\)/);
assert.match(modalData, /invoke\('notification-settings', 'status'\)/);
assert.match(modalData, /invoke\('kakao-auth', 'delete_account'\)/);
assert.match(modalData, /activeThemeController\.savePreference/);

console.log('index and economic charts use one account-modal behavior path: ok');
