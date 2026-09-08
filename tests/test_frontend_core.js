const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const test = require('node:test');

function fixture(responses, session = { access_token: 'session-token' }, refreshError = null) {
  const requests = [];
  let refreshes = 0;
  const client = { auth: {
    getSession: async () => ({ data: { session }, error: null }),
    refreshSession: async () => { refreshes++; return { data: { session }, error: refreshError }; },
  } };
  const context = { window: { MACROWATCH_CONFIG: {
    supabaseUrl: 'https://example.invalid', supabasePublishableKey: 'public-key',
  } }, fetch: async (url, options) => {
    requests.push({ url, ...options });
    const response = responses.shift();
    return { status: response.status, ok: response.status < 400,
      json: async () => { if (response.invalidJson) throw new Error('invalid JSON'); return response.body; } };
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../assets/js/core/frontend-core.js'), 'utf8'), context);
  return { invoke: context.window.MacroWatchFrontend.createFunctionClient(client).invoke,
    requests, refreshes: () => refreshes };
}

test('authenticated function requests preserve endpoint, payload and bearer token', async () => {
  const f = fixture([{ status: 200, body: { ready: true } }]);
  assert.equal((await f.invoke('notification-settings', { action: 'status' })).ready, true);
  assert.equal(f.requests[0].url, 'https://example.invalid/functions/v1/notification-settings');
  assert.equal(f.requests[0].headers.Authorization, 'Bearer session-token');
  assert.equal(f.requests[0].body, '{"action":"status"}');
});

test('401 refresh is bounded to once and retains the caller error message', async () => {
  const f = fixture([{ status: 401, body: {} }, { status: 401, body: {} }]);
  await assert.rejects(f.invoke('test', {}, { errorMessage: status => `failure ${status}` }), /failure 401/);
  assert.equal(f.requests.length, 2);
  assert.equal(f.refreshes(), 1);
});

test('anonymous OAuth exchange uses publishable key without session or refresh', async () => {
  const f = fixture([{ status: 401, body: { error: 'bad state' } }], null);
  await assert.rejects(f.invoke('kakao-auth', { action: 'exchange' }, { authenticated: false }), /bad state/);
  assert.equal(f.requests[0].headers.Authorization, 'Bearer public-key');
  assert.equal(f.refreshes(), 0);
});

test('missing session blocks authenticated calls before fetch', async () => {
  const f = fixture([], null);
  await assert.rejects(f.invoke('test', {}), /로그인이 필요합니다/);
  assert.equal(f.requests.length, 0);
});

test('application errors and malformed JSON retain original handling', async () => {
  const f = fixture([{ status: 200, body: { error: 'application error' } }, { status: 200, invalidJson: true }]);
  await assert.rejects(f.invoke('test', {}), /application error/);
  assert.equal(JSON.stringify(await f.invoke('test', {})), '{}');
});
