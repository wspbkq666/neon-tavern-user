import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../frontend_dist/index.html', import.meta.url), 'utf8');

test('worldbook categories render their entries and keep details content visible', () => {
  assert.match(js, /entry\.category_ids/);
  assert.match(js, /category-count/);
  assert.match(html, /details\.worldbook-category[^{}]*\{[^}]*display:block/);
});

test('worldbook entries expose visual classes for each trigger mode', () => {
  assert.match(js, /worldBookEntryClass\(entry\.trigger_mode\)/);
  assert.match(js, /worldbook-entry-mark/);
  assert.match(html, /\.worldbook-entry-keyword/);
  assert.match(html, /\.worldbook-entry-always/);
  assert.match(html, /\.worldbook-entry-manual/);
});

test('worldbook folders start collapsed and the entry editor owns the viewport', () => {
  assert.match(js, /<details class="worldbook-category"><summary/);
  assert.match(js, /worldbook-editor-open/);
  assert.match(html, /body\.worldbook-editor-open \.app>\.bottom/);
});

test('imports JSON through a mobile-compatible file reader for both transfer types', () => {
  assert.match(js, /async function readJsonFile\(file\)/);
  assert.match(js, /payload: await readJsonFile\(file\)/);
  assert.match(js, /function readAsText\(file\)/);
});

test('worldbook category delete buttons stay clickable inside collapsed summaries', () => {
  assert.match(js, /data-category-delete/);
  assert.match(js, /if \(categoryDelete\) \{ event\.preventDefault\(\); event\.stopPropagation\(\);/);
  assert.match(js, /async function deleteWorldBookCategory\(id\)/);
  assert.match(html, /\.category-actions button\.danger/);
});

test('top-level worldbook categories expose their own delete action', () => {
  assert.match(js, /const categoryLevel = parentId === null \? 'root' : 'nested'/);
  assert.match(js, /data-category-level="\$\{categoryLevel\}"/);
  assert.match(js, /data-category-delete="\$\{h\(item\.id\)\}"/);
  assert.match(js, /删除顶级分类/);
});

test('worldbook settings keep the bottom navigation clickable', () => {
  assert.match(html, /\.settings-panel\{[^}]*z-index:20/);
  assert.match(html, /\.bottom\{[^}]*z-index:25/);
  assert.match(js, /function closeSettingsPanels\(\)/);
  assert.match(js, /window\.showConversations = .*closeSettingsPanels\(\)/);
  assert.match(js, /window\.openPanel = id => \{ closeSettingsPanels\(\)/);
});

test('the selected worldbook itself exposes a delete action', () => {
  assert.match(html, /id="worldBookDelete"/);
  assert.match(js, /async function deleteWorldBook\(\)/);
  assert.match(js, /worldBookDelete.*deleteWorldBook/);
});

test('generation picker exposes explicit participation, serial execution, and responsive cards', () => {
  assert.match(js, /data-role-toggle/);
  assert.match(js, /roleGrid\.addEventListener/);
  assert.match(js, /mode: 'serial'/);
  assert.match(html, /generation-role-grid/);
  assert.match(html, /generation-role-card/);
});
