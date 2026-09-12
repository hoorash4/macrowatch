'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const modal = fs.readFileSync('assets/js/core/account-modal.js', 'utf8');
const password = modal.indexOf('id="password-change-form"');
const kakao = modal.indexOf('id="kakao-connection-status"');
const email = modal.indexOf('id="email-alert-status"');
const theme = modal.indexOf('id="theme-preference"');
const remove = modal.indexOf('id="account-delete-button"');

assert.ok(password >= 0 && kakao > password && email > kakao && theme > email && remove > theme,
  'shared account modal must preserve the main profile order: password → Kakao → email → theme → delete');
assert.doesNotMatch(modal, /addEventListener\('change'/);
assert.doesNotMatch(modal, /MacroWatchTheme/);
assert.match(modal, /mounted = true;\s*normalize\(\);/);

console.log('shared account modal owns markup only and preserves the profile layout: ok');
