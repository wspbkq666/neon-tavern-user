import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const adminHtml = read('../frontend_dist/admin.html');
const adminScript = read('../frontend_dist/assets/prototype-admin.js');
const appScript = read('../frontend_dist/assets/prototype-app.js');
const urls = read('../tavern/urls.py');
const settingsApi = read('../core/settings_api.py');
const federationClient = read('../core/site_federation_client.py');

test('user edition removes global controls but keeps site administrator tools', () => {
  for (const source of [adminHtml, adminScript, appScript, urls, settingsApi]) {
    assert.doesNotMatch(source, /is_global_admin|data-global-admin-only|global_admin/);
    assert.doesNotMatch(source, /api\/(?:admin\/(?:update-policy|deployment-info|site-admins)|update-policy)\//);
  }

  assert.match(adminHtml, /id="deleteUser"/);
  assert.match(adminHtml, /本站默认配置/);
  assert.match(adminScript, /api\('\/api\/admin\/defaults\//);
  assert.match(adminScript, /api\('\/api\/admin\/users\//);
  assert.match(urls, /path\("api\/admin\/defaults\/", settings_api\.admin_defaults\)/);
  assert.match(settingsApi, /if not request\.user\.is_staff:/);
  assert.match(adminHtml, /id="federationStatus"/);
  assert.match(adminScript, /api\('\/api\/federation\/status\//);
  assert.match(federationClient, /TARGET_CENTRAL_URLS/);
});
