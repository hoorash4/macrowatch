const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const auth = fs.readFileSync(path.join(root, 'assets/js/core/auth.js'), 'utf8');
const index = fs.readFileSync(path.join(root, 'index.html'), 'utf8');

assert.match(auth, /async function showDashboard\(\) \{\s*await window\.MacroWatchTheme\?\.loadStoredPreference\?\.\(\);/);
assert.match(auth, /function showLogin\(message = ''\) \{[\s\S]*?applyPreference\?\.\('light', \{ cache: false, announce: false \}\)/);
assert.match(index, /assets\/js\/core\/frontend-core\.js\?v=10/);
assert.match(index, /assets\/js\/core\/auth\.js\?v=23/);

console.log('login screen stays light until an authenticated theme is loaded: ok');
