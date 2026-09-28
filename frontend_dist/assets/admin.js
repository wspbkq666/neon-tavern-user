import { api, apiPath, avatar, dateLabel, escapeHtml as h, listFrom, settingsMarkup, settingsChanges, toast } from './common.js';

const state = { me: null, users: [], selectedUser: null, userDetail: null, conversation: null, defaults: null, view: 'users' };
const $ = selector => document.querySelector(selector);
const html = (selector, value) => { $(selector).innerHTML = value; };

function changeView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(element => element.classList.toggle('active', element.id === `admin-${view}`));
  document.querySelectorAll('[data-nav]').forEach(button => button.classList.toggle('active', button.dataset.nav === view || (['user', 'conversation'].includes(view) && button.dataset.nav === 'users')));
  $('#page-title').textContent = ({ users: '酒馆管理', user: state.selectedUser?.username || '用户', conversation: state.conversation?.title || '对话', defaults: '全局默认设置' })[view];
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function openModal(title, body) {
  html('#modal-root', `<div class="sheet-backdrop" data-backdrop="true"><section class="sheet" role="dialog" aria-modal="true" aria-label="${h(title)}"><div class="sheet-head"><h2>${h(title)}</h2><button type="button" data-action="close-modal" aria-label="关闭">×</button></div>${body}</section></div>`);
  $('.sheet input')?.focus();
}
function closeModal() { html('#modal-root', ''); }

async function loadUsers() {
  state.users = listFrom(await api('/api/admin/users/'), 'users');
  renderUsers();
}

function renderUsers() {
  html('#admin-users', `<div class="page-heading"><div><span class="eyebrow">ADMINISTRATION</span><h1>用户</h1><p>查看账号与故事</p></div><span class="count-badge">${state.users.length}</span></div>
    ${state.users.length ? `<label class="search-field"><input id="user-search" type="search" placeholder="搜索用户名" aria-label="搜索用户"></label><div class="stack">${state.users.map(user => `<button class="user-row" data-action="open-user" data-id="${h(user.id)}">${avatar(user)}<span class="item-body"><strong>${h(user.username || user.name || '用户')}</strong><small>${user.conversation_count != null ? `${h(user.conversation_count)} 个故事` : `账号 ID：${h(user.id)}`}</small></span><span class="row-chevron">›</span></button>`).join('')}</div>` : `<div class="empty"><h2>暂无用户</h2><p>用户出现后会显示在这里。</p></div>`}`);
}

async function openUser(id) {
  const data = await api(`/api/admin/users/${apiPath(id)}/`);
  state.userDetail = data;
  state.selectedUser = data.user || data;
  if (!state.selectedUser.id) state.selectedUser.id = id;
  renderUser(); changeView('user');
}

function userConversations() {
  return listFrom(state.userDetail, 'conversations').length ? listFrom(state.userDetail, 'conversations') : listFrom(state.selectedUser, 'conversations');
}

function renderUser() {
  const user = state.selectedUser;
  const conversations = userConversations();
  html('#admin-user', `<div class="back-line"><button class="icon-button" data-nav="users" aria-label="返回用户列表">‹</button><h1>${h(user.username || user.name || '用户')}</h1></div>
    <div class="readonly-banner">管理员只读查看，无法代替用户发送消息。</div>
    <div class="settings-group"><h2>账号信息</h2><div class="check-row"><span><b>登录账号</b></span><span>${h(user.username || '')}</span></div><div class="check-row"><span><b>用户 ID</b></span><span>${h(user.id)}</span></div><button class="soft-button wide" data-action="reset-password" style="margin-top:14px">设置新密码</button></div>
    <div class="section-title"><h2>用户的故事</h2><small>${conversations.length} 个</small></div>
    ${conversations.length ? `<div class="stack">${conversations.map(c => `<button class="conversation-row" data-action="open-conversation" data-id="${h(c.id)}"><span class="item-body"><strong>${h(c.title)}</strong><p>${h((c.participants || []).map(p => p.name || p).join(' · '))}</p><small>${dateLabel(c.updated_at)}</small></span><span class="row-chevron">›</span></button>`).join('')}</div>` : `<div class="empty"><h2>暂无故事</h2><p>这个用户还没有对话。</p></div>`}`);
}

async function openConversation(id) {
  const data = await api(`/api/admin/users/${apiPath(state.selectedUser.id)}/conversations/${apiPath(id)}/`);
  state.conversation = data.conversation || data;
  renderConversation(); changeView('conversation');
}

function renderConversation() {
  const conversation = state.conversation;
  const byId = Object.fromEntries((conversation.participants || []).map(p => [p.id, p]));
  const environment = Object.entries(conversation.environment || {}).filter(([, value]) => value !== '' && value != null);
  html('#admin-conversation', `<div class="back-line"><button class="icon-button" data-nav="user" aria-label="返回用户">‹</button><h1>${h(conversation.title || '对话')}</h1></div><div class="readonly-banner">只读模式</div>
    ${environment.length ? `<div class="scene-band"><strong>场景</strong><dl>${environment.map(([key, value]) => `<dt>${h(key)}</dt><dd>${h(typeof value === 'string' ? value : JSON.stringify(value))}</dd>`).join('')}</dl></div>` : ''}
    <div class="chat-log">${(conversation.messages || []).length ? conversation.messages.map(message => {
      const speaker = byId[message.speaker_id];
      return `<article class="message ${message.speaker_id === conversation.player_character_id ? 'mine' : ''} ${h(message.kind || '')}">${speaker ? avatar(speaker, 'small') : ''}<div class="message-main"><div class="message-meta"><strong>${h(speaker?.name || (message.kind === 'narration' ? '旁白' : '系统'))}</strong><span>${dateLabel(message.created_at)}</span></div><div class="message-bubble">${h(message.content)}</div></div></article>`;
    }).join('') : `<div class="empty"><h2>暂无消息</h2></div>`}</div>`);
}

async function loadDefaults() {
  state.defaults = await api('/api/admin/defaults/');
  renderDefaults();
}

function renderDefaults() {
  if (!state.defaults) { html('#admin-defaults', `<div class="inline-note">正在读取默认设置…</div>`); return; }
  html('#admin-defaults', `<div class="page-heading"><div><span class="eyebrow">GLOBAL DEFAULTS</span><h1>全局默认设置</h1><p>新用户继承这些值，个人设置可单独覆盖</p></div></div>
    <form id="defaults-form" data-form="defaults">${settingsMarkup(state.defaults.defaults || {}, state.defaults.has_api_key)}<button class="primary-button wide" type="submit">保存全局默认设置</button></form>`);
}

document.addEventListener('click', event => {
  if (event.target.dataset.backdrop && event.target === $('.sheet-backdrop')) { closeModal(); return; }
  const nav = event.target.closest('[data-nav]');
  if (nav) {
    const view = nav.dataset.nav;
    if (view === 'users') { changeView('users'); loadUsers().catch(error => toast(error.message)); }
    else if (view === 'defaults') { changeView('defaults'); loadDefaults().catch(error => { html('#admin-defaults', `<div class="inline-note error">${h(error.message)}</div>`); }); }
    else if (view === 'user') { renderUser(); changeView('user'); }
    return;
  }
  const button = event.target.closest('[data-action]');
  if (!button) return;
  const action = button.dataset.action;
  if (action === 'open-user') openUser(button.dataset.id).catch(error => toast(error.message));
  else if (action === 'open-conversation') openConversation(button.dataset.id).catch(error => toast(error.message));
  else if (action === 'close-modal') closeModal();
  else if (action === 'reset-password') openModal('重置密码', `<p>将为 ${h(state.selectedUser.username || '用户')} 生成一次性临时密码。旧密码会立即失效。</p><form id="reset-password-form" data-form="reset-password"><button class="primary-button wide" type="submit">生成临时密码</button></form>`);
  else if (action === 'copy-password') {
    const input = $('#temporary-password');
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(input.value).then(() => toast('已复制密码')).catch(() => { input.select(); toast('请手动复制密码'); });
    else { input.select(); toast('请手动复制密码'); }
  }
});

document.addEventListener('input', event => {
  if (event.target.id !== 'user-search') return;
  const query = event.target.value.trim().toLocaleLowerCase();
  document.querySelectorAll('#admin-users .user-row').forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase().includes(query); });
});

document.addEventListener('keydown', event => { if (event.key === 'Escape') closeModal(); });

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-form]');
  if (!form) return;
  event.preventDefault();
  if (!form.reportValidity()) return;
  const submit = form.querySelector('[type="submit"]');
  if (submit?.disabled) return;
  if (submit) submit.disabled = true;
  try {
    if (form.dataset.form === 'defaults') {
      const changes = settingsChanges(form, state.defaults.defaults || {});
      if (!Object.keys(changes).length) toast('设置没有变化');
      else { state.defaults = await api('/api/admin/defaults/', { method: 'PUT', body: changes }); renderDefaults(); toast('全局默认设置已保存'); }
    } else if (form.dataset.form === 'reset-password') {
      const result = await api(`/api/admin/users/${apiPath(state.selectedUser.id)}/reset-password/`, { method: 'POST', body: {} });
      openModal('临时密码', `<p>请现在交给用户。关闭后不会再次显示。</p><label class="field"><span>临时密码</span><input id="temporary-password" type="text" readonly value="${h(result.temporary_password || '')}" autocomplete="off"></label><button type="button" class="soft-button wide" data-action="copy-password">复制密码</button>`);
    }
  } catch (error) { toast(error.message); }
  finally { if (submit?.isConnected) submit.disabled = false; }
});

async function boot() {
  try {
    state.me = await api('/api/auth/me/', { redirectOnUnauthorized: false });
    if (!state.me.authenticated) { window.location.href = '/login/'; return; }
    if (!state.me.is_admin) { html('#admin-users', `<div class="inline-note error">此账号没有管理权限。</div><a class="soft-button" href="/app/" style="margin-top:12px">返回酒馆</a>`); changeView('users'); return; }
    await loadUsers(); changeView('users');
  } catch (error) {
    html('#admin-users', `<div class="inline-note error">${h(error.message)}</div>`); changeView('users');
  }
}

boot();
