'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');

const source = fs.readFileSync('assets/js/core/frontend-core.js', 'utf8');
const frontendExport = source.indexOf('window.MacroWatchFrontend =');
const themeExport = source.indexOf('window.MacroWatchTheme =');
const integrationStart = source.indexOf('installThemeStyles();');

assert.ok(frontendExport >= 0);
assert.ok(themeExport > frontendExport);
assert.ok(integrationStart > themeExport);

console.log('shared frontend APIs are published before dependent integrations: ok');
