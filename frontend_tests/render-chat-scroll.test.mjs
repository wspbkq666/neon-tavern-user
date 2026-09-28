import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');

test('chat rendering keeps the users scroll position while generating', () => {
  assert.match(js, /function shouldFollowChatBottom\(container\)/);
  assert.match(js, /const followChatBottom = forceBottom \|\| shouldFollowChatBottom\(\$\('#content'\)\)/);
  assert.match(js, /if \(followChatBottom\) \$\('#content'\)\.scrollTop = \$\('#content'\)\.scrollHeight/);
});
