'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const auth = fs.readFileSync('assets/js/core/auth.js', 'utf8');
const logoutStart = auth.indexOf('function bindLogoutEvent()');
const accountStart = auth.indexOf('async function bindAccountUi()');
const initializeStart = auth.indexOf('async function initialize()');

assert.ok(logoutStart >= 0 && accountStart > logoutStart && initializeStart > accountStart);
assert.match(auth.slice(logoutStart, accountStart), /getElementById\('logout-button'\)[\s\S]*auth\.signOut\(\)[\s\S]*location\.replace\('index\.html'\)/);
assert.match(auth.slice(accountStart, initializeStart), /MacroWatchAccountModal\.bindTrigger/);
assert.match(auth.slice(initializeStart), /bindLogoutEvent\(\);[\s\S]*if \(!hasMainLogin\)/);

console.log('logout remains independent while profile actions use the shared modal: ok');
