export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]);
}

export function apiPath(segment) {
  return encodeURIComponent(String(segment));
}

function csrfToken() {
  const entry = document.cookie.split('; ').find(part => part.startsWith('neon_tavern_csrf='));
  return entry ? decodeURIComponent(entry.slice('neon_tavern_csrf='.length)) : '';
}

export async function api(path, options = {}) {
  const { method = 'GET', body, redirectOnUnauthorized = true } = options;
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken();
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch {
    throw new Error('无法连接服务器，请检查网络后重试');
  }
  const raw = await response.text();
  let data = {};
  if (raw) {
    try { data = JSON.parse(raw); } catch { throw new Error('服务器返回了无法读取的内容'); }
  }
  if (response.status === 401 && redirectOnUnauthorized) {
    window.location.href = '/login/';
    throw new Error('登录已过期');
  }
  if (!response.ok) {
    const detail = data.error || data.detail || data.message;
    throw new Error(typeof detail === 'string' ? detail : `请求失败（${response.status}）`);
  }
  return data;
}

export async function apiStream(path, options = {}) {
  const { method = 'POST', body, redirectOnUnauthorized = true } = options;
  const headers = { Accept: 'text/event-stream' };
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken();
  if (body !== undefined && !(body instanceof FormData)) headers['Content-Type'] = 'application/json';
  let response;
  try {
    response = await fetch(path, {
      method,
      headers,
      credentials: 'same-origin',
      cache: 'no-store',
      body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    });
  } catch {
    throw new Error('无法连接服务器，请检查网络后重试');
  }
  if (response.status === 401 && redirectOnUnauthorized) {
    window.location.href = '/login/';
    throw new Error('登录已过期');
  }
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.error || data.detail || data.message;
    throw new Error(typeof detail === 'string' ? detail : `请求失败（${response.status}）`);
  }
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('服务器未开启实时识别日志');
  }
  return response;
}

export function listFrom(data, key) {
  if (Array.isArray(data)) return data;
  return Array.isArray(data?.[key]) ? data[key] : Array.isArray(data?.results) ? data.results : [];
}

export function safeImageUrl(value) {
  if (!value || typeof value !== 'string') return '';
  try {
    const url = new URL(value, window.location.href);
    return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
  } catch { return ''; }
}

export function avatar(character, extraClass = '') {
  const name = character?.name || character?.username || '?';
  const src = safeImageUrl(character?.avatar_url);
  return `<span class="avatar ${character?.is_player_controlled ? 'player' : ''} ${extraClass}">${src ? `<img src="${escapeHtml(src)}" alt="">` : escapeHtml(name.slice(0, 1))}</span>`;
}

export function dateLabel(value) {
  if (!value) return '';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

let toastTimer;
export function toast(message) {
  const element = document.getElementById('toast');
  if (!element) return;
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove('show'), 3500);
}

export const settingsFields = [
  { key: 'model', label: '模型', type: 'select', options: [['deepseek-flash', 'DeepSeek Flash'], ['deepseek-v4-pro', 'DeepSeek V4 Pro']] },
  { key: 'language', label: '默认语言', type: 'text' },
  { key: 'temperature', label: '回复随机度', type: 'number', min: 0, max: 2, step: 0.1 },
  { key: 'top_p', label: '采样范围', type: 'number', min: 0, max: 1, step: 0.05 },
  { key: 'max_tokens', label: '回复长度上限', type: 'number', min: 128, max: 32768, step: 1 },
  { key: 'reply_format', label: '回复格式', type: 'select', options: [['structured', '结构化'], ['natural', '自然文本']] },
  { key: 'world_background', label: '世界背景', type: 'textarea' },
  { key: 'global_prompt', label: '全局角色提示', type: 'textarea' },
  { key: 'welcome_message', label: '欢迎语', type: 'textarea' },
  { key: 'scene_defaults', label: '默认场景（JSON）', type: 'json' },
  { key: 'strict_persona', label: '严格保持角色设定', type: 'checkbox' },
  { key: 'auto_state_extraction', label: '自动整理角色状态', type: 'checkbox' },
  { key: 'stream_output', label: '边生成边显示', type: 'checkbox' },
  { key: 'adult_content_preference', label: '允许成人内容', type: 'checkbox' },
  { key: 'save_raw_response', label: '保存原始回复', type: 'checkbox' },
  { key: 'memory_threshold', label: '记忆整理间隔', type: 'number', min: 5, max: 500, step: 1 },
  { key: 'profile_threshold', label: '人物档案整理间隔', type: 'number', min: 5, max: 500, step: 1 },
];

export function settingsMarkup(values = {}, hasApiKey = false) {
  const primary = settingsFields.filter(field => ['model', 'language', 'temperature', 'top_p', 'max_tokens', 'reply_format'].includes(field.key));
  const prompts = settingsFields.filter(field => ['world_background', 'global_prompt', 'welcome_message', 'scene_defaults'].includes(field.key));
  const switches = settingsFields.filter(field => field.type === 'checkbox');
  const advanced = settingsFields.filter(field => ['memory_threshold', 'profile_threshold'].includes(field.key));
  const render = fields => fields.map(field => fieldMarkup(field, values[field.key])).join('');
  return `<div class="settings-group"><h2>模型连接</h2><label class="field"><span>API 密钥</span><input name="api_key" type="password" autocomplete="off" placeholder="${hasApiKey ? '已设置，留空保持不变' : '输入 API 密钥'}"><small>密钥不会再次显示</small></label><div class="field-row">${render(primary)}</div></div>
    <div class="settings-group"><h2>故事与角色</h2>${render(prompts)}${switches.map(field => fieldMarkup(field, values[field.key])).join('')}</div>
    <details class="settings-group"><summary>详细设置</summary><div class="field-row" style="margin-top:14px">${render(advanced)}</div></details>`;
}

function fieldMarkup(field, value) {
  const key = escapeHtml(field.key);
  const label = escapeHtml(field.label);
  if (field.type === 'checkbox') return `<label class="check-row"><span><b>${label}</b></span><input type="checkbox" name="${key}" ${value === true ? 'checked' : ''}></label>`;
  if (field.type === 'textarea') return `<label class="field"><span>${label}</span><textarea name="${key}" rows="3">${escapeHtml(value ?? '')}</textarea></label>`;
  if (field.type === 'json') return `<label class="field"><span>${label}</span><textarea name="${key}" rows="3" spellcheck="false">${escapeHtml(JSON.stringify(value ?? {}, null, 2))}</textarea></label>`;
  if (field.type === 'select') return `<label class="field"><span>${label}</span><select name="${key}">${field.options.map(([option, text]) => `<option value="${escapeHtml(option)}" ${value === option ? 'selected' : ''}>${escapeHtml(text)}</option>`).join('')}</select></label>`;
  return `<label class="field"><span>${label}</span><input name="${key}" type="${field.type}" value="${escapeHtml(value ?? '')}" ${field.type === 'number' ? `min="${field.min}" max="${field.max}" step="${field.step}"` : ''}></label>`;
}

export function settingsChanges(form, initial = {}) {
  const changes = {};
  for (const field of settingsFields) {
    const input = form.elements.namedItem(field.key);
    if (!input) continue;
    let value = field.type === 'checkbox' ? input.checked : input.value.trim();
    if (field.type === 'number') {
      if (value === '') throw new Error(`${field.label}不能为空`);
      value = Number(value);
      if (!Number.isFinite(value) || value < field.min || value > field.max || (field.step === 1 && !Number.isInteger(value))) throw new Error(`${field.label}超出允许范围`);
    }
    if (field.type === 'json') {
      try { value = JSON.parse(value); } catch { throw new Error(`${field.label}需要有效的 JSON`); }
      if (!value || Array.isArray(value) || typeof value !== 'object') throw new Error(`${field.label}需要对象`);
    }
    if (JSON.stringify(value) !== JSON.stringify(initial[field.key])) changes[field.key] = value;
  }
  const key = form.elements.namedItem('api_key')?.value.trim();
  if (key) changes.api_key = key;
  return changes;
}
