import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const html = readFileSync(new URL('../frontend_dist/index.html', import.meta.url), 'utf8');
const app = readFileSync(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');
const urls = readFileSync(new URL('../tavern/urls.py', import.meta.url), 'utf8');

test('site warnings are visible in the account panel and acknowledge through a user-scoped API', () => {
  assert.match(html, /id="accountWarnings"/);
  assert.match(app, /escapeHtml|\bh\(item\.message\)/);
  assert.match(app, /api\('\/api\/account\/warnings\/read\//);
  assert.match(urls, /api\/account\/warnings\/read/);
});
