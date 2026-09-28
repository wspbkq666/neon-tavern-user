import { api, apiPath, avatar, dateLabel, escapeHtml as h, listFrom, settingsMarkup, settingsChanges, toast } from './common.js';

const state = {
  me: null, characters: [], conversations: [], current: null, view: 'home',
  editingCharacter: null, editingConversation: null, messageKind: 'dialogue',
  speakerId: null, characterSearch: '', conversationSearch: '', jobId: null, pollTimer: null, lastJob: null,
  settings: null, profile: null,
};
const $ = selector => document.querySelector(selector);
const html = (selector, value) => { $(selector).innerHTML = value; };
const npc = conversation => (conversation?.participants || []).filter(character => !character.is_player_controlled);
const player = conversation => (conversation?.participants || []).find(character => character.id === conversation.player_character_id);
const characterById = id => state.characters.find(character => character.id === id);

async function loadCharacters() {
  state.characters = listFrom(await api('/api/characters/'), 'characters');
  if (state.view === 'characters') renderCharacters();
}
async function loadConversations() {
  state.conversations = listFrom(await api('/api/conversations/'), 'conversations');
  if (state.view === 'home') renderHome();
}
async function loadConversation(id) {
  state.current = await api(`/api/conversations/${apiPath(id)}/`);
  if (!state.speakerId || !state.current.participants?.some(c => c.id === state.speakerId)) state.speakerId = state.current.player_character_id;
  renderChat();
}

function changeView(view) {
  state.view = view;
  document.querySelectorAll('.view').forEach(element => {
    element.classList.toggle('active', element.id === `${view}-view`);
    element.classList.toggle('chat-active', element.id === 'chat-view' && view === 'chat');
  });
  document.querySelectorAll('[data-nav]').forEach(button => button.classList.toggle('active', button.dataset.nav === view || (view === 'chat' || view === 'create' ? button.dataset.nav === 'home' : view === 'character-form' && button.dataset.nav === 'characters')));
  const titles = { home: '我的故事', chat: state.current?.title || '对话', create: state.editingConversation ? '对话设置' : '新建故事', characters: '角色卡', 'character-form': state.editingCharacter ? '编辑角色' : '创建角色', settings: '设置' };
  $('#page-title').textContent = titles[view];
  html('#header-actions', view === 'home' ? `<button class="icon-button" data-action="new-conversation" title="新建故事" aria-label="新建故事">＋</button>` : view === 'chat' ? `<button class="icon-button" data-action="state-drawer" title="角色状态" aria-label="角色状态">◫</button><button class="icon-button" data-action="edit-conversation" title="对话设置" aria-label="对话设置">⚙</button>` : view === 'characters' ? `<button class="icon-button" data-action="new-character" title="创建角色" aria-label="创建角色">＋</button>` : '');
  window.scrollTo({ top: 0, behavior: 'instant' });
}

function empty(icon, title, description, action = '') {
  return `<div class="empty"><div class="empty-icon" aria-hidden="true">${icon}</div><h2>${title}</h2><p>${description}</p>${action}</div>`;
}

function renderHome() {
  const query = state.conversationSearch.toLocaleLowerCase();
  const filtered = state.conversations.filter(conversation => `${conversation.title} ${(conversation.participants || []).map(c => c.name).join(' ')}`.toLocaleLowerCase().includes(query));
  html('#home-view', `<div class="page-heading"><div><span class="eyebrow">YOUR STORIES</span><h1>我的故事</h1><p>欢迎回来，${h(state.me?.username || '')}</p></div><span class="count-badge">${state.conversations.length}</span></div>
    <button class="primary-button wide" data-action="new-conversation">＋ 开始新故事</button>
    <div class="section-title"><h2>最近的对话</h2><small>${state.conversations.length} 个故事</small></div>
    ${state.conversations.length ? `<label class="search-field"><input data-search="conversations" type="search" placeholder="搜索故事或角色" value="${h(state.conversationSearch)}" aria-label="搜索故事"></label>` : ''}
    <div class="grid cards">${filtered.map(conversation => `<button class="conversation-row" data-action="open-conversation" data-id="${h(conversation.id)}">${avatar((conversation.participants || []).find(c => !c.is_player_controlled) || { name: conversation.title })}<span class="item-body"><strong>${h(conversation.title)}</strong><p>${h((conversation.participants || []).map(c => c.name).join(' · '))}</p><small>${dateLabel(conversation.updated_at)}</small></span><span class="row-chevron" aria-hidden="true">›</span></button>`).join('')}</div>
    ${!state.conversations.length ? empty('✦', '故事从这里开始', '创建玩家角色和 NPC 后，就能开启第一段对话。', `<button class="soft-button" data-nav="characters">先创建角色</button>`) : !filtered.length ? empty('⌕', '没有找到故事', '试试其他标题或角色名。') : ''}`);
}

function renderCharacters() {
  const query = state.characterSearch.toLocaleLowerCase();
  const filtered = state.characters.filter(character => `${character.name} ${character.summary || ''}`.toLocaleLowerCase().includes(query));
  html('#characters-view', `<div class="page-heading"><div><span class="eyebrow">CHARACTERS</span><h1>角色卡</h1><p>为故事准备玩家与人物</p></div><span class="count-badge">${state.characters.length}</span></div>
    <button class="primary-button wide" data-action="new-character">＋ 创建角色</button>
    ${state.characters.length ? `<div class="section-title"><h2>全部角色</h2><small>${state.characters.length} 张角色卡</small></div><label class="search-field"><input data-search="characters" type="search" placeholder="搜索角色" value="${h(state.characterSearch)}" aria-label="搜索角色"></label>` : ''}
    <div class="grid cards">${filtered.map(character => `<button class="character-row" data-action="edit-character" data-id="${h(character.id)}">${avatar(character)}<span class="item-body"><strong>${h(character.name)}</strong><p>${h(character.summary || '暂无简介')}</p><span class="chips"><span class="chip ${character.is_player_controlled ? 'warm' : ''}">${character.is_player_controlled ? '玩家角色' : 'NPC'}</span></span></span><span class="row-chevron" aria-hidden="true">›</span></button>`).join('')}</div>
    ${!state.characters.length ? empty('♙', '还没有角色卡', '先创建自己的玩家角色，再添加至少一名 NPC。') : !filtered.length ? empty('⌕', '没有找到角色', '换一个关键词试试。') : ''}`);
}

function pairRows(values, kind) {
  const entries = Object.entries(values && typeof values === 'object' && !Array.isArray(values) ? values : {});
  return (entries.length ? entries : [['', '']]).map(([key, value]) => `<div class="field-row pair-row"><label class="field"><span>名称</span><input class="pair-key" maxlength="50" value="${h(key)}" placeholder="例如：心情"></label><label class="field"><span>内容</span><input class="pair-value" maxlength="2000" value="${h(typeof value === 'string' ? value : JSON.stringify(value))}" placeholder="填写内容"></label><button type="button" class="icon-button danger" data-action="remove-pair" title="移除此项" aria-label="移除此项">×</button></div>`).join('');
}

function renderCharacterForm() {
  const character = state.editingCharacter;
  html('#character-form-view', `<div class="back-line"><button class="icon-button" data-nav="characters" aria-label="返回角色列表">‹</button><h1>${character ? '编辑角色' : '创建角色'}</h1></div>
    <form id="character-form" data-form="character" class="surface">
      <div class="form-section"><h2>基本信息</h2><label class="field"><span>角色名字</span><input name="name" required maxlength="60" value="${h(character?.name || '')}" placeholder="为角色起一个名字"></label><label class="field"><span>一句话简介</span><input name="summary" maxlength="200" value="${h(character?.summary || '')}" placeholder="例如：总在雨夜营业的酒馆老板"></label><label class="field"><span>性格与背景</span><textarea name="personality" rows="5" maxlength="10000" placeholder="写下角色的性格与经历">${h(character?.personality || '')}</textarea></label><label class="field"><span>说话习惯</span><textarea name="speech_habits" rows="3" maxlength="5000">${h(character?.speech_habits || '')}</textarea></label><label class="field"><span>重要记忆</span><textarea name="memories" rows="3" maxlength="10000">${h(character?.memories || '')}</textarea></label><label class="check-row"><span><b>由我扮演</b><small>玩家角色可以在对话中亲自发言</small></span><input name="is_player_controlled" type="checkbox" ${character?.is_player_controlled ? 'checked' : ''}></label></div>
      <div class="form-section"><h2>自定义状态</h2><div class="stack pair-list" data-pairs="state">${pairRows(character?.state_fields, 'state')}</div><button type="button" class="soft-button" data-action="add-pair" data-kind="state">＋ 添加状态</button></div>
      <div class="form-section"><h2>关系备注</h2><div class="stack pair-list" data-pairs="relationship">${pairRows(character?.relationship_notes, 'relationship')}</div><button type="button" class="soft-button" data-action="add-pair" data-kind="relationship">＋ 添加关系</button></div>
      <div class="form-section"><h2>头像</h2><label class="field"><span>上传图片</span><input name="avatar" type="file" accept="image/*"></label>${character?.avatar_url ? `<div class="button-row">${avatar(character)}<span class="field-help">当前头像</span></div>` : ''}</div>
      <div class="form-actions"><button class="primary-button" type="submit">${character ? '保存角色' : '创建角色'}</button>${character ? `<button type="button" class="danger-button" data-action="delete-character" data-id="${h(character.id)}">删除</button>` : ''}</div>
    </form>`);
}

function roleChoice(character, name, checked, disabled = false) {
  return `<label class="role-choice" data-role-name="${h(character.name.toLocaleLowerCase())}">${avatar(character, 'small')}<span class="item-body"><strong>${h(character.name)}</strong><small>${h(character.summary || (character.is_player_controlled ? '玩家角色' : 'NPC'))}</small></span><input type="${name === 'player' ? 'radio' : 'checkbox'}" name="${name}" value="${h(character.id)}" ${checked ? 'checked' : ''} ${disabled ? 'disabled' : ''}></label>`;
}

function renderCreate() {
  const conversation = state.editingConversation;
  const players = state.characters.filter(character => character.is_player_controlled);
  const npcs = state.characters.filter(character => !character.is_player_controlled);
  const selectedNpcs = new Set((conversation?.participants || []).filter(c => !c.is_player_controlled).map(c => c.id));
  const environment = conversation?.environment || state.settings?.effective?.scene_defaults || {};
  html('#create-view', `<div class="back-line"><button class="icon-button" data-nav="${conversation ? 'chat' : 'home'}" aria-label="返回">‹</button><h1>${conversation ? '对话设置' : '新建故事'}</h1></div>
    ${!players.length || !npcs.length ? `<div class="inline-note warm">需要至少一名玩家角色和一名 NPC 才能创建故事。</div><div style="margin-top:12px"><button class="soft-button" data-nav="characters">管理角色卡</button></div>` : `<form id="conversation-form" data-form="conversation" class="surface">
      <div class="form-section"><h2>故事与场景</h2><label class="field"><span>故事标题</span><input name="title" required maxlength="120" value="${h(conversation?.title || '')}" placeholder="例如：雨夜的酒馆"></label><div class="field-row"><label class="field"><span>地点</span><input name="place" maxlength="2000" value="${h(environment['地点'] || '')}" placeholder="故事发生在哪里"></label><label class="field"><span>时间</span><input name="time" maxlength="2000" value="${h(environment['时间'] || '')}" placeholder="时间或时段"></label></div><label class="field"><span>环境描写</span><textarea name="description" maxlength="2000" rows="3" placeholder="天气、氛围或正在发生的事">${h(environment['环境描写'] || '')}</textarea></label><label class="field"><span>语言</span><input name="language" maxlength="32" value="${h(conversation?.language || '中文')}"></label></div>
      <div class="form-section"><h2>玩家角色</h2>${conversation ? `<div class="role-list">${players.filter(c => c.id === conversation.player_character_id).map(c => roleChoice(c, 'player', true, true)).join('')}</div>` : `<div class="role-list">${players.map((c, i) => roleChoice(c, 'player', i === 0)).join('')}</div>`}</div>
      <div class="form-section"><h2>参与的 NPC</h2><label class="search-field"><input data-search="roles" type="search" placeholder="搜索角色" aria-label="搜索 NPC"></label><div class="role-list">${npcs.map(c => roleChoice(c, 'npcs', selectedNpcs.has(c.id))).join('')}</div></div>
      <div class="form-section"><h2>自动回复</h2><label class="check-row"><span><b>发送后自动生成</b><small>由所选 NPC 接续故事</small></span><input name="auto_generate" type="checkbox" ${conversation?.auto_generate ? 'checked' : ''}></label><label class="field" style="margin-top:12px"><span>生成方式</span><select name="auto_mode"><option value="parallel" ${conversation?.auto_mode !== 'serial' ? 'selected' : ''}>并行</option><option value="serial" ${conversation?.auto_mode === 'serial' ? 'selected' : ''}>依次</option></select></label><p class="field-help">留空角色范围时，所有参与的 NPC 都可回复。</p><div class="role-list">${npcs.map(c => `<label class="role-choice" data-auto-id="${h(c.id)}">${avatar(c, 'small')}<span class="item-body"><strong>${h(c.name)}</strong></span><input type="checkbox" name="auto_actor_ids" value="${h(c.id)}" ${conversation?.auto_actor_ids?.includes(c.id) ? 'checked' : ''} ${selectedNpcs.has(c.id) ? '' : 'disabled'}></label>`).join('')}</div></div>
      <div class="form-actions"><button class="primary-button" type="submit">${conversation ? '保存设置' : '创建故事'}</button>${conversation ? `<button type="button" class="danger-button" data-action="delete-conversation">删除故事</button>` : ''}</div>
    </form>`}`);
}

function sceneMarkup(conversation) {
  const entries = Object.entries(conversation.environment || {}).filter(([, value]) => value !== '' && value != null);
  if (!entries.length) return '';
  return `<div class="scene-band"><strong>当前场景</strong><dl>${entries.map(([key, value]) => `<dt>${h(key)}</dt><dd>${h(typeof value === 'string' ? value : JSON.stringify(value))}</dd>`).join('')}</dl></div>`;
}

function renderChat() {
  const conversation = state.current;
  if (!conversation) return;
  const speakers = conversation.participants || [];
  const byId = Object.fromEntries(speakers.map(c => [c.id, c]));
  const messages = conversation.messages || [];
  html('#chat-view', `<div class="back-line"><button class="icon-button" data-nav="home" aria-label="返回故事列表">‹</button><h1>${h(conversation.title)}</h1><button class="icon-button" data-action="refresh-chat" title="刷新对话" aria-label="刷新对话">↻</button></div>
    ${sceneMarkup(conversation)}
    ${state.jobId ? `<div class="job-status" id="job-status"><span class="spinner"></span><span id="job-label">正在生成回复…</span><pre id="job-partial" hidden></pre></div>` : state.lastJob?.conversation_id === conversation.id && Object.values(state.lastJob.progress || {}).some(item => item.raw_response) ? `<div class="job-status"><span>本轮回复已保存</span><button class="soft-button" data-action="view-raw-response">查看原始回复</button></div>` : ''}
    <div class="chat-log" id="chat-log" aria-live="polite">${messages.length ? messages.map(message => {
      const who = byId[message.speaker_id];
      const mine = message.speaker_id === conversation.player_character_id;
      return `<article class="message ${mine ? 'mine' : ''} ${h(message.kind || '')}">${who ? avatar(who, 'small') : ''}<div class="message-main"><div class="message-meta"><strong>${h(who?.name || (message.kind === 'narration' ? '旁白' : '系统'))}</strong><span>${h(({ dialogue: '台词', action: '动作', ooc: '导演', narration: '旁白', state: '状态' })[message.kind] || '')}</span><span>${dateLabel(message.created_at)}</span></div><div class="message-bubble">${h(message.content)}</div></div></article>`;
    }).join('') : empty('✦', '故事等待第一句话', state.settings?.effective?.welcome_message || '选择角色，写下对话、动作或导演提示。')}</div>
    <form id="message-form" data-form="message" class="composer"><div class="composer-top"><input type="hidden" name="speaker_id" value="${h(conversation.player_character_id)}"><span class="speaker-chip">${avatar(player(conversation), 'small')}<strong>${h(player(conversation)?.name || '玩家')}</strong></span><div class="segmented compact" role="group" aria-label="消息类型">${[['dialogue', '台词'], ['action', '动作'], ['ooc', '导演']].map(([kind, label]) => `<button type="button" data-action="message-kind" data-kind="${kind}" class="${state.messageKind === kind ? 'active' : ''}">${label}</button>`).join('')}</div></div><div class="composer-row"><textarea name="content" rows="1" maxlength="10000" required placeholder="写下接下来发生的事…" aria-label="消息内容"></textarea><button class="primary-button" type="submit" title="发送消息">发送</button><button class="soft-button" type="button" data-action="open-generation" title="生成角色回复">✦</button></div></form>`);
  $('#page-title').textContent = conversation.title;
  requestAnimationFrame(() => { const log = $('#chat-log'); if (log) log.scrollIntoView({ block: 'end', behavior: 'instant' }); });
}

function renderSettings() {
  if (!state.settings) { html('#settings-view', `<div class="page-heading"><div><span class="eyebrow">PREFERENCES</span><h1>设置</h1></div></div><div class="inline-note">正在读取设置…</div>`); return; }
  const values = state.settings.effective || {};
  html('#settings-view', `<div class="page-heading"><div><span class="eyebrow">PREFERENCES</span><h1>设置</h1><p>${h(state.me?.username || '')}</p></div></div>
    <form id="settings-form" data-form="settings">${settingsMarkup(values, state.settings.has_api_key)}<div class="setting-actions"><button class="primary-button" type="submit">保存设置</button><button class="outline-button" type="button" data-action="test-connection">测试连接</button><button class="outline-button" type="button" data-action="reset-settings">恢复默认</button></div></form>
    <div class="settings-group"><h2>对话自动生成</h2><p class="field-help">每个故事单独设置。开启后，发送消息会自动生成角色回复。</p>${state.conversations.length ? `<div class="stack">${state.conversations.map(c => `<button class="conversation-row" data-action="edit-conversation-from-settings" data-id="${h(c.id)}"><span class="item-body"><strong>${h(c.title)}</strong><small>${c.auto_generate ? '已开启' : '已关闭'}</small></span><span class="row-chevron">›</span></button>`).join('')}</div>` : `<p class="field-help">还没有故事</p>`}</div>
    <div class="settings-group"><h2>我的画像</h2><form data-form="profile"><label class="field"><span>故事中的偏好与称呼</span><textarea name="inferred_traits" rows="4" maxlength="10000">${h(state.profile?.inferred_traits || '')}</textarea></label><button class="soft-button" type="submit">保存画像</button></form></div>
    <div class="settings-group"><h2>账号安全</h2><form data-form="password"><label class="field"><span>当前密码</span><input name="old_password" type="password" autocomplete="current-password" required></label><label class="field"><span>新密码</span><input name="new_password" type="password" autocomplete="new-password" minlength="10" required></label><label class="field"><span>确认新密码</span><input name="confirm_password" type="password" autocomplete="new-password" required></label><button class="soft-button" type="submit">修改密码</button></form></div>
    <div class="settings-group"><button class="outline-button wide" data-action="logout">退出登录</button></div>`);
}

function openModal(title, content, footer = '') {
  html('#modal-root', `<div class="sheet-backdrop" data-backdrop="true"><section class="sheet" role="dialog" aria-modal="true" aria-label="${h(title)}"><div class="sheet-head"><h2>${h(title)}</h2><button type="button" data-action="close-modal" aria-label="关闭">×</button></div>${content}${footer ? `<footer>${footer}</footer>` : ''}</section></div>`);
  $('.sheet button, .sheet input')?.focus();
}
function closeModal() { html('#modal-root', ''); }

function openStateDrawer() {
  const participants = state.current?.participants || [];
  openModal('角色状态', participants.length ? `<div class="state-grid">${participants.map(c => {
    const fields = characterById(c.id)?.state_fields || c.state_fields || {};
    return `<div class="state-person"><h3>${h(c.name)}</h3>${Object.keys(fields).length ? `<dl>${Object.entries(fields).map(([key, value]) => `<dt>${h(key)}</dt><dd>${h(typeof value === 'string' ? value : JSON.stringify(value))}</dd>`).join('')}</dl>` : `<p class="field-help">暂无状态</p>`}</div>`;
  }).join('')}</div>` : `<p>暂无参与角色</p>`);
}

function openGeneration() {
  const actors = npc(state.current);
  if (!actors.length) return toast('当前故事没有可生成的 NPC');
  openModal('生成角色回复', `<p>选择本轮发言的角色，也可以先获取建议。</p><div class="role-list" id="generation-roles">${actors.map(c => `<div>${roleChoice(c, 'actor_ids', false)}<label class="field" style="margin:7px 0 10px"><span>给 ${h(c.name)} 的提示</span><input data-hint-for="${h(c.id)}" maxlength="500" placeholder="可选"></label></div>`).join('')}</div><label class="field" style="margin-top:12px"><span>生成方式</span><select id="generation-mode"><option value="parallel">并行</option><option value="serial">依次</option></select></label><div id="suggest-note" class="field-help"></div>`, `<button class="soft-button" type="button" data-action="suggest-generation">获取建议</button><button class="primary-button" type="button" data-action="confirm-generation">开始生成</button>`);
}

function objectFromPairs(form, kind) {
  const result = {};
  form.querySelectorAll(`[data-pairs="${kind}"] .pair-row`).forEach(row => {
    const key = row.querySelector('.pair-key').value.trim();
    const value = row.querySelector('.pair-value').value.trim();
    if (!key && !value) return;
    if (!key) throw new Error('请为每一项填写名称');
    if (Object.hasOwn(result, key)) throw new Error(`重复的名称：${key}`);
    result[key] = value;
  });
  return result;
}

async function saveCharacter(form) {
  if (!form.reportValidity()) return;
  const file = form.elements.avatar?.files?.[0];
  if (file && file.size > 2 * 1024 * 1024) throw new Error('头像需小于 2 MB');
  const data = {
    name: form.elements.name.value.trim(), summary: form.elements.summary.value.trim(),
    personality: form.elements.personality.value.trim(),
    speech_habits: form.elements.speech_habits.value.trim(), memories: form.elements.memories.value.trim(),
    is_player_controlled: form.elements.is_player_controlled.checked,
    state_fields: objectFromPairs(form, 'state'), relationship_notes: objectFromPairs(form, 'relationship'),
  };
  if (!data.name) throw new Error('请填写角色名字');
  const existing = state.editingCharacter;
  const character = await api(existing ? `/api/characters/${apiPath(existing.id)}/` : '/api/characters/', { method: existing ? 'PATCH' : 'POST', body: data });
  state.editingCharacter = character;
  let avatarError = null;
  if (file && (character.id || existing?.id)) {
    const upload = new FormData(); upload.append('avatar', file);
    try { await api(`/api/characters/${apiPath(character.id || existing.id)}/avatar/`, { method: 'POST', body: upload }); }
    catch (error) { avatarError = error.message; }
  }
  await loadCharacters();
  toast(avatarError ? `角色已保存，头像上传失败：${avatarError}` : existing ? '角色已保存' : '角色已创建');
  changeView('characters'); renderCharacters();
}

async function saveConversation(form) {
  if (!form.reportValidity()) return;
  const existing = state.editingConversation;
  const selectedNpcs = [...form.querySelectorAll('input[name="npcs"]:checked')].map(x => x.value);
  const playerId = existing?.player_character_id || form.querySelector('input[name="player"]:checked')?.value;
  if (!playerId || !selectedNpcs.length) throw new Error('请选择玩家角色和至少一名 NPC');
  const selectedAuto = [...form.querySelectorAll('input[name="auto_actor_ids"]:checked')].map(x => x.value);
  if (selectedAuto.some(id => !selectedNpcs.includes(id))) throw new Error('自动回复角色必须参与故事');
  const environment = { ...(existing?.environment || {}) };
  for (const [key, input] of [['地点', 'place'], ['时间', 'time'], ['环境描写', 'description']]) {
    const value = form.elements[input].value.trim();
    if (value) environment[key] = value; else delete environment[key];
  }
  const data = {
    title: form.elements.title.value.trim(), environment,
    language: form.elements.language.value.trim() || '中文',
    auto_generate: form.elements.auto_generate.checked, auto_actor_ids: selectedAuto,
    auto_mode: form.elements.auto_mode.value,
  };
  if (!data.title) throw new Error('请填写故事标题');
  if (!existing) data.player_character_id = playerId;
  data.character_ids = selectedNpcs;
  const conversation = await api(existing ? `/api/conversations/${apiPath(existing.id)}/` : '/api/conversations/', { method: existing ? 'PATCH' : 'POST', body: data });
  await loadConversations();
  toast(existing ? '对话设置已保存' : '故事已创建');
  await loadConversation(conversation.id || existing.id);
  changeView('chat');
}

async function sendMessage(form) {
  const content = form.elements.content.value.trim();
  const speakerId = form.elements.speaker_id.value;
  if (!content) return;
  if (!speakerId) throw new Error('请选择发言角色');
  const result = await api(`/api/conversations/${apiPath(state.current.id)}/messages/`, { method: 'POST', body: { speaker_id: speakerId, kind: state.messageKind, content } });
  state.speakerId = speakerId;
  await loadConversation(state.current.id);
  loadConversations().catch(error => toast(error.message));
  if (result.warning) toast(result.warning);
  if (result.job_id) startPolling(result.job_id);
}

async function refreshCurrent() {
  if (state.current) await loadConversation(state.current.id);
}

async function startPolling(jobId) {
  clearTimeout(state.pollTimer);
  state.jobId = jobId;
  state.lastJob = null;
  if (state.view === 'chat') renderChat();
  let attempts = 0;
  const poll = async () => {
    try {
      const job = await api(`/api/generation-jobs/${apiPath(jobId)}/`);
      if (job.status === 'done' || job.status === 'failed') {
        state.jobId = null;
        state.lastJob = job;
        await Promise.all([refreshCurrent(), loadCharacters(), loadConversations()]);
        if (job.status === 'failed') toast(job.error || '生成失败，请重试');
        else toast('回复已生成');
        return;
      }
      const status = $('#job-label');
      if (status) status.textContent = job.status === 'running' ? '角色正在回复…' : '生成任务排队中…';
      const partial = $('#job-partial');
      if (partial) {
        const text = Object.entries(job.progress || {}).filter(([, value]) => value.partial).map(([id, value]) => `${state.current?.participants?.find(actor => actor.id === id)?.name || '角色'}：${value.partial}`).join('\n\n');
        partial.textContent = text;
        partial.hidden = !text;
      }
      attempts += 1;
      if (attempts >= 120) { state.jobId = null; renderChat(); toast('等待时间较长，请稍后刷新对话'); return; }
      state.pollTimer = setTimeout(poll, 2000);
    } catch (error) {
      state.jobId = null;
      toast(error.message);
    }
  };
  poll();
}

async function suggestGeneration() {
  const button = $('[data-action="suggest-generation"]');
  button.disabled = true;
  try {
    const suggestion = await api(`/api/conversations/${apiPath(state.current.id)}/suggest/`, { method: 'POST', body: {} });
    const ids = new Set(suggestion.actor_ids || []);
    document.querySelectorAll('#generation-roles input[name="actor_ids"]').forEach(input => { input.checked = ids.has(input.value); });
    document.querySelectorAll('#generation-roles [data-hint-for]').forEach(input => { input.value = suggestion.director_hints?.[input.dataset.hintFor] || ''; });
    $('#suggest-note').textContent = ids.size ? '已选中建议角色；确认后才会开始生成。' : '没有建议角色，请手动选择。';
  } finally { button.disabled = false; }
}

async function confirmGeneration() {
  const actorIds = [...document.querySelectorAll('#generation-roles input[name="actor_ids"]:checked')].map(input => input.value);
  if (!actorIds.length) throw new Error('请选择至少一名 NPC');
  const hints = {};
  for (const id of actorIds) {
    const value = [...document.querySelectorAll('#generation-roles [data-hint-for]')].find(input => input.dataset.hintFor === id)?.value.trim();
    if (value) hints[id] = value;
  }
  const result = await api(`/api/conversations/${apiPath(state.current.id)}/generate/`, { method: 'POST', body: { actor_ids: actorIds, mode: $('#generation-mode').value, director_hints: hints } });
  closeModal();
  const jobId = result.job_id || result.id;
  if (jobId) startPolling(jobId); else await refreshCurrent();
}

async function loadSettings() {
  [state.settings, state.profile] = await Promise.all([api('/api/settings/'), api('/api/account/profile/')]);
  renderSettings();
}
async function saveSettings(form) {
  if (!form.reportValidity()) return;
  const changes = settingsChanges(form, state.settings.effective || {});
  if (!Object.keys(changes).length) return toast('设置没有变化');
  state.settings = await api('/api/settings/', { method: 'PUT', body: changes });
  renderSettings(); toast('设置已保存');
}

async function handleAction(button) {
  const action = button.dataset.action;
  const id = button.dataset.id;
  if (action === 'new-conversation') { state.editingConversation = null; renderCreate(); changeView('create'); }
  else if (action === 'open-conversation') { await loadConversation(id); changeView('chat'); }
  else if (action === 'new-character') { state.editingCharacter = null; renderCharacterForm(); changeView('character-form'); }
  else if (action === 'edit-character') { state.editingCharacter = characterById(id) || await api(`/api/characters/${apiPath(id)}/`); renderCharacterForm(); changeView('character-form'); }
  else if (action === 'delete-character') {
    if (window.confirm('确定删除这张角色卡吗？')) { await api(`/api/characters/${apiPath(id)}/`, { method: 'DELETE' }); await loadCharacters(); toast('角色已删除'); changeView('characters'); renderCharacters(); }
  }
  else if (action === 'edit-conversation' || action === 'edit-conversation-from-settings') {
    if (id) await loadConversation(id);
    state.editingConversation = state.current; renderCreate(); changeView('create');
  }
  else if (action === 'delete-conversation') {
    if (window.confirm('确定删除这个故事及其所有消息吗？')) { await api(`/api/conversations/${apiPath(state.editingConversation.id)}/`, { method: 'DELETE' }); state.current = null; state.editingConversation = null; await loadConversations(); toast('故事已删除'); changeView('home'); renderHome(); }
  }
  else if (action === 'refresh-chat') await refreshCurrent();
  else if (action === 'state-drawer') { await loadCharacters(); openStateDrawer(); }
  else if (action === 'open-generation') openGeneration();
  else if (action === 'view-raw-response') {
    const raw = Object.entries(state.lastJob?.progress || {}).filter(([, value]) => value.raw_response).map(([id, value]) => `${state.current?.participants?.find(actor => actor.id === id)?.name || '角色'}\n${JSON.stringify(value.raw_response, null, 2)}`).join('\n\n');
    openModal('本轮原始回复', `<pre class="raw-response">${h(raw)}</pre>`);
  }
  else if (action === 'suggest-generation') await suggestGeneration();
  else if (action === 'confirm-generation') await confirmGeneration();
  else if (action === 'message-kind') { state.messageKind = button.dataset.kind; document.querySelectorAll('[data-action="message-kind"]').forEach(x => x.classList.toggle('active', x === button)); }
  else if (action === 'add-pair') { const list = $(`[data-pairs="${button.dataset.kind}"]`); list.insertAdjacentHTML('beforeend', pairRows({}, button.dataset.kind)); list.querySelector('.pair-row:last-child .pair-key')?.focus(); }
  else if (action === 'remove-pair') button.closest('.pair-row')?.remove();
  else if (action === 'close-modal') closeModal();
  else if (action === 'reset-settings') {
    if (!window.confirm('恢复账号设置为全站默认值？')) return;
    const reset = Object.fromEntries(Object.keys(state.settings.overrides || {}).map(key => [key, null]));
    reset.api_key = null;
    state.settings = await api('/api/settings/', { method: 'PUT', body: reset }); renderSettings(); toast('已恢复默认设置');
  }
  else if (action === 'test-connection') {
    button.disabled = true;
    try { await api('/api/settings/test-connection/', { method: 'POST', body: {} }); toast('模型连接正常'); }
    finally { button.disabled = false; }
  }
  else if (action === 'logout') { await api('/api/auth/logout/', { method: 'POST', body: {} }); window.location.href = '/login/'; }
  else if (action === 'retry-boot') await boot();
}

document.addEventListener('click', event => {
  if (event.target.dataset.backdrop && event.target === $('.sheet-backdrop')) { closeModal(); return; }
  const nav = event.target.closest('[data-nav]');
  if (nav) {
    const view = nav.dataset.nav;
    if (view === 'home') { changeView('home'); loadConversations().catch(error => toast(error.message)); }
    else if (view === 'characters') { changeView('characters'); renderCharacters(); }
    else if (view === 'settings') { changeView('settings'); loadSettings().catch(error => { html('#settings-view', `<div class="inline-note error">${h(error.message)}</div>`); }); }
    else if (view === 'chat' && state.current) { changeView('chat'); renderChat(); }
    return;
  }
  const action = event.target.closest('[data-action]');
  if (action) handleAction(action).catch(error => toast(error.message));
});

document.addEventListener('input', event => {
  const search = event.target.dataset.search;
  if (search === 'roles') {
    const query = event.target.value.trim().toLocaleLowerCase();
    document.querySelectorAll('#create-view [data-role-name]').forEach(row => { row.hidden = !row.dataset.roleName.includes(query); });
  } else if (search === 'characters' || search === 'conversations') {
    const view = search === 'characters' ? '#characters-view' : '#home-view';
    const query = event.target.value.trim().toLocaleLowerCase();
    document.querySelectorAll(`${view} .character-row, ${view} .conversation-row`).forEach(row => { row.hidden = !row.textContent.toLocaleLowerCase().includes(query); });
    state[search === 'characters' ? 'characterSearch' : 'conversationSearch'] = event.target.value;
  }
});

document.addEventListener('change', event => {
  if (!event.target.matches('#conversation-form input[name="npcs"]')) return;
  const auto = [...document.querySelectorAll('#conversation-form input[name="auto_actor_ids"]')].find(input => input.value === event.target.value);
  if (!auto) return;
  auto.disabled = !event.target.checked;
  if (auto.disabled) auto.checked = false;
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeModal();
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && event.target.closest('#message-form')) {
    event.preventDefault(); $('#message-form').requestSubmit();
  }
});

document.addEventListener('submit', async event => {
  const form = event.target.closest('[data-form]');
  if (!form) return;
  event.preventDefault();
  const submit = form.querySelector('[type="submit"]');
  if (submit?.disabled) return;
  if (submit) submit.disabled = true;
  try {
    if (form.dataset.form === 'character') await saveCharacter(form);
    if (form.dataset.form === 'conversation') await saveConversation(form);
    if (form.dataset.form === 'message') await sendMessage(form);
    if (form.dataset.form === 'settings') await saveSettings(form);
    if (form.dataset.form === 'profile') {
      state.profile = await api('/api/account/profile/', { method: 'PATCH', body: { inferred_traits: form.elements.inferred_traits.value.trim() } });
      renderSettings(); toast('画像已保存');
    }
    if (form.dataset.form === 'password') {
      const next = form.elements.new_password.value;
      if (next !== form.elements.confirm_password.value) throw new Error('两次输入的新密码不一致');
      await api('/api/account/change-password/', { method: 'POST', body: { old_password: form.elements.old_password.value, new_password: next } });
      form.reset(); toast('密码已更新');
    }
  } catch (error) { toast(error.message); }
  finally { if (submit?.isConnected) submit.disabled = false; }
});

async function boot() {
  try {
    state.me = await api('/api/auth/me/', { redirectOnUnauthorized: false });
    if (!state.me.authenticated) { window.location.href = '/login/'; return; }
    const results = await Promise.allSettled([loadCharacters(), loadConversations(), api('/api/settings/').then(settings => { state.settings = settings; })]);
    for (const result of results) if (result.status === 'rejected') toast(result.reason.message);
    renderHome(); changeView('home');
  } catch (error) {
    html('#home-view', `<div class="inline-note error">${h(error.message)}</div><button class="soft-button" data-action="retry-boot">重试</button>`);
    changeView('home');
  }
}

boot();
