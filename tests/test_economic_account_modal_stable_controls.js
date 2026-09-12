'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const auth = fs.readFileSync('assets/js/core/auth-chart.js', 'utf8');
assert.match(auth, /getElementById\('logout-button'\)/);
assert.match(auth, /updateAdminLink\(currentSession\)/);
assert.doesNotMatch(auth, /MacroWatchAccountModal\.open/);
console.log('logout/admin remain on the stable chart auth path: ok');
