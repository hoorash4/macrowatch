'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');
const bridge = fs.readFileSync('assets/js/core/economic-account-modal-bridge.js', 'utf8');
const modalData = fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8');
const chartAuth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');

assert.match(html, /account-modal\.js\?v=1[\s\S]*account-modal-data\.js\?v=1[\s\S]*auth-chart\.js\?v=1[\s\S]*economic-account-modal-bridge\.js\?v=1/);
assert.match(bridge, /client\.auth\.getSession\(\)/);
assert.match(bridge, /userId\s*=\s*data\?\.session\?\.user\?\.id/);
assert.match(bridge, /MacroWatchAccountModal\?\.open\?\.\(\{ client, userId \}\)/);
assert.match(bridge, /stopImmediatePropagation\(\)/);

assert.match(modalData, /async function open\(\{ client, userId \}\)/);
assert.match(modalData, /select\('username,theme_preference'\)/);
assert.match(modalData, /eq\('user_id', userId\)/);
assert.match(modalData, /invoke\(client, 'kakao-auth', 'status'\)/);
assert.match(modalData, /invoke\(client, 'notification-settings', 'status'\)/);
assert.doesNotMatch(modalData, /auth\.getSession\(\)[\s\S]*loadAccount\(client, userId\)/);

assert.match(chartAuth, /getElementById\('logout-button'\)[\s\S]*signOut\(\{ scope: 'local' \}\)[\s\S]*location\.replace\('index\.html'\)/);
assert.match(chartAuth, /updateAdminLink\(currentSession\)/);

console.log('economic chart passes page session identity into the shared account modal: ok');
