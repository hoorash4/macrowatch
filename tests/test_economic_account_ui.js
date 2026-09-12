const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const shared = fs.readFileSync(path.join(root, 'assets/js/core/account-controls.js'), 'utf8');

assert.match(html, /class="dashboard-nav-actions"/);
assert.doesNotMatch(html, /id="profile-modal"/);
assert.match(shared, /id="theme-preference"/);
assert.match(shared, /body\.insertBefore\(card, deleteSection\)/);
assert.match(shared, /body\.insertBefore\(themeCard, deleteSection\)/);
assert.match(shared, /colorScheme = 'dark'/);
assert.match(shared, /themeCard\.style\.background = 'rgba\(2, 6, 23, \.6\)'/);
assert.match(shared, /select\.style\.background = '#0f172a'/);
console.log('shared profile modal keeps dark styling and places theme control before delete: ok');
