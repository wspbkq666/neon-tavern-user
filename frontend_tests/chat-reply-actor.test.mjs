import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const js = await readFile(new URL('../frontend_dist/assets/prototype-app.js', import.meta.url), 'utf8');

test('switching from chat to another main panel hides the chat view and composer', () => {
  assert.match(js, /window\.openPanel = id => \{ closeSettingsPanels\(\);\s*original\.showConversations\(\);/);
});

test('reply role picker is built from the current conversation participants', () => {
  assert.match(js, /replyActorId/);
  assert.match(js, /currentParticipants[^;]*data-reply-actor/);
  assert.match(js, /roleSheet[^\n]*addEventListener/);
});

test('sending a message uses the selected reply actor and keeps director messages on the player card', () => {
  assert.match(js, /const speaker = isOoc \? player : replyActor/);
  assert.match(js, /speaker_id: speaker\.id/);
  assert.match(js, /场外导演消息只能由玩家卡发送/);
});
