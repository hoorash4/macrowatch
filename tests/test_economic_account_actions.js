const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.join(__dirname, '..');
const economicHtml = fs.readFileSync(path.join(root, 'economic-charts.html'), 'utf8');
const indexHtml = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
const auth = fs.readFileSync(path.join(root, 'assets/js/core/auth.js'), 'utf8');

for (const id of ['profile-button', 'admin-page-link', 'logout-button']) {
  assert.match(economicHtml, new RegExp(`id="${id}"`));
}

assert.doesNotMatch(economicHtml, /id="profile-modal"|id="account-delete-modal"|id="service-preparing-modal"/);
assert.equal((indexHtml.match(/id="profile-modal"/g) || []).length, 1);
assert.equal((indexHtml.match(/id="account-delete-modal"/g) || []).length, 1);
assert.equal((indexHtml.match(/id="service-preparing-modal"/g) || []).length, 1);
assert.doesNotMatch(economicHtml, /economic-profile-button|economic-admin-link|economic-logout-button|economic-account-actions|account-controls|macrowatch\.open-profile/);

assert.match(auth, /getElementById\('profile-button'\)/);
assert.match(auth, /getElementById\('logout-button'\)/);
assert.match(auth, /function bindProfileEvents/);
assert.match(auth, /function bindAccountEvents/);
assert.match(auth, /id="theme-preference"/);
assert.match(auth, /body\.insertBefore\(card, deleteSection\)/);
assert.match(auth, /body\.insertBefore\(themeCard, deleteSection\)/);
assert.match(auth, /colorScheme = 'dark'/);

console.log('economic account controls keep the shared button contract and one modal source: ok');
