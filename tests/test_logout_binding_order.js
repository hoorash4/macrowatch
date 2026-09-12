'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const auth = fs.readFileSync('assets/js/core/auth.js', 'utf8');
const accountStart = auth.indexOf('function bindAccountEvents()');
const logoutStart = auth.indexOf('function bindLogoutEvent()');
const initializeStart = auth.indexOf('async function initialize()');

assert.ok(accountStart >= 0 && logoutStart > accountStart && initializeStart > logoutStart);
assert.doesNotMatch(auth.slice(accountStart, logoutStart), /getElementById\('logout-button'\)/);
assert.match(auth.slice(logoutStart, initializeStart), /getElementById\('logout-button'\)[\s\S]*auth\.signOut\(\)[\s\S]*location\.replace\('index\.html'\)/);
assert.match(auth.slice(initializeStart), /bindLogoutEvent\(\);[\s\S]*if \(!hasMainLogin\)/);

console.log('logout binds independently before account UI initialization: ok');
