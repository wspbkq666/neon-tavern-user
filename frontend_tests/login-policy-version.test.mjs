import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

test('registration sends the same policy versions required by the backend', async () => {
  const root = new URL('../', import.meta.url);
  const [frontend, backend] = await Promise.all([
    readFile(new URL('frontend_dist/assets/prototype-login.js', root), 'utf8'),
    readFile(new URL('core/auth_api.py', root), 'utf8'),
  ]);
  const frontendPrivacy = frontend.match(/const PRIVACY_POLICY_VERSION = '([^']+)'/);
  const backendPrivacy = backend.match(/PRIVACY_POLICY_VERSION = "([^"]+)"/);
  const frontendRules = frontend.match(/const USAGE_RULES_VERSION = '([^']+)'/);
  const backendRules = backend.match(/USAGE_RULES_VERSION = "([^"]+)"/);
  assert.ok(frontendPrivacy && backendPrivacy && frontendRules && backendRules);
  assert.equal(frontendPrivacy[1], backendPrivacy[1]);
  assert.equal(frontendRules[1], backendRules[1]);
});
