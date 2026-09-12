const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const auth = fs.readFileSync(path.join(root, 'assets/js/core/auth.js'), 'utf8');

assert.match(html, /class="dashboard-nav-actions"/);
assert.doesNotMatch(html, /id="profile-modal"/);
assert.match(auth, /id="theme-preference"/);
assert.match(auth, /body\.insertBefore\(card, deleteSection\)/);
assert.match(auth, /body\.insertBefore\(themeCard, deleteSection\)/);
assert.match(auth, /colorScheme = 'dark'/);
assert.match(auth, /themeCard\.style\.background = 'rgba\(2, 6, 23, \.6\)'/);
assert.match(auth, /select\.style\.background = '#0f172a'/);
console.log('profile modal stays dark and theme control sits directly before account delete: ok');
