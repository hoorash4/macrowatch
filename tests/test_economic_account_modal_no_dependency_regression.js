'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const chartAuth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');
const modalData = fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8');

assert.doesNotMatch(chartAuth, /MacroWatchAccountModal\.open/);
assert.doesNotMatch(chartAuth, /account-modal-data/);
assert.match(chartAuth, /document\.addEventListener\('DOMContentLoaded'/);
assert.match(chartAuth, /getElementById\('logout-button'\)/);
assert.match(chartAuth, /updateAdminLink\(currentSession\)/);
assert.match(modalData, /window\.MacroWatchAccountModal\.open = open/);

console.log('chart logout/admin initialization stays independent from account modal data: ok');
