'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(initial = {}) {
  const classes = new Set(initial.classes || ['hidden']);
  const handlers = {};
  return {
    textContent: '', className: '', value: initial.value || '', disabled: false, dataset: {},
    classList: {
      add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
      toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); },
      contains(name) { return classes.has(name); },
    },
    addEventListener(type, handler) { handlers[type] = handler; },
    focus() {}, reset() {},
    handlers,
  };
}

const ids = {};
for (const id of [
  'profile-modal', 'profile-close-button', 'profile-username', 'theme-preference',
  'kakao-connection-status', 'kakao-status-badge', 'kakao-unlink-button',
  'email-alert-status', 'email-alert-badge', 'email-alert-address', 'email-alert-remove-button',
  'password-change-form', 'current-password', 'new-password', 'confirm-password',
  'kakao-connect-button', 'service-preparing-modal', 'service-preparing-close',
  'email-alert-save-button', 'account-delete-button', 'account-delete-modal',
  'account-delete-cancel', 'account-delete-confirm',
]) ids[id] = element();

const functionCalls = [];
const functionClient = { invoke: async (name, payload) => {
  functionCalls.push([name, payload.action]);
  if (name === 'kakao-auth') return { connected: true };
  return { address: 'member@example.com', is_active: true };
} };
const accountQuery = { select() { return this; }, eq(column, value) { this.eqArgs = [column, value]; return this; },
  async maybeSingle() { return { data: { username: 'member', theme_preference: 'dark' }, error: null }; } };
const client = {
  from(table) { assert.equal(table, 'user_accounts'); return accountQuery; },
  auth: {
    getSession: async () => ({ data: { session: { user: { id: 'user-123', email: 'member@example.com' } } }, error: null }),
    signInWithPassword: async () => ({ error: null }), updateUser: async () => ({ error: null }), signOut: async () => ({}),
  },
};
const appliedThemes = [];
const savedThemes = [];
const themeController = {
  applyPreference(value) { appliedThemes.push(value); },
  async savePreference(value) { savedThemes.push(value); },
};
const document = { getElementById(id) { return ids[id] || null; }, addEventListener() {} };
const window = {
  MacroWatchAccountModal: { ensure() {}, normalize() {} },
  MacroWatchFrontend: { createFunctionClient() { return functionClient; } },
  MacroWatchTheme: themeController,
  alert() {}, confirm() { return true; }, location: { replace() {} },
};

vm.runInNewContext(fs.readFileSync('assets/js/core/account-modal-data.js', 'utf8'), { window, document, console });

(async () => {
  await window.MacroWatchAccountModal.open({ client, userId: 'user-123' });
  assert.deepEqual(accountQuery.eqArgs, ['user_id', 'user-123']);
  assert.equal(ids['profile-username'].textContent, 'member');
  assert.equal(ids['kakao-status-badge'].textContent, '연결됨');
  assert.equal(ids['email-alert-badge'].textContent, '사용 중');
  assert.equal(ids['email-alert-address'].value, 'member@example.com');
  assert.deepEqual(functionCalls, [['kakao-auth', 'status'], ['notification-settings', 'status']]);
  assert.deepEqual(appliedThemes, ['dark']);

  ids['theme-preference'].value = 'light';
  await ids['theme-preference'].handlers.change();
  assert.deepEqual(savedThemes, ['light']);
  console.log('shared account modal resolves identity, status APIs and theme through one runtime: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
