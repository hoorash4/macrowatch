const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const normalizer = fs.readFileSync(path.join(root, 'assets/js/core/profile-modal-normalizer.js'), 'utf8');

assert.match(html, /class="dashboard-nav-actions"/);
assert.match(html, /id="profile-username"[\s\S]*id="password-change-form"[\s\S]*id="account-delete-button"/);
assert.match(normalizer, /theme-preference/);
assert.match(normalizer, /insertBefore\(themeCard, deleteSection\)/);
assert.match(normalizer, /colorScheme = 'dark'/);
assert.match(normalizer, /--theme-surface', '#0f172a'/);
console.log('economic shared profile modal ui contracts: ok');
