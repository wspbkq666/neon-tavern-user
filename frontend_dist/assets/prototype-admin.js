import { api, escapeHtml as h, listFrom, dateLabel } from './common.js';

const state = { me: null, users: [], detail: null, conversation: null, defaults: null };
const $ = selector => document.querySelector(selector);
const notifyError = error => window.alert(error.message);
const initialShow = window.show;
window.show = id => {
  initialShow(id);
  if (id === 'users') loadUsers().catch(notifyError);
  if (id === 'globaldefaults') loadDefaults().catch(notifyError);
  if (id === 'marketAdmin') loadMarketAdmin().catch(notifyError);
};

async function loadMarketAdmin() {
  const [connection, sites, reports, listings] = await Promise.all([
    api('/api/admin/market/connection/'), api('/api/admin/market/sites/'),
    api('/api/admin/market/reports/'), api('/api/admin/market/listings/'),
  ]);
  $('#adminMarketUrl').value = connection.market_url || '';
  $('#adminMarketConnectionStatus').textContent = connection.is_market_host
    ? `当前部署是公共市场主站 · 站点编号 ${connection.site_id}`
    : `已配置公共市场 · 站点编号 ${connection.site_id} · 私钥${connection.has_private_key ? '已安全保存' : '未生成'}`;
  const siteRows = listFrom(sites, 'sites');
  $('#marketSitesList').innerHTML = siteRows.map(site => `<article class="market-admin-item"><b>${h(site.name)}</b><p>状态：${h(site.status === 'approved' ? '已批准' : site.status === 'revoked' ? '已撤销' : '待审批')} · 公钥指纹 ${h(site.fingerprint)}<br>登记时间：${dateLabel(site.created_at)}</p><div class="market-admin-actions">${site.status !== 'approved' ? `<button type="button" data-market-site-action="approve" data-site-id="${h(site.site_id)}">批准接入</button>` : ''}${site.status !== 'revoked' ? `<button type="button" class="danger" data-market-site-action="revoke" data-site-id="${h(site.site_id)}">撤销接入</button>` : ''}</div></article>`).join('') || '<div class="market-empty">暂无登记站点。连接其他部署后，其公钥会出现在这里。</div>';
  const reportRows = listFrom(reports, 'reports');
  $('#marketReportsList').innerHTML = reportRows.map(report => `<article class="market-admin-item"><b>${h(report.title)}</b><p>${h(report.reason)}<br>举报人：${h(report.reporter)} · ${h(report.status === 'open' ? '待处理' : report.status === 'reviewed' ? '已处理' : '已驳回')} · ${dateLabel(report.created_at)}</p>${report.status === 'open' ? `<div class="market-admin-actions"><button type="button" class="danger" data-market-report-action="hide" data-report-id="${h(report.id)}">下架素材</button><button type="button" data-market-report-action="dismiss" data-report-id="${h(report.id)}">驳回举报</button></div>` : ''}</article>`).join('') || '<div class="market-empty">暂无举报</div>';
  const listingRows = listFrom(listings, 'listings');
  $('#marketAdminListings').innerHTML = listingRows.map(item => `<article class="market-admin-item"><b>${h(item.title)}</b><p>${h(item.kind === 'character' ? '角色卡' : '世界书')} · ${h(item.scope === 'public' ? '公共' : '本站')} · ${h(item.origin)} · ${h(item.author_alias || '匿名')}<br>状态：${h(item.status === 'published' ? '已发布' : item.status === 'hidden' ? '已隐藏' : '已撤回')}</p>${item.status !== 'withdrawn' ? `<div class="market-admin-actions"><button type="button" class="${item.status === 'published' ? 'danger' : ''}" data-market-listing-action="${item.status === 'published' ? 'hide' : 'restore'}" data-listing-id="${h(item.id)}">${item.status === 'published' ? '隐藏素材' : '恢复展示'}</button></div>` : ''}</article>`).join('') || '<div class="market-empty">暂无市场素材</div>';
}

async function saveMarketConnection(rotate = false) {
  const marketUrl = $('#adminMarketUrl').value.trim();
  if (rotate && !window.confirm('重新生成后，其他市场需要使用新密钥重新登记并等待批准。确定继续吗？')) return;
  const result = await api('/api/admin/market/connection/', { method: 'PUT', body: { market_url: marketUrl, ...(rotate ? { rotate_key: true } : {}) } });
  $('#adminMarketConnectionStatus').textContent = result.site_status === '本机公共市场'
    ? `当前部署作为公共市场主站 · 站点编号 ${result.site_id || '本地'}`
    : `连接登记状态：${result.site_status === 'approved' ? '已批准' : result.site_status === 'revoked' ? '已撤销' : '等待公共市场管理员批准'}`;
  await loadMarketAdmin();
  window.alert('公共市场设置已保存');
}

async function marketAdminAction(path, body) {
  await api(path, { method: 'POST', body });
  await loadMarketAdmin();
}

async function loadUsers() {
  const [usersResponse, federationResponse] = await Promise.all([
    api('/api/admin/users/'),
    api('/api/federation/status/'),
  ]);
  state.users = listFrom(usersResponse, 'users');
  const targets = listFrom(federationResponse, 'targets');
  $('#federationStatus').textContent = targets.map(target => {
    const name = target.target_key === 'main_154' ? '总站 154' : '总站 123';
    const status = target.active ? '已连接' : '未登记或已停用';
    const pending = Number(target.pending_chat || 0) + Number(target.pending_users || 0);
    const error = target.last_error_code ? ` · 最近错误：${target.last_error_code}` : '';
    return `${name} ${status} · 待同步 ${pending}${error}`;
  }).join('；') || '尚未登记总站';
  const stats = document.querySelectorAll('.stats .stat b');
  stats[0].textContent = state.users.length;
  stats[1].textContent = state.users.reduce((total, user) => total + user.conversation_count, 0);
  stats[2].textContent = state.users.filter(user => user.last_login && Date.now() - Date.parse(user.last_login) < 86400000).length;
  $('.userlist').innerHTML = state.users.map(user => `<button class="user" data-id="${user.id}" data-name="${h(user.username)}"><div class="avatar">${h(user.username.slice(0, 1))}</div><div class="userinfo"><b>${h(user.username)}</b><small>${user.conversation_count} 次对话 · ${user.character_count} 张角色卡</small></div><div class="go">›</div></button>`).join('') || '<p class="notice">暂无用户</p>';
  window.filterUsers($('#q').value);
}

async function openUser(id) {
  state.detail = await api(`/api/admin/users/${encodeURIComponent(id)}/`);
  const user = state.detail.user;
  $('#username').textContent = user.username;
  $('#loginAccount').textContent = user.username;
  $('.phonepreview .conversation').innerHTML = listFrom(state.detail, 'conversations').map(item => `<button class="conv" data-conversation="${h(item.id)}"><b>${h(item.title)}</b><p>${h((item.participants || []).map(participant => participant.name).join('、'))} · ${dateLabel(item.updated_at)}</p></button>`).join('') || '<p>暂无对话</p>';
  $('#deleteUser').hidden = !state.me?.is_admin;
  initialShow('userdetail');
}

async function openConversation(id) {
  const userId = state.detail.user.id;
  state.conversation = await api(`/api/admin/users/${userId}/conversations/${encodeURIComponent(id)}/`);
  const conversation = state.conversation;
  const names = Object.fromEntries((conversation.participants || []).map(character => [character.id, character.name]));
  $('.phonepreview .conversation').innerHTML = `<button class="conv" data-back="true">‹ 返回对话列表</button>${(conversation.messages || []).map(message => `<div class="conv"><b>${h(names[message.speaker_id] || '旁白')}</b><p>${h(message.content)}</p></div>`).join('') || '<p>暂无消息</p>'}`;
}

async function loadDefaults() {
  state.defaults = await api('/api/admin/defaults/');
  const values = state.defaults.defaults || {};
  $('#defaultApiBase').value = values.api_base_url || 'https://api.deepseek.com';
  $('#defaultApiBase').readOnly = true;
  $('#defaultApiKey').value = ''; $('#defaultApiKey').placeholder = state.defaults.has_api_key ? '已配置（留空保持不变）' : '填写默认密钥';
  $('#defaultModel').value = values.model || 'deepseek-flash';
  $('#defaultPrompt').value = values.global_prompt || '';
  $('#defaultTemperature').value = values.temperature ?? '';
  $('#defaultTopP').value = values.top_p ?? '';
  $('#defaultMaxTokens').value = values.max_tokens ?? '';
  $('#defaultWelcome').value = values.welcome_message || '';
  $('#defaultScene').value = values.scene_defaults?.地点 || '';
  const switches = $$('.global-card .admin-switch input');
  ['strict_persona', 'auto_state_extraction', 'stream_output', 'save_raw_response'].forEach((key, index) => { switches[index].checked = !!values[key]; });
}

function $$(selector) { return [...document.querySelectorAll(selector)]; }

async function saveDefaults() {
  const switches = $$('.global-card .admin-switch input');
  const body = {
    model: $('#defaultModel').value.trim(), global_prompt: $('#defaultPrompt').value,
    temperature: Number($('#defaultTemperature').value), top_p: Number($('#defaultTopP').value),
    max_tokens: Number($('#defaultMaxTokens').value), welcome_message: $('#defaultWelcome').value,
    scene_defaults: { 地点: $('#defaultScene').value },
    strict_persona: switches[0].checked, auto_state_extraction: switches[1].checked,
    stream_output: switches[2].checked, save_raw_response: switches[3].checked,
  };
  if ($('#defaultApiKey').value) body.api_key = $('#defaultApiKey').value;
  await api('/api/admin/defaults/', { method: 'PUT', body });
  $('#defaultApiKey').value = '';
  window.alert('本站默认配置已保存');
}

function showAdminLogin(message = '') {
  initialShow('adminLogin');
  const error = $('#adminLoginError');
  error.textContent = message;
  error.hidden = !message;
}

function enterAdminPanel(session) {
  state.me = session;
  $('#adminIdentity').textContent = session.username;
  $('#adminAccountName').textContent = session.username;
  $$('[data-site-admin-or-global]').forEach(element => { element.hidden = !session.is_admin; });
  initialShow('users');
  loadUsers().catch(notifyError);
}

async function submitAdminLogin() {
  const username = $('#adminLoginUsername').value.trim();
  const password = $('#adminLoginPassword').value;
  if (!username || !password) return showAdminLogin('请输入管理员账号和密码');
  const button = $('#adminLoginSubmit');
  button.disabled = true;
  try {
    const session = await api('/api/auth/login/', { method: 'POST', body: { username, password }, redirectOnUnauthorized: false });
    if (!session.is_admin) {
      await api('/api/auth/logout/', { method: 'POST', body: {}, redirectOnUnauthorized: false });
      throw new Error('这个账号不是管理员，请从普通登录页进入酒馆');
    }
    enterAdminPanel(session);
  } catch (error) {
    showAdminLogin(error.message);
    button.disabled = false;
  }
}

window.showGlobalDefaults = () => { initialShow('globaldefaults'); loadDefaults().catch(notifyError); };
window.openUser = name => { const user = state.users.find(item => item.username === name); if (user) openUser(user.id).catch(notifyError); };
window.saveResetPassword = async () => {
  const password = $('#newPassword').value;
  if (!password || password !== $('#newPassword2').value) { window.alert('请确认两次输入的新密码'); return; }
  try {
    await api(`/api/admin/users/${state.detail.user.id}/reset-password/`, { method: 'POST', body: { new_password: password } });
    window.closeResetPassword();
    $('#newPassword').value = ''; $('#newPassword2').value = '';
    window.alert('新密码已设置');
  } catch (error) { notifyError(error); }
};

window.deleteSelectedUser = async () => {
  const user = state.detail?.user;
  if (!user || !state.me?.is_admin || !window.confirm(`确定删除账号 ${user.username} 的全部角色、对话和世界书数据吗？`)) return;
  const confirmation = window.prompt(`请输入账号名 ${user.username} 以确认删除`);
  if (confirmation !== user.username) return window.alert('账号名不一致，已取消删除');
  try {
    await api(`/api/admin/users/${state.detail.user.id}/`, { method: 'DELETE', body: { confirm_username: confirmation } });
    window.alert('用户及其全部数据已删除');
    state.detail = null;
    initialShow('users');
    await loadUsers();
  } catch (error) { notifyError(error); }
};

document.addEventListener('click', event => {
  const user = event.target.closest('.userlist .user[data-id]');
  if (user) { event.stopPropagation(); openUser(user.dataset.id).catch(notifyError); }
  const conversation = event.target.closest('[data-conversation]');
  if (conversation) openConversation(conversation.dataset.conversation).catch(notifyError);
  if (event.target.closest('[data-back]')) openUser(state.detail.user.id).catch(notifyError);
});
$('#saveGlobal').addEventListener('click', () => saveDefaults().catch(notifyError));
$('#createSiteAdmin').addEventListener('click', () => createSiteAdmin().catch(notifyError));
$('#siteAdminSite').addEventListener('change', () => {
  $('#siteAdminCustomSiteWrap').hidden = $('#siteAdminSite').value !== '__custom__';
  renderSiteAdminAccounts(state.users);
});
$('#siteAdminCustomSite').addEventListener('input', () => renderSiteAdminAccounts(state.users));
$('#publishUpdate').addEventListener('click', () => publishUpdate().catch(notifyError));
$('#saveMarketConnection').addEventListener('click', () => saveMarketConnection().catch(notifyError));
$('#rotateMarketKey').addEventListener('click', () => saveMarketConnection(true).catch(notifyError));
document.addEventListener('click', event => {
  const site = event.target.closest('[data-market-site-action]');
  if (site) return marketAdminAction('/api/admin/market/sites/', { site_id: site.dataset.siteId, action: site.dataset.marketSiteAction }).catch(notifyError);
  const report = event.target.closest('[data-market-report-action]');
  if (report) return marketAdminAction('/api/admin/market/reports/', { report_id: report.dataset.reportId, action: report.dataset.marketReportAction }).catch(notifyError);
  const listing = event.target.closest('[data-market-listing-action]');
  if (listing) return marketAdminAction(`/api/admin/market/listings/${encodeURIComponent(listing.dataset.listingId)}/`, { action: listing.dataset.marketListingAction }).catch(notifyError);
});
$('#adminLoginSubmit').addEventListener('click', () => submitAdminLogin());
$('#adminLoginPassword').addEventListener('keydown', event => { if (event.key === 'Enter') submitAdminLogin(); });
$('#openUserAccount').addEventListener('click', () => {
  if (!state.detail?.user?.id) return;
  location.assign(`/app/?admin_view=${encodeURIComponent(state.detail.user.id)}`);
});
$('#auditEntry').addEventListener('click', async () => {
  try {
    const data = await api('/api/admin/audit/');
    $('#auditList').innerHTML = listFrom(data, 'entries').map(entry => `<div class="row"><span>${h(entry.action)} · ${h(entry.target_user || '')}</span><small>${dateLabel(entry.created_at)}</small></div>`).join('') || '<div class="notice">暂无操作记录</div>';
    initialShow('audit');
  } catch (error) { notifyError(error); }
});
$('#adminLogout').addEventListener('click', async () => {
  try {
    await api('/api/auth/logout/', { method: 'POST', body: {} });
    location.assign('/admin/');
  } catch (error) { notifyError(error); }
});

api('/api/auth/me/').then(session => {
  if (!session.authenticated || !session.is_admin) {
    showAdminLogin(session.authenticated ? '当前账号不是管理员，请使用管理员账号登录' : '');
    return;
  }
  enterAdminPanel(session);
}).catch(notifyError);

$('#deleteUser').addEventListener('click', () => window.deleteSelectedUser());
