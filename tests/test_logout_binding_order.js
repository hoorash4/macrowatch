'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const auth = fs.readFileSync('assets/js/core/auth.js', 'utf8');

assert.match(auth, /function bindLogoutEvent\(\)/);
assert.match(auth, /bindLogoutEvent\(\);[\s\S]*if \(!hasMainLogin\)/);
assert.doesNotMatch(auth, /function bindAccountEvents\(\)[\s\S]*getElementById\('logout-button'\)/);
assert.match(auth, /getElementById\('logout-button'\)[\s\S]*auth\.signOut\(\)[\s\S]*location\.replace\('index\.html'\)/);

console.log('logout binds independently before account UI initialization: ok');
