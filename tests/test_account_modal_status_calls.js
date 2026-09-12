'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8');
assert.match(source, /invoke\(client, 'kakao-auth', 'status'\)/);
assert.match(source, /invoke\(client, 'notification-settings', 'status'\)/);
assert.match(source, /invoke\(activeClient, 'kakao-auth', 'unlink'\)/);
assert.match(source, /invoke\(activeClient, 'notification-settings', 'save'/);
assert.match(source, /invoke\(activeClient, 'notification-settings', 'remove'\)/);
console.log('shared account modal uses existing account status APIs: ok');
