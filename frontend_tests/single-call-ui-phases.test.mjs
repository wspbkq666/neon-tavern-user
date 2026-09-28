import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');

test('generation progress renders thought before the public response from one job update', () => {
  assert.match(js, /item\.thought \? `<div class="thought-panel">/);
  assert.match(js, /item\.partial \? `<small>正在生成公开回应/);
  assert.match(js, /Object\.entries\(state\.lastJob\.progress/);
});

test('generation progress renders measured phase timings', () => {
  assert.match(js, /生成计时/);
  assert.match(js, /first_token_ms/);
  assert.match(js, /response_complete_ms/);
});
