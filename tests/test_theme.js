const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '..', 'assets/js/core/frontend-core.js'), 'utf8');
const storage = new Map();
const media = {
  matches: false,
  listener: null,
  addEventListener(type, fn) { if (type === 'change') this.listener = fn; },
};
const documentElement = { dataset: {} };
const headChildren = [];
const document = {
  readyState: 'loading',
  documentElement,
  head: { append(node) { headChildren.push(node); } },
  createElement(tag) { return { tagName: tag.toUpperCase(), id: '', textContent: '', className: '', dataset: {}, append() {}, addEventListener() {}, querySelector() { return null; } }; },
  getElementById() { return null; },
  querySelector() { return null; },
  addEventListener() {},
};
let fetchCount = 0;
const window = {
  MACROWATCH_CONFIG: { supabaseUrl: 'https://example.invalid', supabasePublishableKey: 'public' },
  matchMedia() { return media; },
  localStorage: {
    getItem(key) { return storage.has(key) ? storage.get(key) : null; },
    setItem(key, value) { storage.set(key, value); },
  },
  dispatchEvent() {},
  addEventListener() {},
  supabase: null,
};
const context = {
  window,
  document,
  console,
  queueMicrotask,
  CustomEvent: class CustomEvent { constructor(type, init) { this.type = type; this.detail = init?.detail; } },
  getComputedStyle() { return { getPropertyValue() { return ''; } }; },
  fetch() { fetchCount += 1; throw new Error('theme application must not fetch'); },
  setTimeout,
  clearTimeout,
};
vm.createContext(context);
vm.runInContext(source, context, { filename: 'frontend-core.js' });

const theme = window.MacroWatchTheme;
assert.ok(theme, 'theme API should be exposed');
assert.equal(theme.normalizeThemePreference('system'), 'system');
assert.equal(theme.normalizeThemePreference('light'), 'light');
assert.equal(theme.normalizeThemePreference('dark'), 'dark');
assert.equal(theme.normalizeThemePreference('invalid'), 'system');
assert.equal(theme.resolveTheme('system', false), 'light');
assert.equal(theme.resolveTheme('system', true), 'dark');
assert.equal(theme.resolveTheme('light', true), 'light');
assert.equal(theme.resolveTheme('dark', false), 'dark');

assert.equal(theme.getPreference(), 'system');
theme.applyPreference('dark');
assert.equal(documentElement.dataset.themePreference, 'dark');
assert.equal(documentElement.dataset.theme, 'dark');
assert.equal(storage.get('macrowatch.theme-preference'), 'dark');
assert.equal(fetchCount, 0, 'theme changes must not cause data fetches');

media.matches = true;
theme.applyPreference('light');
media.listener?.({ matches: false });
assert.equal(documentElement.dataset.theme, 'light', 'manual light must ignore system changes');
theme.applyPreference('system');
media.matches = true;
media.listener?.({ matches: true });
assert.equal(documentElement.dataset.theme, 'dark', 'system preference must follow media-query changes');
media.matches = false;
media.listener?.({ matches: false });
assert.equal(documentElement.dataset.theme, 'light');

assert.match(source, /<option value="system">시스템 설정<\/option>/);
assert.match(source, /<option value="light">라이트 모드<\/option>/);
assert.match(source, /<option value="dark">다크 모드<\/option>/);
assert.match(source, /\.select\('theme_preference'\)/);
assert.match(source, /\.rpc\('set_theme_preference'/);
assert.match(source, /macrowatch:themechange/);
assert.doesNotMatch(source, /filter\s*:\s*invert/i);
assert.ok(headChildren.some((node) => node.id === 'macrowatch-theme-styles'), 'theme tokens should be installed once');

console.log('theme contracts ok');
