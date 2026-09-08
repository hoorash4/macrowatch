const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');

test('all page-local scripts and styles resolve without changing document entry URLs', () => {
  for (const entry of ['index.html', 'admin.html']) {
    const html = fs.readFileSync(path.join(root, entry), 'utf8');
    for (const [, url] of html.matchAll(/<(?:script|link)\b[^>]*?(?:src|href)="([^"]+)"/g)) {
      if (/^(?:https?:|\/\/|#)/.test(url)) continue;
      const target = path.resolve(root, url.split('?')[0]);
      assert.ok(target.startsWith(root + path.sep), url);
      assert.ok(fs.statSync(target).isFile(), `${entry}: ${url}`);
    }
  }
});

test('stylesheet images resolve relative to the stylesheet, not the document', () => {
  const cssFile = path.join(root, 'assets/css/styles.css');
  const css = fs.readFileSync(cssFile, 'utf8');
  for (const [, url] of css.matchAll(/url\(["']?([^"')]+)["']?\)/g)) {
    if (/^(?:https?:|data:|#)/.test(url)) continue;
    assert.ok(fs.statSync(path.resolve(path.dirname(cssFile), url)).isFile(), url);
  }
});
