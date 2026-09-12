'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const source = fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8');
assert.match(source, /async function open\(\{ client, userId \}\)/);
assert.match(source, /activeUserId = userId/);
assert.doesNotMatch(source, /async function open[\s\S]{0,500}auth\.getSession\(/);
console.log('shared modal consumes the page-supplied user id: ok');
