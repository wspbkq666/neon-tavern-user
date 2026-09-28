import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const root = new URL('../', import.meta.url);
const read = path => readFile(new URL(path, root), 'utf8');

test('smart import has an independent panel with text and supported document inputs', async () => {
  const html = await read('frontend_dist/index.html');
  assert.match(html, /id="smartImport"/);
  assert.match(html, /id="smartImportText"/);
  assert.match(html, /accept="\.txt,\.md,\.docx,\.doc"/);
  assert.match(html, /openPanel\('smartImport'\)/);
});

test('final panel handler allows opening smart import', async () => {
  const script = await read('frontend_dist/assets/prototype-app.js');
  const handler = script.match(/window\.openPanel = id => \{ closeSettingsPanels\(\);([\s\S]*?)\n\};/);
  assert.ok(handler);
  assert.match(handler[1], /smartImport/);
});

test('drafts can change category and be skipped before final import', async () => {
  const [html, script] = await Promise.all([
    read('frontend_dist/index.html'),
    read('frontend_dist/assets/prototype-app.js'),
  ]);
  assert.match(html, /id="smartImportDrafts"/);
  assert.match(script, /data-smart-import-type/);
  assert.match(script, /data-smart-import-skip/);
  assert.match(script, /smartImportBuildPayload/);
});

test('preview stages the model result and bundle validation before explicit commit', async () => {
  const script = await read('frontend_dist/assets/prototype-app.js');
  const analyze = script.indexOf('/api/smart-import/preview/');
  const bundlePreview = script.indexOf('/api/bundles/import/preview/', analyze);
  const commit = script.indexOf('/api/bundles/import/commit/', bundlePreview);
  assert.ok(analyze >= 0 && bundlePreview > analyze && commit > bundlePreview);
  assert.match(script, /smartImportConfirm/);
  assert.match(script, /smartImportState\.preview/);
});

test('the form supports failed request recovery and displays Chinese warnings', async () => {
  const [html, script] = await Promise.all([
    read('frontend_dist/index.html'),
    read('frontend_dist/assets/prototype-app.js'),
  ]);
  assert.match(html, /id="smartImportStatus"/);
  assert.match(script, /识别失败/);
  assert.match(script, /finally/);
  assert.match(script, /smartImportText/);
});
