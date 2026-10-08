import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const script=await readFile(new URL('../frontend_dist/assets/story-tools.js',import.meta.url),'utf8');
const chat=await readFile(new URL('../frontend_dist/assets/prototype-app.js',import.meta.url),'utf8');

test('chat exposes independent story state and memory controls',()=>{
  assert.match(chat,/data-story-state/);
  assert.match(chat,/memoryButton\.textContent='故事记忆'/);
  assert.match(script,/revision:current\.revision/);
  assert.match(script,/selected_fields:/);
  assert.match(script,/data-template-key/);
});

test('locked facts use stable ids and memory status refreshes',()=>{
  assert.match(script,/data-fact-id/);
  assert.match(script,/crypto\.randomUUID\(\)/);
  assert.match(script,/locked_facts:facts/);
  assert.match(script,/clearInterval\(polling\)/);
  assert.match(script,/过期|未保存的编辑/);
});

test('checkpoints preview restore and allow named branches from a message boundary',()=>{
  assert.match(script,/export async function openStoryCheckpoints/);
  assert.match(script,/through_message_id/);
  assert.match(script,/has_story_snapshot/);
  assert.match(script,/action:'restore',revision:/);
  assert.match(script,/action:'fork',title:/);
  assert.match(script,/回档前自动存档/);
});
