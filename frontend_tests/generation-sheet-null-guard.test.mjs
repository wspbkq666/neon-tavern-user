import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../frontend_dist/index.html', import.meta.url), 'utf8');

test('generation sheet supports the legacy sheet class and guards missing DOM nodes', () => {
  assert.match(js, /querySelector\('\.generation-sheet'\) \|\| generationOverlay\?\.querySelector\('\.sheet'\)/);
  assert.match(js, /if \(!generationOverlay \|\| !generationSheet\)/);
  assert.match(js, /generationSheet\.querySelector\('#generationRoleGrid'\)/);
});

test('generation script URL is versioned so mobile browsers do not keep the broken cached bundle', () => {
  assert.match(html, /prototype-app\.js'\s*%}\?v=20260923-adult-preference/);
});
