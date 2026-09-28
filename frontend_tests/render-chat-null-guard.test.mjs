import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');

test('chat refresh does not write to replaced generation or role sheet nodes', () => {
  assert.match(js, /const roleSheetBody = roleSheet\?\.querySelector\('\.sheet'\);/);
  assert.match(js, /if \(roleSheetBody\) roleSheetBody\.innerHTML/);
  assert.match(js, /if \(roleSheet && roleSheetBody && !roleSheetBody\.dataset\.replyBinding\)/);
  assert.match(js, /const legacyRoleGrid = \$\('#sheet \.role-grid'\);/);
  assert.match(js, /if \(legacyRoleGrid\) legacyRoleGrid\.innerHTML/);
});
