import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

const root = path.resolve(import.meta.dirname, '..');
const html = fs.readFileSync(path.join(root, 'frontend_dist', 'index.html'), 'utf8');
const js = fs.readFileSync(path.join(root, 'frontend_dist', 'assets', 'prototype-app.js'), 'utf8');

test('adult preference UI exposes recorded keywords and examples', () => {
  assert.match(html, /id="adultPreferenceKeywords"/);
  assert.match(html, /id="adultPreferenceView"/);
  assert.match(html, /data-adult-example/);
  assert.match(js, /adult_content_keywords/);
  assert.match(js, /adultPreferenceKeywords/);
  assert.match(js, /adultPreferenceView/);
});

test('adult preference UI only exposes keyword controls when enabled', () => {
  assert.match(js, /function renderAdultPreferencePanel[\s\S]*panel\.hidden = !enabled/);
  assert.match(js, /adult_content_preference/);
  assert.match(js, /splitPreferenceKeywords/);
});
