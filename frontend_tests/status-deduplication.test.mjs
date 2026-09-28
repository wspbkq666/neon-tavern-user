import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');

test('status cards unwrap legacy character-named status containers', () => {
  assert.match(js, /function normalizeStatusSource\(character, snapshot\)/);
  assert.match(js, /source\[character\.name\]/);
  assert.match(js, /delete source\[character\.name\]/);
});
