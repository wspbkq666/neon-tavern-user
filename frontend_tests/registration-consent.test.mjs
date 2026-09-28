import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

const html = await readFile(new URL('../frontend_dist/login.html', import.meta.url), 'utf8');
const js = await readFile(new URL('../frontend_dist/assets/prototype-login.js', import.meta.url), 'utf8');

test('registration requires consent and provides readable policy dialogs', () => {
  assert.match(html, /id="policyConsent"[^>]*type="checkbox"[^>]*required/);
  assert.match(html, /data-policy-dialog="privacyPolicyDialog"/);
  assert.match(html, /data-policy-dialog="usageRulesDialog"/);
  assert.match(html, /本站与其他霓虹酒馆站点相互独立/);
  assert.match(html, /账号目录及你在确认本协议后新增或修改的完整私聊消息/);
  assert.match(html, /分别同步到两个总站，并在总站长期保存/);
  assert.match(html, /id="registerSubmit"[^>]*disabled/);
  assert.match(js, /policyConsent\.checked/);
  assert.match(js, /policy_consent: true/);
  assert.match(js, /privacy_policy_version:/);
  assert.match(js, /usage_rules_version:/);
});

test('existing accounts must accept the current policy before using site APIs', () => {
  assert.match(html, /id="policyUpdateRequired"/);
  assert.match(js, /session\.policy_consent_required/);
  assert.match(js, /api\('\/api\/auth\/consent\//);
});
