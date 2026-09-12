'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8');

assert.match(source, /from\('user_accounts'\)/);
assert.match(source, /select\('username,theme_preference'\)/);
assert.match(source, /eq\('user_id', userId\)/);
assert.match(source, /Promise\.allSettled/);
assert.match(source, /activeThemeController\?\.applyPreference\?\.\(preference\)/);
assert.match(source, /window\.MacroWatchAccountModal\.open = open/);
assert.match(source, /window\.MacroWatchAccountModal\.bindTrigger = bindTrigger/);

console.log('shared account modal loads account data from the passed user id: ok');
