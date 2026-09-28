function authorityIsCurrent(status, now) {
  const authority = status?.authority;
  return Boolean(authority?.status === 'confirmed'
    && Number.isInteger(Number(authority.epoch))
    && Number(authority.epoch) > 0
    && Number(authority.expires_at) * 1000 > now);
}

function safeStandbyOrigin(value) {
  try {
    const standby = new URL(value);
    if (standby.protocol !== 'https:' || standby.username || standby.password || standby.search || standby.hash) return null;
    return standby;
  } catch {
    return null;
  }
}

export function evaluateDisasterRecoveryState(local, standbyStatus, {
  standbyNodeId = '', standbyOrigin = '', pathname = '/app/', now = Date.now(),
} = {}) {
  const standby = safeStandbyOrigin(standbyOrigin);
  if (!standbyNodeId || !standby) return { state: 'configuration_error', redirect: '' };

  const standbyIsPrimary = standbyStatus?.node_id === standbyNodeId
    && standbyStatus.role === 'primary'
    && authorityIsCurrent(standbyStatus, now)
    && standbyStatus.authority.holder_id === standbyNodeId
    && Number(standbyStatus.authority.epoch) === Number(standbyStatus.epoch);
  const observedLocalEpoch = Math.max(0, Number(local?.epoch || 0));
  if (standbyIsPrimary && Number(standbyStatus.epoch) > observedLocalEpoch && local?.node_id !== standbyNodeId) {
    const safePath = pathname === '/login/' ? '/login/' : '/app/';
    return { state: 'failover_confirmed', redirect: `${standby.origin}${safePath}?failover=1` };
  }

  if (local?.role === 'primary'
    && local.node_id
    && authorityIsCurrent(local, now)
    && local.authority.holder_id === local.node_id
    && Number(local.authority.epoch) === Number(local.epoch)) {
    return { state: 'primary', redirect: '' };
  }

  if (!local) return { state: standbyStatus ? 'site_unreachable' : 'arbitration_unavailable', redirect: '' };
  if (local.authority?.status !== 'confirmed' || standbyStatus?.authority?.status !== 'confirmed') {
    return { state: 'arbitration_unavailable', redirect: '' };
  }
  return { state: 'waiting_for_authority', redirect: '' };
}

function renderStatus(element, status, { local = null } = {}) {
  if (!element) return;
  const messages = {
    primary: '主站正常，灾备状态已由仲裁器确认。',
    site_unreachable: '本站暂时无法连接；备用站尚未确认接管，不会误跳转。',
    arbitration_unavailable: '仲裁状态暂时无法确认，请稍后重试。',
    waiting_for_authority: '备用站尚未取得新的有效主写任期。',
    failover_confirmed: '已确认备用站接管，正在前往备用站；请重新登录。',
    configuration_error: '备用站地址配置无效，请联系网站管理员。',
  };
  let message = messages[status] || messages.arbitration_unavailable;
  if (status === 'primary' && local?.replica_status === 'healthy') {
    const lag = Math.max(0, Number(local.replication_lag_seconds || 0));
    message = lag > 0 ? `主站正常 · 灾备副本数据点距今 ${Math.floor(lag / 60)} 分钟。` : messages.primary;
  } else if (status === 'primary' && local?.replica_status === 'stale') {
    message = `主站正常 · 灾备同步滞后 ${Math.floor(Number(local.replication_lag_seconds || 0) / 60)} 分钟，请管理员检查。`;
  } else if (status === 'primary' && local?.replica_status === 'error') {
    message = '主站正常 · 最近一次灾备复制失败，请管理员检查。';
  }
  element.textContent = message;
  element.hidden = false;
  element.dataset.state = status;
}

async function fetchJson(fetchImpl, url, timeoutMs) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {
      method: 'GET', cache: 'no-store', credentials: 'omit', signal: controller.signal,
      headers: { Accept: 'application/json' },
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

export function startDisasterRecoveryMonitor({
  localStatusUrl = '/api/disaster-recovery/status/', standbyStatusUrl,
  standbyNodeId, standbyOrigin, fetchImpl = fetch, location = window.location,
  onState = status => renderStatus(document.getElementById('disasterRecoveryStatus'), status),
  intervalMs = 15000, timeoutMs = 2500,
} = {}) {
  let stopped = false;
  let timer = null;
  let failureCount = 0;
  let redirected = false;

  const stop = () => { stopped = true; clearTimeout(timer); };
  const poll = async () => {
    if (stopped || redirected) return;
    const [localResult, standbyResult] = await Promise.allSettled([
      fetchJson(fetchImpl, localStatusUrl, timeoutMs),
      standbyStatusUrl ? fetchJson(fetchImpl, standbyStatusUrl, timeoutMs) : Promise.reject(new Error('standby status URL missing')),
    ]);
    const local = localResult.status === 'fulfilled' ? localResult.value : null;
    const standbyStatus = standbyResult.status === 'fulfilled' ? standbyResult.value : null;
    const result = evaluateDisasterRecoveryState(local, standbyStatus, {
      standbyNodeId, standbyOrigin, pathname: location.pathname,
    });
    failureCount = result.state === 'primary' ? 0 : failureCount + 1;
    onState(result.state, { local, standby: standbyStatus });
    if (result.redirect) {
      redirected = true;
      stop();
      location.assign(result.redirect);
      return;
    }
    const delay = Math.min(intervalMs * (2 ** Math.min(failureCount, 2)), 60000);
    if (!stopped) timer = setTimeout(poll, delay);
  };

  poll();
  return { stop, poll };
}

if (typeof document !== 'undefined') {
  const body = document.body;
  if (body?.dataset.drEnabled === 'true') {
    startDisasterRecoveryMonitor({
      standbyStatusUrl: body.dataset.drStandbyStatusUrl,
      standbyNodeId: body.dataset.drStandbyNodeId,
      standbyOrigin: body.dataset.drStandbyOrigin,
    });
  }
}
