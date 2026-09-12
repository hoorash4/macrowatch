'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');
const modal = html.indexOf('assets/js/core/account-modal.js?v=1');
const data = html.indexOf('assets/js/core/account-modal-data.js?v=1');
const auth = html.indexOf('assets/js/core/auth-chart.js?v=1');
const bridge = html.indexOf('assets/js/core/economic-account-modal-bridge.js?v=1');

assert.ok(modal >= 0 && data > modal && auth > data && bridge > auth);
console.log('economic account modal bridge script order: ok');
