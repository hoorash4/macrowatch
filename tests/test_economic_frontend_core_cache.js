'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('economic-charts.html', 'utf8');
assert.match(html, /assets\/js\/core\/frontend-core\.js\?v=7/);
assert.doesNotMatch(html, /assets\/js\/core\/frontend-core\.js\?v=6/);

console.log('economic charts load the current themed frontend core cache version: ok');
