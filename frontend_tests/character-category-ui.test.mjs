import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');

test('character categories are collapsed by default and player cards have their own group', () => {
  assert.match(js, /character-user-category/);
  assert.match(js, /我的角色卡/);
  assert.match(js, /!character\.is_player_controlled && \(memberships\.get\(character\.id\)/);
  assert.match(js, /const playerCards = state\.characters\.filter\(character => character\.is_player_controlled\)/);
  assert.doesNotMatch(js, /<details class="character-category" open>/);
});
