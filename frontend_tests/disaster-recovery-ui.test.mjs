import assert from 'node:assert/strict';
import test from 'node:test';
import { evaluateDisasterRecoveryState, startDisasterRecoveryMonitor } from '../frontend_dist/assets/disaster-recovery.js';

const now = Date.now();
const status = (node_id, role, epoch, holder_id = node_id) => ({
  node_id,
  role,
  epoch,
  authority: {
    status: 'confirmed',
    holder_id,
    epoch,
    expires_at: Math.floor(now / 1000) + 60,
  },
});

test('redirect happens only when standby confirms its own valid higher epoch', () => {
  const oldPrimary = { ...status('154', 'read_only', 1), authority: status('123', 'primary', 2).authority };
  const result = evaluateDisasterRecoveryState(oldPrimary, status('123', 'primary', 2), {
    standbyNodeId: '123', standbyOrigin: 'https://backup.example', pathname: '/app/', now,
  });
  assert.equal(result.state, 'failover_confirmed');
  assert.equal(result.redirect, 'https://backup.example/app/?failover=1');
});

test('a newer verified standby epoch wins over a just-stale local primary response', () => {
  const result = evaluateDisasterRecoveryState(status('154', 'primary', 1), status('123', 'primary', 2), {
    standbyNodeId: '123', standbyOrigin: 'https://backup.example', pathname: '/app/', now,
  });
  assert.equal(result.state, 'failover_confirmed');
});

test('a site timeout does not redirect while standby authority still names the old primary', () => {
  const result = evaluateDisasterRecoveryState(null, status('123', 'read_only', 1, '154'), {
    standbyNodeId: '123', standbyOrigin: 'https://backup.example', now,
  });
  assert.equal(result.state, 'site_unreachable');
  assert.equal(result.redirect, '');
});

test('same, stale, expired, mismatched, and unavailable standby authority never redirects', () => {
  const local = status('154', 'primary', 2);
  const candidates = [
    status('123', 'primary', 2),
    status('123', 'primary', 1),
    { ...status('123', 'primary', 3), authority: { ...status('123', 'primary', 3).authority, expires_at: 1 } },
    status('other', 'primary', 3),
    { ...status('123', 'primary', 3), authority: { status: 'unavailable', holder_id: '123', epoch: 3 } },
  ];
  for (const standby of candidates) {
    assert.equal(evaluateDisasterRecoveryState(local, standby, {
      standbyNodeId: '123', standbyOrigin: 'https://backup.example', now,
    }).redirect, '');
  }
});

test('monitor probes only site status APIs and never sends browser credentials', async () => {
  const calls = [];
  const assigned = [];
  const monitor = startDisasterRecoveryMonitor({
    localStatusUrl: '/api/disaster-recovery/status/',
    standbyStatusUrl: 'https://backup.example/api/disaster-recovery/status/',
    standbyNodeId: '123', standbyOrigin: 'https://backup.example',
    location: { pathname: '/login/', assign: value => assigned.push(value) },
    onState: () => {}, intervalMs: 100000,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        json: async () => url.startsWith('https:')
          ? status('123', 'read_only', 1, '154')
          : status('154', 'primary', 1),
      };
    },
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  monitor.stop();

  assert.deepEqual(assigned, []);
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url, options }) => !url.includes('/api/lease/status/')
    && options.credentials === 'omit'
    && options.cache === 'no-store'));
});

test('monitor redirects to standby after both public site APIs confirm the new epoch', async () => {
  const assigned = [];
  const monitor = startDisasterRecoveryMonitor({
    localStatusUrl: '/api/disaster-recovery/status/',
    standbyStatusUrl: 'https://backup.example/api/disaster-recovery/status/',
    standbyNodeId: '123', standbyOrigin: 'https://backup.example',
    location: { pathname: '/login/', assign: value => assigned.push(value) },
    onState: () => {}, intervalMs: 100000,
    fetchImpl: async url => ({
      ok: true,
      json: async () => url.startsWith('https:')
        ? status('123', 'primary', 2)
        : status('154', 'read_only', 1, '123'),
    }),
  });
  await new Promise(resolve => setTimeout(resolve, 10));
  monitor.stop();

  assert.deepEqual(assigned, ['https://backup.example/login/?failover=1']);
});

test('redirect URL rejects non-HTTPS origins and never preserves unsafe routes', () => {
  const oldPrimary = { ...status('154', 'read_only', 1), authority: status('123', 'primary', 2).authority };
  assert.equal(evaluateDisasterRecoveryState(oldPrimary, status('123', 'primary', 2), {
    standbyNodeId: '123', standbyOrigin: 'http://backup.example', now,
  }).state, 'configuration_error');
  assert.equal(evaluateDisasterRecoveryState(oldPrimary, status('123', 'primary', 2), {
    standbyNodeId: '123', standbyOrigin: 'https://backup.example', pathname: '/admin/?token=secret', now,
  }).redirect, 'https://backup.example/app/?failover=1');
});
