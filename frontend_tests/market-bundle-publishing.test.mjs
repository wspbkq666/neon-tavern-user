import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const read = path => readFile(new URL(path, root), 'utf8');

test('market publish form offers bundle composition using the existing bundle sheet', async () => {
  const [html, script] = await Promise.all([
    read('frontend_dist/index.html'),
    read('frontend_dist/assets/prototype-app.js'),
  ]);

  assert.match(html, /<option value="bundle">整合包<\/option>/);
  assert.match(html, /id="marketPublishBundle"/);
  assert.match(html, /data-bundle-market/);
  assert.match(script, /marketState\.bundleSelection/);
});

test('bundle listings submit selected character and worldbook IDs and render bundle details', async () => {
  const script = await read('frontend_dist/assets/prototype-app.js');

  assert.match(script, /body\.character_ids\s*=\s*marketState\.bundleSelection\.character_ids/);
  assert.match(script, /body\.worldbook_ids\s*=\s*marketState\.bundleSelection\.worldbook_ids/);
  assert.match(script, /neon-tavern-bundle/);
  assert.match(script, /已导入.*张角色卡.*本世界书/);
  assert.match(script, /api\('\/api\/bundles\/import\/preview\/'/);
  assert.match(script, /renderBundlePreview\(preview\)/);
});

test('market backend snapshots and imports bundle packages as a distinct kind', async () => {
  const [api, model] = await Promise.all([
    read('core/market_api.py'),
    read('core/models.py'),
  ]);

  assert.match(model, /BUNDLE\s*=\s*"bundle"/);
  assert.match(api, /export_bundle\(/);
  assert.match(api, /parse_bundle\(/);
  assert.match(api, /commit_bundle\(/);
  assert.match(api, /MarketListing\.BUNDLE/);
});

test('market publishing requires all metadata and selected source content', async () => {
  const [html, script, api] = await Promise.all([
    read('frontend_dist/index.html'),
    read('frontend_dist/assets/prototype-app.js'),
    read('core/market_api.py'),
  ]);

  for (const id of ['marketPublishKind', 'marketPublishSource', 'marketPublishScope', 'marketPublishName', 'marketPublishDescription', 'marketPublishTags', 'marketPublishAlias']) {
    assert.match(html, new RegExp(`id="${id}"[^>]*required|<[^>]+required[^>]*id="${id}"`), `${id} should be required`);
  }
  assert.match(script, /marketState\.bundleSelection\.character_ids\.length\s*\+\s*marketState\.bundleSelection\.worldbook_ids\.length/);
  assert.match(api, /简介不能为空/);
  assert.match(api, /署名不能为空/);
  assert.match(api, /至少填写一个标签/);
  assert.match(api, /整合包至少选择一张角色卡或一本世界书/);
  assert.match(api, /scope\s*=\s*data\.get\("scope"\)\s*$/m);
});
