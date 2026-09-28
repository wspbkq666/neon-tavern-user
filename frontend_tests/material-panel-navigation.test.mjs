import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const scriptPath = new URL('../frontend_dist/assets/prototype-app.js', import.meta.url);

test('the final panel handler opens the materials panel and loads listings', async () => {
  const source = await readFile(scriptPath, 'utf8');
  const handler = source.match(/window\.openPanel = id => \{ closeSettingsPanels\(\);([\s\S]*?)\n\};/);

  assert.ok(handler, 'expected to find the final openPanel handler');
  assert.match(handler[1], /id === 'materials'/);
  assert.match(handler[1], /original\.openPanel\(id\)/);
  assert.match(handler[1], /loadMarket\(\)/);
});
