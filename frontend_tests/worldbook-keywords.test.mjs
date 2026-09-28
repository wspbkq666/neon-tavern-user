import assert from 'node:assert/strict';
import test from 'node:test';

import { splitWorldBookKeywords } from '../frontend_dist/assets/worldbook-keywords.js';

test('splits worldbook keywords with common Chinese and English separators', () => {
  assert.deepEqual(
    splitWorldBookKeywords('酒馆,雨夜，旧街;规则；人物、城市|秘密'),
    ['酒馆', '雨夜', '旧街', '规则', '人物', '城市', '秘密'],
  );
});

test('treats spaces tabs and newlines as separators and removes duplicates', () => {
  assert.deepEqual(
    splitWorldBookKeywords('霓虹 酒馆\t雨夜\n旧街  霓虹'),
    ['霓虹', '酒馆', '雨夜', '旧街'],
  );
});

test('ignores empty separators and normalizes case for matching', () => {
  assert.deepEqual(splitWorldBookKeywords(' Door ,,，； door '), ['door']);
});
