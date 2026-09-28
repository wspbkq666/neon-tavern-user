import { api, escapeHtml as h, listFrom, dateLabel } from './common.js';
import { splitWorldBookKeywords } from './worldbook-keywords.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const state = { me: null, characters: [], characterCategories: [], conversations: [], current: null, replyActorId: null, settings: null, providers: [], editing: null, job: null, lastJob: null, polling: null, usagePolling: null, updatePolicy: null, readOnly: false, adminUser: null };
const fail = error => window.toast(error.message);
const path = id => encodeURIComponent(String(id));
function readAsText(file) {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('无法读取导入文件'));
    reader.readAsText(file, 'utf-8');
  });
}
async function readJsonFile(file) {
  try { return JSON.parse((await readAsText(file)).replace(/^\uFEFF/, '')); }
  catch { throw new Error('导入文件不是有效的 JSON'); }
}
const original = { showConversations: window.showConversations, openChat: window.openChat, openPanel: window.openPanel, openSheet: window.openSheet, openNewConversation: window.openNewConversation, closeNewConversation: window.closeNewConversation, openRoleCreator: window.openRoleCreator, closeSettings: window.closeSettings };
let worldBookEditingId = null;
const worldBookState = { books: [], current: null, entries: [], categories: [], manage: false, selected: new Set() };
const bundleState = { payload: null, preview: null, mode: 'file' };
const marketState = { scope: 'local', kind: '', query: '', listings: [], selected: null, bundleSelection: null, timer: null };
const smartImportState = { drafts: [], payload: null, preview: null, revision: 0, busy: false };

const smartImportNewId = () => window.crypto?.randomUUID?.() || `smart-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const smartImportTextValue = (value, maximum, label) => {
  const text = String(value || '');
  if (text.length > maximum) throw new Error(`${label}不能超过 ${maximum} 个字符`);
  return text;
};
function smartImportInvalidatePreview() {
  smartImportState.revision += 1;
  smartImportState.preview = null;
  $('#smartImportBundlePreview').hidden = true;
  $('#smartImportConfirm').hidden = true;
}
function smartImportRenderDrafts() {
  $('#smartImportDrafts').innerHTML = smartImportState.drafts.map((draft, index) => {
    const fields = draft.fields || {};
    const typeOptions = [['npc', 'NPC 角色卡'], ['player', '玩家卡'], ['worldbook', '世界书'], ['unknown', '待确认']];
    const common = `<label style="display:block;margin:8px 0;font-size:11px">名称或标题<input data-smart-import-field="name" data-index="${index}" value="${h(fields.name || '')}" style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--line);border-radius:9px"></label>`;
    const characterFields = ['summary', 'personality', 'speech_habits', 'memories', 'clothing_type', 'clothing_state'].map(key => `<label style="display:block;margin:8px 0;font-size:11px">${({ summary: '摘要', personality: '性格', speech_habits: '说话习惯', memories: '背景与记忆', clothing_type: '衣着类型', clothing_state: '衣着状态' })[key]}<textarea data-smart-import-field="${key}" data-index="${index}" rows="2" style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--line);border-radius:9px">${h(fields[key] || '')}</textarea></label>`).join('') + `<label style="display:block;margin:8px 0;font-size:11px">好感度（0–100）<input data-smart-import-field="affinity" data-index="${index}" type="number" min="0" max="100" step="1" value="${h(fields.affinity ?? 0)}" style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--line);border-radius:9px"></label>` + ['relationship_notes', 'state_fields', 'categories'].map(key => `<label style="display:block;margin:8px 0;font-size:11px">${({ relationship_notes: '关系备注（JSON）', state_fields: '状态字段（JSON）', categories: '角色分类（JSON 数组）' })[key]}<textarea data-smart-import-field="${key}" data-index="${index}" rows="3" style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--line);border-radius:9px;font:11px monospace">${h(JSON.stringify(fields[key] ?? (key === 'categories' ? [] : {}), null, 2))}</textarea></label>`).join('');
    const worldbookFields = `<label style="display:block;margin:8px 0;font-size:11px">世界书简介<textarea data-smart-import-field="description" data-index="${index}" rows="2" style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--line);border-radius:9px">${h(fields.description || '')}</textarea></label><label style="display:block;margin:8px 0;font-size:11px">条目（JSON，可编辑）<textarea data-smart-import-field="entries" data-index="${index}" rows="5" style="display:block;width:100%;margin-top:4px;padding:8px;border:1px solid var(--line);border-radius:9px;font:11px monospace">${h(JSON.stringify(fields.entries || [], null, 2))}</textarea></label>`;
    const unmapped = draft.unmapped_fields && Object.keys(draft.unmapped_fields).length ? `<details style="margin:8px 0"><summary style="font-size:11px;color:#a76b32">查看暂不能导入的字段</summary><pre style="white-space:pre-wrap;font-size:10px">${h(JSON.stringify(draft.unmapped_fields, null, 2))}</pre></details>` : '';
    return `<article class="card" data-smart-import-card="${index}"><div style="display:flex;align-items:center;gap:8px"><b style="flex:1">草稿 ${index + 1}</b><select data-smart-import-type data-index="${index}" aria-label="草稿类型">${typeOptions.map(([value, label]) => `<option value="${value}"${draft.type === value ? ' selected' : ''}>${label}</option>`).join('')}</select></div><div style="margin-top:6px;color:#8ca0aa;font-size:10px">识别置信度 ${Math.round((draft.confidence || 0) * 100)}%</div>${common}${draft.type === 'worldbook' ? worldbookFields : draft.type === 'unknown' ? `<div style="font-size:10px;color:#758d98">请先选择类别，再编辑对应字段。</div>` : characterFields}${unmapped}<details style="margin:8px 0"><summary style="font-size:11px;color:#648392">查看原文片段</summary><p style="white-space:pre-wrap;font-size:11px">${h(draft.source_excerpt || '')}</p></details>${(draft.warnings || []).length ? `<div class="bundle-warning">${draft.warnings.map(h).join('<br>')}</div>` : ''}<label style="display:block;margin-top:10px;font-size:11px"><input type="checkbox" data-smart-import-skip data-index="${index}"${draft.skipped ? ' checked' : ''}> 跳过此草稿</label></article>`;
  }).join('');
}
function smartImportBuildPayload() {
  const payload = { format: 'neon-tavern-bundle', version: 1, characters: [], worldbooks: [] };
  const characterIds = new Map();
  for (const draft of smartImportState.drafts) {
    if (draft.skipped) continue;
    if (Object.keys(draft.editErrors || {}).length) throw new Error('请先修正草稿中标出的 JSON 或数字格式问题');
    if (!['npc', 'player'].includes(draft.type)) continue;
    const fields = draft.fields || {};
    const name = String(fields.name || '').trim();
    if (!name || name.length > 60) throw new Error('角色卡名称需填写且不能超过 60 个字符');
    const packageId = draft.bundle_id || smartImportNewId();
    draft.bundle_id = packageId;
    characterIds.set(name.toLocaleLowerCase(), [...(characterIds.get(name.toLocaleLowerCase()) || []), packageId]);
    if (!Number.isInteger(fields.affinity) || fields.affinity < 0 || fields.affinity > 100) throw new Error('角色好感度需为 0 到 100 的整数');
    if (!fields.relationship_notes || Array.isArray(fields.relationship_notes) || typeof fields.relationship_notes !== 'object' || !fields.state_fields || Array.isArray(fields.state_fields) || typeof fields.state_fields !== 'object' || !Array.isArray(fields.categories)) throw new Error('角色关系、状态和分类字段格式无效');
    payload.characters.push({ package_id: packageId, name, summary: smartImportTextValue(fields.summary, 200, '角色摘要'), personality: smartImportTextValue(fields.personality, 10000, '性格'), speech_habits: smartImportTextValue(fields.speech_habits, 5000, '说话习惯'), memories: smartImportTextValue(fields.memories, 10000, '角色背景'), relationship_notes: fields.relationship_notes, state_fields: fields.state_fields, affinity: fields.affinity, clothing_type: smartImportTextValue(fields.clothing_type, 200, '衣着类型'), clothing_state: smartImportTextValue(fields.clothing_state, 200, '衣着状态'), is_player_controlled: draft.type === 'player', categories: fields.categories });
  }
  for (const draft of smartImportState.drafts) {
    if (draft.skipped || draft.type !== 'worldbook') continue;
    const fields = draft.fields || {};
    const name = String(fields.name || '').trim();
    if (!name || name.length > 120) throw new Error('世界书名称需填写且不能超过 120 个字符');
    const description = smartImportTextValue(fields.description, 20000, '世界书简介');
    if (!Array.isArray(fields.entries) || !fields.entries.length) throw new Error('世界书至少需要一条条目');
    const packageId = draft.bundle_id || smartImportNewId();
    draft.bundle_id = packageId;
    const categoryId = `smart-import-category-${draft.id}`;
    const entries = fields.entries.map((entry, index) => {
      if (!entry || typeof entry !== 'object' || Array.isArray(entry) || !String(entry.name || '').trim() || String(entry.name).trim().length > 160) throw new Error('世界书条目名称需填写且不能超过 160 个字符');
      const content = smartImportTextValue(entry.content, 200000, '世界书条目正文');
      const refs = Array.isArray(entry.scoped_characters) ? entry.scoped_characters : [];
      const scoped = refs.flatMap(ref => { const matches = characterIds.get(String(ref).trim().toLocaleLowerCase()) || []; return matches.length === 1 ? matches : []; });
      const keywords = Array.isArray(entry.keywords) ? entry.keywords.filter(value => typeof value === 'string') : [];
      if (keywords.length > 200 || keywords.some(value => value.length > 200)) throw new Error('每条世界书关键词最多 200 个且单个不超过 200 个字符');
      return { id: `smart-import-entry-${draft.id}-${index}`, name: String(entry.name).trim(), content, enabled: true, trigger_mode: keywords.length ? 'keyword' : 'always', keywords, insertion_position: 'before_character', priority: index, scope_type: scoped.length ? 'character' : 'global', category_ids: [categoryId], scoped_character_ids: [...new Set(scoped)], scoped_conversation_ids: [], import_metadata: {} };
    });
    payload.worldbooks.push({ package_id: packageId, payload: { format: 'neon-tavern-worldbook', version: 1, worldbook: { name, description, enabled: true }, categories: [{ id: categoryId, name: 'AI 导入条目', parent_id: null, position: 0 }], entries } });
  }
  if (!payload.characters.length && !payload.worldbooks.length) throw new Error('请至少保留一条可导入草稿');
  return payload;
}
function smartImportEdit(index, key, value) {
  const draft = smartImportState.drafts[index];
  if (!draft) return;
  if (['entries', 'relationship_notes', 'state_fields', 'categories'].includes(key)) {
    let parsed;
    try { parsed = JSON.parse(value); }
    catch { draft.editErrors = { ...(draft.editErrors || {}), [key]: true }; $('#smartImportStatus').textContent = `${({ entries: '世界书条目', relationship_notes: '关系备注', state_fields: '状态字段', categories: '角色分类' })[key]} JSON 格式无效，请检查后重试。`; smartImportInvalidatePreview(); return; }
    draft.fields[key] = parsed;
    if (draft.editErrors) delete draft.editErrors[key];
  } else if (key === 'affinity') {
    const numeric = Number(value);
    if (!/^\d+$/.test(value) || numeric > 100) { draft.editErrors = { ...(draft.editErrors || {}), [key]: true }; $('#smartImportStatus').textContent = '好感度需填写 0 到 100 的整数。'; smartImportInvalidatePreview(); return; }
    draft.fields[key] = numeric;
    if (draft.editErrors) delete draft.editErrors[key];
  } else draft.fields[key] = value;
  $('#smartImportStatus').textContent = '';
  smartImportInvalidatePreview();
}
$('#smartImportDrafts').addEventListener('input', event => {
  const field = event.target.closest('[data-smart-import-field]');
  if (field && field.dataset.smartImportField !== 'entries') smartImportEdit(Number(field.dataset.index), field.dataset.smartImportField, field.value);
});
$('#smartImportDrafts').addEventListener('change', event => {
  const type = event.target.closest('[data-smart-import-type]');
  const skipped = event.target.closest('[data-smart-import-skip]');
  const field = event.target.closest('[data-smart-import-field="entries"]');
  if (type) {
    const draft = smartImportState.drafts[Number(type.dataset.index)];
    if (!draft) return;
    draft.type = type.value;
    if (draft.type === 'worldbook' && !Array.isArray(draft.fields.entries)) draft.fields.entries = [{ name: '新条目', content: draft.source_excerpt || '', keywords: [] }];
    if (['npc', 'player'].includes(draft.type)) {
      if (!draft.fields.name) draft.fields.name = '';
      draft.fields.relationship_notes ||= {};
      draft.fields.state_fields ||= {};
      draft.fields.categories ||= [];
      draft.fields.affinity ??= 0;
    }
    smartImportRenderDrafts(); smartImportInvalidatePreview();
  } else if (skipped) {
    smartImportState.drafts[Number(skipped.dataset.index)].skipped = skipped.checked;
    smartImportInvalidatePreview();
  } else if (field) smartImportEdit(Number(field.dataset.index), 'entries', field.value);
});
function smartImportSourceChanged() {
  smartImportInvalidatePreview();
  $('#smartImportResults').hidden = true;
  $('#smartImportStatus').textContent = '输入已更改，请重新识别后再检查导入内容。';
}
$('#smartImportText').addEventListener('input', smartImportSourceChanged);
$('#smartImportFile').addEventListener('change', smartImportSourceChanged);
$('#smartImportAnalyze').addEventListener('click', async () => {
  if (state.readOnly) { $('#smartImportStatus').textContent = '管理员只读查看中，不能使用智能导入。'; return; }
  const button = $('#smartImportAnalyze');
  const file = $('#smartImportFile').files?.[0];
  const text = $('#smartImportText').value;
  if (file && text.trim()) { $('#smartImportStatus').textContent = '请只粘贴文字或选择文件其中一种输入。'; return; }
  if (!file && !text.trim()) { $('#smartImportStatus').textContent = '请粘贴文字或选择一个文件。'; return; }
  const body = file ? new FormData() : { text };
  if (file) body.append('file', file);
  smartImportInvalidatePreview();
  const revision = smartImportState.revision;
  button.disabled = true;
  $('#smartImportResults').hidden = true;
  $('#smartImportValidate').disabled = true;
  $('#smartImportStatus').textContent = '正在读取并识别，请稍候…';
  try {
    const result = await api('/api/smart-import/preview/', { method: 'POST', body });
    if (revision !== smartImportState.revision) { $('#smartImportStatus').textContent = '输入已更改，已忽略过期的识别结果。请重新识别。'; return; }
    smartImportState.drafts = result.drafts.map(draft => ({ ...draft, fields: { ...(draft.fields || {}) }, skipped: draft.type === 'unknown' }));
    smartImportState.payload = result.payload;
    smartImportState.revision += 1;
    $('#smartImportResults').hidden = false;
    $('#smartImportValidate').disabled = false;
    $('#smartImportBundlePreview').hidden = true;
    $('#smartImportConfirm').hidden = true;
    smartImportRenderDrafts();
    $('#smartImportStatus').textContent = `识别完成，找到 ${result.drafts.length} 条草稿。请检查后继续。`;
    if (result.warnings?.length) $('#smartImportFinalWarnings').innerHTML = `<b>识别提示</b><div style="margin-top:6px">${result.warnings.map(h).join('<br>')}</div>`, $('#smartImportFinalWarnings').hidden = false;
    else $('#smartImportFinalWarnings').hidden = true;
  } catch (error) { if (revision === smartImportState.revision) $('#smartImportStatus').textContent = `识别失败：${error.message}。输入内容仍保留，可修改后重试。`; }
  finally { button.disabled = false; $('#smartImportValidate').disabled = false; }
});
$('#smartImportValidate').addEventListener('click', async () => {
  if (state.readOnly) { $('#smartImportStatus').textContent = '管理员只读查看中，不能导入素材。'; return; }
  const button = $('#smartImportValidate'); button.disabled = true;
  try {
    const payload = smartImportBuildPayload();
    const revision = smartImportState.revision;
    const preview = await api('/api/bundles/import/preview/', { method: 'POST', body: { payload } });
    if (revision !== smartImportState.revision) { $('#smartImportStatus').textContent = '草稿已更改，已忽略过期的检查结果，请重新检查。'; return; }
    smartImportState.payload = payload; smartImportState.preview = preview;
    $('#smartImportBundlePreview').innerHTML = `<b>导入检查</b><div style="margin-top:6px">${preview.counts.characters} 张角色卡 · ${preview.counts.worldbooks} 本世界书 · ${preview.counts.worldbook_entries} 个条目</div>${(preview.warnings || []).length ? `<div class="bundle-warning">${preview.warnings.map(h).join('<br>')}</div>` : ''}`;
    $('#smartImportBundlePreview').hidden = false;
    $('#smartImportConfirm').hidden = false;
    $('#smartImportStatus').textContent = '检查通过。确认后才会写入素材。';
  } catch (error) { smartImportInvalidatePreview(); $('#smartImportStatus').textContent = `检查失败：${error.message}`; }
  finally { button.disabled = false; }
});
$('#smartImportConfirm').addEventListener('click', async () => {
  if (state.readOnly) { $('#smartImportStatus').textContent = '管理员只读查看中，不能导入素材。'; return; }
  if (!smartImportState.preview || !smartImportState.payload) return;
  const button = $('#smartImportConfirm'); button.disabled = true;
  try {
    const result = await api('/api/bundles/import/commit/', { method: 'POST', body: { payload: smartImportState.payload } });
    await refreshLists(); await loadWorldBooks(result.worldbooks[0]?.id || null);
    window.toast(`已导入 ${result.characters.length} 张角色卡和 ${result.worldbooks.length} 本世界书`);
    smartImportState.drafts = []; smartImportState.payload = null; smartImportState.preview = null;
    $('#smartImportText').value = ''; $('#smartImportFile').value = ''; $('#smartImportResults').hidden = true;
    window.openPanel('materials');
  } catch (error) { $('#smartImportStatus').textContent = `导入失败：${error.message}。草稿仍保留，可重新检查或重试。`; }
  finally { button.disabled = false; }
});

window.closeBundleSheet = () => {
  $('#bundleOverlay').classList.remove('open');
  $('#bundleOverlay').classList.remove('bundle-market-top');
  bundleState.payload = null;
  bundleState.preview = null;
  bundleState.mode = 'file';
  $('#bundleFile').value = '';
};

function renderBundleChoices() {
  $('#bundleCharacters').innerHTML = state.characters.map(item => `<label class="bundle-option"><input type="checkbox" data-bundle-kind="character" value="${h(item.id)}"><span><b>${h(item.name)}</b><small>${h(item.summary || (item.is_player_controlled ? '玩家角色' : 'NPC'))}</small></span></label>`).join('') || '<div class="bundle-empty">还没有角色卡</div>';
  $('#bundleWorldbooks').innerHTML = worldBookState.books.map(item => `<label class="bundle-option"><input type="checkbox" data-bundle-kind="worldbook" value="${h(item.id)}"><span><b>${h(item.name)}</b><small>${h(item.description || '世界设定')}</small></span></label>`).join('') || '<div class="bundle-empty">还没有世界书</div>';
}

async function openBundleSheet(mode = 'file') {
  if (state.readOnly && mode !== 'market') return window.toast('只读查看中，不能导入或导出素材');
  try {
    bundleState.mode = mode;
    await refreshLists();
    await loadWorldBooks();
    renderBundleChoices();
    $('#bundlePreview').hidden = true;
    $('#bundleMessage').textContent = '';
    $('#bundleCommitButton').hidden = true;
    $('#bundleImportButton').hidden = mode === 'market';
    $('#bundleExportButton').hidden = mode === 'market';
    $('#bundleMarketButton').hidden = mode !== 'market';
    $('#bundleOverlay').classList.toggle('bundle-market-top', mode === 'market');
    $('#bundleOverlay').classList.add('open');
  } catch (error) { fail(error); }
}

function renderBundlePreview(preview) {
  const conflicts = [
    ...preview.characters.filter(item => item.conflict).map(item => `角色卡：${item.name}`),
    ...preview.worldbooks.filter(item => item.conflict).map(item => `世界书：${item.worldbook.name}`),
  ];
  const warnings = preview.warnings || [];
  $('#bundlePreview').hidden = false;
  $('#bundlePreview').innerHTML = `<b>检查结果</b><div>${preview.counts.characters} 张角色卡 · ${preview.counts.worldbooks} 本世界书 · ${preview.counts.worldbook_categories} 个分类 · ${preview.counts.worldbook_entries} 个条目</div>${conflicts.length ? `<div class="bundle-warning"><b>重名将保留副本</b><br>${conflicts.map(h).join('<br>')}</div>` : ''}${warnings.length ? `<div class="bundle-warning">${warnings.map(h).join('<br>')}</div>` : ''}`;
  $('#bundleCommitButton').hidden = false;
}

$$('[data-open-bundle]').forEach(button => button.addEventListener('click', () => openBundleSheet()));
$$('[data-bundle-select]').forEach(button => button.addEventListener('click', () => {
  const kind = button.dataset.bundleSelect === 'characters' ? 'character' : 'worldbook';
  const boxes = $$(`#bundleOverlay input[data-bundle-kind="${kind}"]`);
  const selectAll = boxes.some(box => !box.checked);
  boxes.forEach(box => { box.checked = selectAll; });
  button.textContent = selectAll ? '取消全选' : '全选';
}));
$('#bundleImportButton').addEventListener('click', () => $('#bundleFile').click());
$('#bundleFile').addEventListener('change', async event => {
  const file = event.target.files?.[0];
  if (!file) return;
  try {
    if (file.size > 20 * 1024 * 1024) throw new Error('整合包不能超过 20 MB');
    const payload = await readJsonFile(file);
    if (payload.format !== 'neon-tavern-bundle') throw new Error('这不是霓虹酒馆整合包');
    const preview = await api('/api/bundles/import/preview/', { method: 'POST', body: { payload } });
    bundleState.payload = payload;
    bundleState.preview = preview;
    renderBundlePreview(preview);
    $('#bundleMessage').textContent = '请核对上方内容与警告，再确认导入。';
  } catch (error) { $('#bundleMessage').textContent = error.message; }
  finally { event.target.value = ''; }
});
$('#bundleExportButton').addEventListener('click', async () => {
  const character_ids = $$('input[data-bundle-kind="character"]:checked').map(item => item.value);
  const worldbook_ids = $$('input[data-bundle-kind="worldbook"]:checked').map(item => item.value);
  if (!character_ids.length && !worldbook_ids.length) return window.toast('至少选择一张角色卡或一本世界书');
  const button = $('#bundleExportButton'); button.disabled = true;
  try {
    const payload = await api('/api/bundles/export/', { method: 'POST', body: { character_ids, worldbook_ids } });
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = '霓虹酒馆-整合包.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    $('#bundleMessage').textContent = `已导出 ${character_ids.length} 张角色卡和 ${worldbook_ids.length} 本世界书。`;
  } catch (error) { $('#bundleMessage').textContent = error.message; }
  finally { button.disabled = false; }
});
$('#bundleMarketButton').addEventListener('click', () => {
  const character_ids = $$('input[data-bundle-kind="character"]:checked').map(item => item.value);
  const worldbook_ids = $$('input[data-bundle-kind="worldbook"]:checked').map(item => item.value);
  if (!character_ids.length && !worldbook_ids.length) return $('#bundleMessage').textContent = '至少选择一张角色卡或一本世界书';
  marketState.bundleSelection = { character_ids, worldbook_ids };
  $('#marketBundleSelectionLabel').textContent = `已选择 ${character_ids.length} 张角色卡和 ${worldbook_ids.length} 本世界书`;
  $('#marketChooseBundle').textContent = '修改所选内容';
  window.closeBundleSheet();
});
$('#bundleCommitButton').addEventListener('click', async () => {
  if (!bundleState.payload || !bundleState.preview) return;
  const button = $('#bundleCommitButton'); button.disabled = true;
  try {
    const result = await api('/api/bundles/import/commit/', { method: 'POST', body: { payload: bundleState.payload } });
    await refreshLists(); await loadWorldBooks(result.worldbooks[0]?.id || null);
    window.closeBundleSheet();
    window.toast(`已导入 ${result.characters.length} 张角色卡和 ${result.worldbooks.length} 本世界书`);
  } catch (error) { $('#bundleMessage').textContent = error.message; }
  finally { button.disabled = false; }
});

function marketContent(payload) {
  if (payload?.format === 'neon-tavern-bundle') {
    const characters = (payload.characters || []).map(item => `角色卡：${item.name || '未命名角色'}${item.summary ? `\n${item.summary}` : ''}`);
    const worldbooks = (payload.worldbooks || []).map(row => {
      const book = row.payload || {};
      return `世界书：${book.worldbook?.name || '未命名世界书'}${book.worldbook?.description ? `\n${book.worldbook.description}` : ''}`;
    });
    return [...characters, ...worldbooks].join('\n\n') || '整合包没有可预览内容';
  }
  if (Array.isArray(payload?.characters)) return payload.characters.map(item => [item.name, item.summary, item.personality, item.speech_habits].filter(Boolean).join('\n')).join('\n\n');
  const book = payload?.worldbook || {};
  const entries = (payload?.entries || []).slice(0, 30).map(item => `【${item.name || '未命名条目'}】\n${item.content || ''}`).join('\n\n');
  return [book.name, book.description, `分类：${(payload?.categories || []).map(item => item.name).join('、')}`, entries, (payload?.entries || []).length > 30 ? '其余条目已省略预览。' : ''].filter(Boolean).join('\n\n');
}

function renderMarket() {
  const kindName = kind => ({ character: '角色卡', worldbook: '世界书', bundle: '整合包' }[kind] || '素材');
  $('#marketListings').innerHTML = marketState.listings.map(item => `<article class="market-item"><button type="button" class="market-item-main" data-market-open="${h(item.id)}"><div class="market-item-head"><b>${h(item.title)}</b><span>${kindName(item.kind)}</span></div><p>${h(item.description || item.preview || '暂无简介')}</p><div class="market-item-foot"><span>${h(item.author_alias || '匿名')}</span><span>·</span><span>${h(item.scope === 'public' ? '公共市场' : '本站')}</span>${(item.tags || []).slice(0, 4).map(tag => `<span>${h(tag)}</span>`).join('')}</div></button>${item.can_withdraw ? `<div class="market-item-actions"><button type="button" data-market-withdraw="${h(item.id)}">撤回发布</button></div>` : ''}</article>`).join('') || '<div class="market-empty">没有找到素材。可以调整筛选，或发布自己的角色卡、世界书和整合包。</div>';
  $('#marketPublishOpen').hidden = state.readOnly;
}

async function loadMarket() {
  $('#marketStatus').textContent = marketState.scope === 'public' ? '正在连接公共市场…' : '本站素材';
  $('#marketListings').innerHTML = '<div class="market-empty">正在加载素材…</div>';
  const params = new URLSearchParams({ scope: marketState.scope, kind: marketState.kind, q: marketState.query });
  try {
    const data = await api(`/api/market/listings/?${params}`);
    marketState.listings = listFrom(data, 'listings');
    renderMarket();
    $('#marketRetry').hidden = true;
    $('#marketStatus').textContent = data.remote ? `公共市场 · ${marketState.listings.length} 项` : `${marketState.scope === 'public' ? '本站公共市场' : '本站素材'} · ${marketState.listings.length} 项`;
  } catch (error) {
    marketState.listings = [];
    renderMarket();
    $('#marketStatus').textContent = `暂时无法读取：${error.message}`;
    $('#marketRetry').hidden = marketState.scope !== 'public';
  }
}

window.closeMarketDetail = () => { $('#marketDetailOverlay').classList.remove('open'); marketState.selected = null; };
window.closeMarketPublish = () => $('#marketPublishOverlay').classList.remove('open');
window.openPanel = id => { original.openPanel(id); if (id === 'materials') loadMarket(); };

async function openMarketDetail(id) {
  try {
    const data = await api(`/api/market/listings/${path(id)}/?scope=${encodeURIComponent(marketState.scope)}`);
    marketState.selected = { ...data, scope: marketState.scope };
    $('#marketDetailTitle').textContent = data.title || '素材详情';
    const kindName = ({ character: '角色卡', worldbook: '世界书', bundle: '整合包' })[data.kind] || '素材';
    $('#marketDetailMeta').textContent = `${kindName} · ${data.author_alias || '匿名'} · ${data.scope === 'public' ? '公共市场' : '本站'}`;
    $('#marketDetailDescription').textContent = data.description || data.preview || '暂无简介';
    $('#marketDetailContent').textContent = marketContent(data.payload || {});
    $('#marketDetailWarnings').textContent = (data.warnings || []).join(' ');
    $('#marketWithdraw').hidden = !data.can_withdraw;
    $('#marketReport').hidden = data.scope !== 'public' && data.can_withdraw;
    $('#marketDetailOverlay').classList.add('open');
  } catch (error) { window.toast(error.message); }
}

function updateMarketSources() {
  const kind = $('#marketPublishKind').value;
  const isBundle = kind === 'bundle';
  $('#marketPublishSourceField').hidden = isBundle;
  $('#marketPublishBundle').hidden = !isBundle;
  $('#marketPublishSource').required = !isBundle;
  const items = kind === 'character' ? state.characters.map(item => ({ id: item.id, name: item.name, description: item.summary || '' })) : worldBookState.books.map(item => ({ id: item.id, name: item.name, description: item.description || '' }));
  $('#marketPublishSource').innerHTML = items.map(item => `<option value="${h(item.id)}" data-title="${h(item.name)}" data-description="${h(item.description)}">${h(item.name)}</option>`).join('') || '<option value="">沒有可發布素材</option>';
  if (isBundle) {
    $('#marketPublishName').value = '整合包';
    $('#marketPublishDescription').value = '';
    return;
  }
  const selected = $('#marketPublishSource').selectedOptions[0];
  $('#marketPublishName').value = selected?.dataset.title || '';
  $('#marketPublishDescription').value = selected?.dataset.description || '';
}

function fillMarketSourceFields() {
  const selected = $('#marketPublishSource').selectedOptions[0];
  $('#marketPublishName').value = selected?.dataset.title || '';
  $('#marketPublishDescription').value = selected?.dataset.description || '';
}

async function openMarketPublish() {
  if (state.readOnly) return window.toast('只读查看中，不能发布素材');
  try {
    await refreshLists(); await loadWorldBooks();
    marketState.bundleSelection = null;
    $('#marketBundleSelectionLabel').textContent = '尚未选择内容';
    $('#marketChooseBundle').textContent = '选择角色卡和世界书';
    updateMarketSources();
    $('#marketPublishScope').value = marketState.scope;
    $('#marketPublishMessage').textContent = '';
    $('#marketPublishOverlay').classList.add('open');
  } catch (error) { window.toast(error.message); }
}

async function submitMarketListing() {
  const source = $('#marketPublishSource');
  const kind = $('#marketPublishKind').value;
  const tags = $('#marketPublishTags').value.split(/[,，;；、|\s]+/).filter(Boolean).slice(0, 20);
  const body = {
    kind, scope: $('#marketPublishScope').value,
    title: $('#marketPublishName').value.trim(), description: $('#marketPublishDescription').value.trim(),
    tags,
    author_alias: $('#marketPublishAlias').value.trim(),
  };
  const missingField = !body.title ? '请填写展示名称。'
    : !body.description ? '请填写简介。'
      : !tags.length ? '请至少填写一个标签。'
        : !body.author_alias ? '请填写署名。'
          : '';
  if (missingField) return $('#marketPublishMessage').textContent = missingField;
  if (kind === 'bundle') {
    if (!marketState.bundleSelection || !(marketState.bundleSelection.character_ids.length + marketState.bundleSelection.worldbook_ids.length)) return $('#marketPublishMessage').textContent = '请至少选择一张角色卡或一本世界书。';
    body.character_ids = marketState.bundleSelection.character_ids;
    body.worldbook_ids = marketState.bundleSelection.worldbook_ids;
  } else {
    if (!source.value) return $('#marketPublishMessage').textContent = '请先创建可发布的角色卡或世界书。';
    body.source_id = source.value;
  }
  const scopeName = body.scope === 'public' ? '公共市场' : '本站';
  if (!window.confirm(`确认将“${body.title || '未命名素材'}”发布到${scopeName}吗？发布内容是素材快照，不会同步后续修改。`)) return;
  const button = $('#marketPublishSubmit'); button.disabled = true;
  try {
    await api('/api/market/listings/', { method: 'POST', body });
    window.closeMarketPublish(); await loadMarket(); window.toast('素材已发布');
  } catch (error) { $('#marketPublishMessage').textContent = error.message; }
  finally { button.disabled = false; }
}

$('#marketPublishOpen').addEventListener('click', openMarketPublish);
$('#marketPublishKind').addEventListener('change', updateMarketSources);
$('#marketPublishSource').addEventListener('change', fillMarketSourceFields);
$('#marketChooseBundle').addEventListener('click', () => openBundleSheet('market'));
$('#marketPublishSubmit').addEventListener('click', submitMarketListing);
$('#marketRetry').addEventListener('click', loadMarket);
$$('[data-market-scope]').forEach(button => button.addEventListener('click', () => {
  marketState.scope = button.dataset.marketScope;
  $$('[data-market-scope]').forEach(item => item.classList.toggle('active', item === button));
  $$('[data-market-kind]').forEach(item => item.classList.toggle('active', item.dataset.marketKind === marketState.kind));
  loadMarket();
}));
$$('[data-market-kind]').forEach(button => button.addEventListener('click', () => {
  marketState.kind = button.dataset.marketKind;
  $$('[data-market-kind]').forEach(item => item.classList.toggle('active', item === button));
  loadMarket();
}));
$('#marketSearch').addEventListener('input', () => {
  marketState.query = $('#marketSearch').value.trim(); clearTimeout(marketState.timer);
  marketState.timer = setTimeout(loadMarket, 250);
});
$('#marketDownload').addEventListener('click', async () => {
  if (!marketState.selected) return;
  const selectedKind = marketState.selected.kind;
  const button = $('#marketDownload'); button.disabled = true;
  try {
    if (selectedKind === 'bundle') {
      const payload = marketState.selected.payload;
      const preview = await api('/api/bundles/import/preview/', { method: 'POST', body: { payload } });
      bundleState.payload = payload;
      bundleState.preview = preview;
      bundleState.mode = 'market-import';
      $('#bundleMessage').textContent = '请核对整合包内容与冲突，再确认导入。';
      renderBundlePreview(preview);
      $('#bundleImportButton').hidden = true;
      $('#bundleExportButton').hidden = true;
      $('#bundleMarketButton').hidden = true;
      $('#bundleOverlay').classList.add('bundle-market-top', 'open');
      window.closeMarketDetail();
      return;
    }
    const result = await api(`/api/market/listings/${path(marketState.selected.id)}/download/`, { method: 'POST', body: { scope: marketState.scope } });
    await refreshLists(); await loadWorldBooks(); window.closeMarketDetail(); await loadMarket();
    if (selectedKind === 'bundle') {
      window.toast(`已导入 ${result.characters?.length || 0} 张角色卡和 ${result.worldbooks?.length || 0} 本世界书`);
    } else {
      window.toast(`已导入 ${result.characters?.[0]?.name || result.worldbooks?.[0]?.name || '素材'} 到我的账号`);
    }
  } catch (error) { window.toast(error.message); }
  finally { button.disabled = false; }
});
$('#marketWithdraw').addEventListener('click', async () => {
  const item = marketState.selected;
  if (!item || !window.confirm(`确定撤回“${item.title}”吗？`)) return;
  try { await api(`/api/market/listings/${path(item.id)}/withdraw/`, { method: 'POST', body: {} }); window.closeMarketDetail(); await loadMarket(); }
  catch (error) { window.toast(error.message); }
});
$('#marketReport').addEventListener('click', async () => {
  const item = marketState.selected; if (!item) return;
  const reason = window.prompt('请填写举报原因（500 字以内）'); if (!reason?.trim()) return;
  try { await api(`/api/market/listings/${path(item.id)}/report/`, { method: 'POST', body: { scope: item.scope, reason: reason.trim() } }); window.toast('举报已提交，管理员会进行审核'); }
  catch (error) { window.toast(error.message); }
});
$('#marketListings').addEventListener('click', event => {
  const detail = event.target.closest('[data-market-open]');
  if (detail) openMarketDetail(detail.dataset.marketOpen);
  const withdraw = event.target.closest('[data-market-withdraw]');
  if (withdraw) api(`/api/market/listings/${path(withdraw.dataset.marketWithdraw)}/withdraw/`, { method: 'POST', body: {} }).then(loadMarket).catch(error => window.toast(error.message));
});

async function refreshLists() {
  if (state.readOnly) {
    const detail = await api(`/api/admin/users/${path(state.adminUser.id)}/`);
    state.adminUser = detail.user;
    state.characters = listFrom(detail, 'characters');
    state.conversations = listFrom(detail, 'conversations');
  } else {
    const [characters, conversations, categories] = await Promise.all([api('/api/characters/'), api('/api/conversations/'), api('/api/character-categories/')]);
    state.characters = listFrom(characters, 'characters');
    state.conversations = listFrom(conversations, 'conversations');
    state.characterCategories = listFrom(categories, 'categories');
  }
  renderHome(); renderCharacters(); renderPicker();
}

function renderHome() {
  $('#conversationHome .home-count').textContent = state.conversations.length;
  $('#conversationList').innerHTML = state.conversations.map(item => `<div class="managed-item"><button class="conversation" data-conversation="${h(item.id)}"><div class="conv-avatar">${h(item.title.slice(0, 1))}</div><div class="conv-body"><div class="conv-top"><b>${h(item.title)}</b><small>${dateLabel(item.updated_at)}</small></div><p>${h((item.participants || []).map(actor => actor.name).join(' · '))}</p><div class="conv-tags">${(item.participants || []).map(actor => `<span>${h(actor.name)}</span>`).join('')}</div></div></button>${state.readOnly ? '' : `<div class="item-actions"><button type="button" data-edit-conversation="${h(item.id)}" aria-label="修改对话">编辑</button><button type="button" class="danger" data-delete-conversation="${h(item.id)}" aria-label="删除对话">删除</button></div>`}</div>`).join('') || '<div class="live-empty">还没有对话，先创建玩家和 NPC 角色卡。</div>';
  $('#conversationHome .new-conv-card').hidden = state.readOnly;
}

function renderCharacters() {
  $$('#characters > .card, #characters .character-item').forEach(element => element.remove());
  $('#characters .add-character-card').hidden = state.readOnly;
  $('#characters .section-label').after($('#characterCategoryTree'));
  const flatten = (nodes, depth = 0) => nodes.flatMap(item => [{ ...item, depth }, ...flatten(item.children || [], depth + 1)]);
  const categories = flatten(state.characterCategories);
  const memberships = new Map(state.characters.map(character => [character.id, categories.filter(category => (character.category_ids || []).includes(category.id))]));
  const shown = new Set();
  const card = character => {
    shown.add(character.id);
    const labels = (memberships.get(character.id) || []).map(category => category.name);
    return `<div class="managed-item character-item"><div class="card" data-character="${h(character.id)}"><div class="card-head"><div class="avatar">${h(character.name.slice(0, 1))}</div><div><b>${h(character.name)}</b><small>${h(character.summary || (character.is_player_controlled ? '玩家角色' : 'NPC'))}</small></div><span class="role-badge ${character.is_player_controlled ? 'user' : ''}">${character.is_player_controlled ? 'USER' : 'NPC'}</span></div><div class="affinity-line"><div><span>好感度</span><b>${character.affinity ?? 0}%</b></div><div class="affinity-bar"><i style="width:${character.affinity ?? 0}%"></i></div></div><div class="tags">${labels.map(label => `<span>${h(label)}</span>`).join('') || '<span>未分类</span>'}</div></div>${state.readOnly ? '' : `<div class="item-actions"><button type="button" data-export-character="${h(character.id)}">导出</button><button type="button" data-edit-character="${h(character.id)}">编辑</button><button type="button" class="danger" data-delete-character="${h(character.id)}">删除</button></div>`}</div>`;
  };
  const renderCategory = category => {
    const children = (category.children || []).map(renderCategory).join('');
    const own = state.characters.filter(character => !character.is_player_controlled && (memberships.get(character.id) || []).some(item => item.id === category.id) && !shown.has(character.id)).map(card).join('');
    const actions = state.readOnly ? '' : `<span class="category-actions"><button type="button" data-export-character-category="${h(category.id)}">导出</button><button data-character-category-add="${h(category.id)}">＋</button><button data-character-category-edit="${h(category.id)}">编辑</button><button data-character-category-delete="${h(category.id)}">删除</button></span>`;
    return `<details class="character-category"><summary><b>${h(category.name)}</b>${actions}</summary>${own}${children}</details>`;
  };
  const roots = state.characterCategories.map(renderCategory).join('');
  const playerCards = state.characters.filter(character => character.is_player_controlled).map(card).join('');
  const unassigned = state.characters.filter(character => !character.is_player_controlled && !shown.has(character.id)).map(card).join('');
  const playerCategory = playerCards ? `<details class="character-category character-user-category"><summary><b>我的角色卡</b><small class="category-count">${state.characters.filter(character => character.is_player_controlled).length} 张</small></summary>${playerCards}</details>` : '';
  const uncategorized = unassigned ? `<details class="character-category"><summary><b>未分类</b></summary>${unassigned}</details>` : '';
  $('#characterCategoryTree').innerHTML = `${playerCategory}${roots}${uncategorized}` || '<div class="live-empty">还没有角色卡</div>';
  $('#characterTools').hidden = state.readOnly;
}

function renderPicker() {
  const npcs = state.characters.filter(character => !character.is_player_controlled);
  $('#newPlayer').innerHTML = state.characters.filter(character => character.is_player_controlled).map(character => `<option value="${h(character.id)}">${h(character.name)}</option>`).join('');
  $('#conversationRoleScroll').innerHTML = npcs.map(character => `<button type="button" class="create-role" data-id="${h(character.id)}" data-role-search="${h(`${character.name} ${character.summary}`)}" onclick="toggleCreateRole(this)"><div class="avatar">${h(character.name.slice(0, 1))}</div><div><b>${h(character.name)}</b><small>${h(character.summary)}</small></div><span></span></button>`).join('') || '<div class="live-empty">请先创建 NPC 角色卡</div>';
}

function normalizeStatusSource(character, snapshot) {
  const source = snapshot && typeof snapshot === 'object' && Object.keys(snapshot).length ? { ...snapshot } : { ...(character.state_fields || {}), affinity: character.affinity, clothing_type: character.clothing_type, clothing_state: character.clothing_state };
  const wrapped = source[character.name];
  if (wrapped && typeof wrapped === 'object' && !Array.isArray(wrapped)) {
    delete source[character.name];
    Object.entries(wrapped).forEach(([key, value]) => { if (!(key in source)) source[key] = value; });
  }
  return source;
}

function stateCard(character, snapshot) {
  const source = normalizeStatusSource(character, snapshot);
  const affinity = Number(source.affinity ?? character.affinity ?? 0);
  const fields = Object.fromEntries(Object.entries(source).filter(([key]) => !['affinity', 'clothing_type', 'clothing_state'].includes(key)));
  return `<div class="status-card character-status current"><div class="status-head"><span>♡ STATUS</span><b>${h(character.name)}</b><small>最新状态</small></div><div class="affinity-line"><div><span>好感度</span><b>${affinity}%</b></div><div class="affinity-bar"><i style="width:${Math.max(0, Math.min(100, affinity))}%"></i></div></div><div class="status-grid"><div>衣服类型 <b>${h(source.clothing_type || character.clothing_type || '未设置')}</b></div><div>衣服状态 <b>${h(source.clothing_state || character.clothing_state || '未设置')}</b></div>${Object.entries(fields).map(([key, value]) => `<div>${h(key)} <b>${h(typeof value === 'string' ? value : JSON.stringify(value))}</b></div>`).join('')}</div></div>`;
}

function quotedDialogue(content) {
  const value = String(content || '').trim().replace(/^[“\"]([\s\S]*)[”\"]$/, '$1');
  return `“${h(value)}”`;
}

function renderConversationMessages(conversation) {
  const participants = conversation.participants || [];
  const blocks = [];
  let group = null;
  let round = 0;
  const flush = () => {
    if (!group) return;
    round += 1;
    const actor = group.actor;
    const thoughts = group.thoughts.map(content => `<p>${h(content)}</p>`).join('');
    const body = group.body.map(item => item.kind === 'dialogue'
      ? `<p class="turn-dialogue">${quotedDialogue(item.content)}</p>`
      : `<p class="turn-action">${h(item.content)}</p>`).join('');
    blocks.push(`<div class="day-divider"><span>第 ${round} 轮</span></div><div class="msg npc"><div class="msg-avatar">${h((actor?.name || '旁白').slice(0, 1))}</div><div class="msg-main"><div class="meta"><b>${h(actor?.name || '旁白')}</b><span>${dateLabel(group.createdAt)}</span></div>${thoughts ? `<div class="thought-panel"><small>内心想法</small>${thoughts}</div>` : ''}${body ? `<div class="bubble turn-bubble">${body}</div>` : ''}</div></div>${actor ? stateCard(actor, group.snapshot) : ''}`);
    group = null;
  };
  for (const message of conversation.messages || []) {
    const actor = participants.find(participant => participant.id === message.speaker_id);
    const mine = message.speaker_id === conversation.player_character_id;
    if (message.kind === 'ooc') {
      flush();
      blocks.push(`<div class="ooc-message"><small>场外 · 导演</small><p>${h(message.content.replace(/^\/\/\s*/, ''))}</p></div>`);
      continue;
    }
    if (mine || message.source !== 'ai') {
      flush();
      round += 1;
      blocks.push(`<div class="day-divider"><span>第 ${round} 轮</span></div><div class="msg player-msg"><div class="msg-avatar player-avatar">${h((actor?.name || '我').slice(0, 1))}</div><div class="msg-main"><div class="meta"><b>${h(actor?.name || '我')}</b><span>${dateLabel(message.created_at)}</span></div><div class="bubble player"><p>${h(message.content)}</p></div></div></div>`);
      continue;
    }
    if (!group || group.actor?.id !== actor?.id) {
      flush();
      group = { actor, thoughts: [], body: [], snapshot: null, createdAt: message.created_at };
    }
    if (message.kind === 'thought') group.thoughts.push(message.content);
    else if (message.kind === 'state') {
      group.snapshot = message.state_snapshot;
      flush();
    } else group.body.push({ kind: message.kind, content: message.content });
  }
  flush();
  return blocks.join('');
}

function formatElapsed(value) {
  if (value === null || value === undefined) return '--';
  return value < 1000 ? `${value}ms` : `${(value / 1000).toFixed(1)}s`;
}

function generationTimingMarkup(timings) {
  if (!timings) return '';
  const labels = [
    ['排队', timings.queue_wait_ms], ['响应头', timings.headers_ms], ['首 token', timings.first_token_ms],
    ['内心出现', timings.thought_visible_ms], ['正文出现', timings.public_visible_ms],
    ['完整返回', timings.response_complete_ms], ['状态完成', timings.state_ready_ms],
    ['保存', timings.persist_ms], ['总计', timings.actor_total_ms || timings.model_total_ms],
  ];
  return `<div class="generation-timing"><small>本轮生成计时</small><div>${labels.map(([label, value]) => `<span>${label} <b>${formatElapsed(value)}</b></span>`).join('')}</div></div>`;
}

function shouldFollowChatBottom(container) {
  if (!container) return true;
  return container.scrollHeight - container.scrollTop - container.clientHeight <= 48;
}

function renderChat(forceBottom = false) {
  const conversation = state.current;
  if (!conversation) return;
  const followChatBottom = forceBottom || shouldFollowChatBottom($('#content'));
  $('.header .title').textContent = conversation.title;
  const environment = conversation.environment || {};
  $('.env span').innerHTML = `<i class="dot"></i>${h([environment['时间'], environment['地点']].filter(Boolean).join(' · ') || conversation.title)}`;
  const participants = `<div id="chatParticipants" class="chat-participants"><b>在场角色</b>${(conversation.participants || []).map(actor => `<span class="${actor.is_player_controlled ? 'player' : ''}">${h(actor.name)}${actor.is_player_controlled ? ' · 玩家' : ''}</span>`).join('')}</div>`;
  const messages = renderConversationMessages(conversation);
  const waiting = messages ? '等待你的下一句话' : '等待你的第一句话';
  const queuedAction = state.lastJob?.conversation_id === conversation.id && state.lastJob.status === 'queued' ? `<button data-cancel-job="${h(state.lastJob.id)}">取消本轮生成</button>` : '';
  const partial = state.lastJob?.conversation_id === conversation.id ? Object.entries(state.lastJob.progress || {}).filter(([, item]) => item.partial || item.thought || item.timings).map(([, item]) => `<div class="stream-preview">${item.thought ? `<div class="thought-panel"><small>内心想法</small><p>${h(item.thought)}</p></div>` : ''}${item.partial ? `<small>正在生成公开回应</small><p>${h(item.partial)}</p>` : ''}${item.retryable ? `<button data-retry-job="${h(state.lastJob.id)}">重试本轮</button>` : ''}${generationTimingMarkup(item.timings)}</div>`).join('') : '';
  $('#chatView').innerHTML = `${participants}<div class="chat-title-card"><div><div class="eyebrow">CONVERSATION</div><strong id="chatTitle">${h(conversation.title)}</strong></div><div>${state.readOnly ? '' : '<button data-chat-settings>设置</button>'}<button onclick="closeChat()">返回</button></div></div><div class="environment-card"><div class="env-head"><b>${h(environment['地点'] || '当前场景')}</b><span>当前环境</span></div><div class="env-desc">${h(environment['环境描写'] || '')}</div><div class="env-tags">${Object.entries(environment).filter(([key, value]) => key !== '环境描写' && value).map(([, value]) => `<span>${h(value)}</span>`).join('')}</div></div>${messages}${partial}<div class="waiting"><span>${waiting}</span>${queuedAction}</div>`;
  const currentParticipants = conversation.participants || [];
  const player = currentParticipants.find(actor => actor.id === conversation.player_character_id);
  const replyActor = currentParticipants.find(actor => actor.id === state.replyActorId) || player || currentParticipants[0];
  state.replyActorId = replyActor?.id || null;
  $('#currentAvatar').textContent = (replyActor?.name || '我').slice(0, 1);
  $('#currentRole').textContent = `${replyActor?.name || '我'} · ${replyActor?.is_player_controlled ? '玩家卡' : 'NPC 卡'}`;
  $('#messageInput').placeholder = `回复 ${replyActor?.name || '我'}……`;
  const roleSheet = $('#roleSheet');
  const roleSheetBody = roleSheet?.querySelector('.sheet');
  if (roleSheetBody) roleSheetBody.innerHTML = `<div class="handle"></div><h2>回复角色</h2><p class="sheet-desc">选择一张在场角色卡，用户发送的下一条消息会记录为该角色发言。</p>${currentParticipants.map(actor => `<div class="role role-select ${actor.id === replyActor?.id ? 'selected' : ''}" data-reply-actor="${h(actor.id)}" role="button" tabindex="0"><div class="avatar">${h((actor.name || '我').slice(0, 1))}</div><div><b>${h(actor.name)}</b><small>${actor.is_player_controlled ? '玩家角色卡' : 'NPC 角色卡'}</small></div><div class="check ${actor.id === replyActor?.id ? 'checked' : ''}"></div></div>`).join('')}`;
  if (roleSheet && roleSheetBody && !roleSheetBody.dataset.replyBinding) {
    roleSheetBody.addEventListener('click', event => {
      const option = event.target.closest('[data-reply-actor]');
      if (!option || !state.current) return;
      const actor = (state.current.participants || []).find(item => item.id === option.dataset.replyActor);
      if (!actor) return;
      state.replyActorId = actor.id;
      $('#currentAvatar').textContent = (actor.name || '我').slice(0, 1);
      $('#currentRole').textContent = `${actor.name || '我'} · ${actor.is_player_controlled ? '玩家卡' : 'NPC 卡'}`;
      $('#messageInput').placeholder = `回复 ${actor.name || '我'}……`;
      $$('#roleSheet [data-reply-actor]').forEach(item => {
        const selected = item.dataset.replyActor === actor.id;
        item.classList.toggle('selected', selected);
        item.querySelector('.check')?.classList.toggle('checked', selected);
      });
      window.closeRoleSheet();
    });
    roleSheetBody.dataset.replyBinding = 'true';
  }
  const legacyRoleGrid = $('#sheet .role-grid');
  if (legacyRoleGrid) legacyRoleGrid.innerHTML = (conversation.participants || []).filter(actor => !actor.is_player_controlled).map((actor, index) => `<button class="role-card selected" data-actor="${h(actor.id)}"><em>${index + 1}</em><div class="avatar">${h(actor.name.slice(0, 1))}</div><div><b>${h(actor.name)}</b><small>${h(actor.summary)}</small></div><span class="order-controls"><i data-move="up">↑</i><i data-move="down">↓</i></span></button>`).join('');
  if (followChatBottom) $('#content').scrollTop = $('#content').scrollHeight;
}

async function loadChat(id) {
  state.current = state.readOnly
    ? await api(`/api/admin/users/${path(state.adminUser.id)}/conversations/${path(id)}/`)
    : await api(`/api/conversations/${path(id)}/`);
  const participants = state.current.participants || [];
  if (!participants.some(actor => actor.id === state.replyActorId)) state.replyActorId = state.current.player_character_id || participants[0]?.id || null;
  original.openChat(state.current.title);
  renderChat(true);
  if (state.readOnly) $('#chatComposer').style.display = 'none';
}

window.openChat = title => {
  const item = state.conversations.find(conversation => conversation.title === title);
  if (item) loadChat(item.id).catch(fail);
};
function closeSettingsPanels() {
  $$('.settings-panel.open').forEach(panel => panel.classList.remove('open'));
  clearInterval(state.usagePolling);
  state.usagePolling = null;
  document.body.classList.remove('worldbook-editor-open');
  worldBookEditingId = null;
}

window.showConversations = () => { closeSettingsPanels(); state.current = null; state.replyActorId = null; original.showConversations(); $('.header .title').textContent = '霓虹酒馆'; $('.env span').innerHTML = '<i class="dot"></i>我的故事'; refreshLists().catch(fail); };
window.closeChat = window.showConversations;
window.openPanel = id => { closeSettingsPanels();
  if (id === 'smartImport' && state.readOnly) return window.toast('管理员只读查看中，不能使用智能导入');
  original.showConversations();
  if (id === 'characters' || id === 'materials' || id === 'account' || id === 'smartImport') original.openPanel(id);
  if (id === 'materials') loadMarket();
  if (id === 'account') renderAccount();
};

window.openNewConversation = () => {
  if (state.readOnly) return window.toast('管理员只读查看中，不能新建对话');
  state.editing = null;
  renderPicker();
  $$('#newEnvironment input, #newEnvironment textarea').forEach(field => { field.value = ''; });
  $('#newPlayer').disabled = false;
  $('#deleteConversation').hidden = true;
  original.openNewConversation();
};
window.startNewConversation = async () => {
  const player = state.characters.find(character => character.id === $('#newPlayer').value);
  const ids = $$('#conversationRoleScroll .create-role.selected').map(element => element.dataset.id);
  if (!player || !ids.length) { window.toast('请先选择玩家角色和至少一名 NPC'); return; }
  const fields = $$('#newEnvironment input:not(#newTitle), #newEnvironment textarea');
  try {
    const body = {
      title: $('#newTitle')?.value.trim() || fields[0].value.trim() || '新故事',
      character_ids: ids,
      environment: { 地点: fields[0].value, 时间: fields[1].value, 环境描写: fields[2].value },
      auto_generate: $('#newConversation [data-auto]')?.checked || false,
      auto_actor_ids: ids, auto_mode: 'serial',
    };
    if (!state.editing) body.player_character_id = player.id;
    const conversation = await api(state.editing ? `/api/conversations/${path(state.editing.id)}/` : '/api/conversations/', { method: state.editing ? 'PATCH' : 'POST', body });
    state.editing = null;
    original.closeNewConversation(); await refreshLists(); await loadChat(conversation.id);
  } catch (error) { fail(error); }
};

window.sendMessage = async () => {
  const input = $('#messageInput'); const content = input.value.trim();
  if (!content || !state.current) return;
  const participants = state.current.participants || [];
  const player = participants.find(actor => actor.id === state.current.player_character_id);
  const replyActor = participants.find(actor => actor.id === state.replyActorId) || player;
  const isOoc = $('#oocToggle').classList.contains('on');
  if (isOoc && replyActor && !replyActor.is_player_controlled) return window.toast('场外导演消息只能由玩家卡发送');
  const speaker = isOoc ? player : replyActor;
  if (!speaker) return window.toast('当前对话没有可用的回复角色');
  try {
    const result = await api(`/api/conversations/${path(state.current.id)}/messages/`, { method: 'POST', body: {
      content, kind: isOoc ? 'ooc' : 'dialogue', speaker_id: speaker.id,
    } });
    input.value = ''; state.current = await api(`/api/conversations/${path(state.current.id)}/`); renderChat();
    if (result.job_id) pollJob(result.job_id);
    if (result.warning) window.toast(result.warning);
  } catch (error) { fail(error); }
};
window.saveDirectorInstruction = () => {
  const content = $('#oocInput').value.trim();
  if (!content) { window.toast('请填写导演指令'); return; }
  $('#messageInput').value = content;
  if (!$('#oocToggle').classList.contains('on')) window.toggleOoc();
  window.closeOocSheet();
  $('#messageInput').focus();
};

async function pollJob(id, attempts = 0) {
  clearTimeout(state.polling);
  if (attempts >= 120) { window.toast('生成仍在进行，请稍后刷新对话'); return; }
  const job = await api(`/api/generation-jobs/${path(id)}/`);
  const previousJob = state.lastJob?.id === job.id ? state.lastJob : null;
  const completedCount = Object.values(job.progress || {}).filter(item => ['done', 'failed'].includes(item.status)).length;
  const previousCompletedCount = Object.values(previousJob?.progress || {}).filter(item => ['done', 'failed'].includes(item.status)).length;
  state.lastJob = job;
  const shouldRefreshConversation = completedCount > previousCompletedCount;
  if (state.current?.id === job.conversation_id && shouldRefreshConversation) {
    state.current = await api(`/api/conversations/${path(job.conversation_id)}/`);
  }
  if (state.current?.id === job.conversation_id) renderChat();
  if (job.status === 'done' || job.status === 'completed' || job.status === 'failed') {
    if (state.current?.id === job.conversation_id) {
      state.current = await api(`/api/conversations/${path(job.conversation_id)}/`);
      renderChat();
    }
    if (job.status === 'failed') window.toast(job.error || '生成失败，已保留已生成内容');
  } else {
    if (state.current?.id === job.conversation_id) $('#chatView .waiting:last-child span').textContent = job.status === 'running' ? '角色正在回复…' : '生成任务排队中…';
    state.polling = setTimeout(() => pollJob(id, attempts + 1).catch(fail), 800);
  }
}

window.openSheet = () => {
  if (!state.current) return;
  const generationOverlay = $('#sheet');
  const generationSheet = generationOverlay?.querySelector('.generation-sheet') || generationOverlay?.querySelector('.sheet');
  if (!generationOverlay || !generationSheet) {
    window.toast('生成面板加载失败，请刷新页面后重试');
    return;
  }
  const actors = (state.current.participants || []).filter(actor => !actor.is_player_controlled);
  const roleCards = actors.map((actor, index) => `<div class="generation-role-card selected" data-actor="${h(actor.id)}"><button type="button" class="generation-role-toggle" data-role-toggle aria-pressed="true"><span class="role-order">${index + 1}</span><span class="avatar">${h(actor.name.slice(0, 1))}</span><span class="generation-role-copy"><b>${h(actor.name)}</b><small>${h(actor.summary || 'NPC')}</small></span><span class="role-participation">参与</span></button><span class="order-controls"><button type="button" data-move="up" aria-label="${h(actor.name)}上移">↑</button><button type="button" data-move="down" aria-label="${h(actor.name)}下移">↓</button></span></div>`).join('');
  generationSheet.innerHTML = `<div class="handle"></div><h2>生成回应</h2><div class="setting-card"><div class="setting-head"><b>回复角色选择</b><span>本轮</span></div><div class="setting-row"><select id="genMode"><option value="manual">手动指定角色与顺序</option><option value="auto">自动 · Router Agent</option></select><button type="button" id="suggestActors" class="setting-option">让 Router 安排行动队列</button></div><p class="sheet-desc">手动模式：点击“参与”即可取消，再用上下箭头调整顺序。自动模式：Router 先安排队列，你仍可取消角色或调整顺序。</p></div><div class="setting-card role-card-section"><div class="setting-head"><b>本轮角色队列</b><span>按显示顺序生成</span></div><div id="generationRoleGrid" class="generation-role-grid">${roleCards || '<div class="live-empty">当前没有可生成的 NPC</div>'}</div></div><div class="sheet-buttons generation-actions"><button type="button" data-clear-generation>全部取消</button><button type="button" data-generation-cancel>关闭</button><button type="button" class="primary" onclick="startGeneration()">开始生成</button></div>`;
  const roleGrid = generationSheet.querySelector('#generationRoleGrid');
  if (!roleGrid) {
    window.toast('生成角色列表加载失败，请刷新页面后重试');
    return;
  }
  roleGrid.addEventListener('click', event => {
    const move = event.target.closest('[data-move]');
    if (move) {
      event.preventDefault();
      const actor = move.closest('[data-actor]');
      const sibling = move.dataset.move === 'up' ? actor.previousElementSibling : actor.nextElementSibling;
      if (sibling) roleGrid.insertBefore(move.dataset.move === 'up' ? actor : sibling, move.dataset.move === 'up' ? sibling : actor);
      updateActorOrder();
      return;
    }
    const toggle = event.target.closest('[data-role-toggle]');
    if (toggle) {
      event.preventDefault();
      toggle.closest('[data-actor]').classList.toggle('selected');
      updateActorOrder();
    }
  });
  $('#sheet [data-clear-generation]').addEventListener('click', () => {
    $$('#sheet [data-actor]').forEach(card => card.classList.remove('selected'));
    updateActorOrder();
  });
  $('#sheet [data-generation-cancel]').addEventListener('click', () => window.closeSheet());
  $('#suggestActors').addEventListener('click', async () => {
    try {
      const result = await api(`/api/conversations/${path(state.current.id)}/suggest/`, { method: 'POST', body: {} });
      const cards = new Map($$('#sheet [data-actor]').map(card => [card.dataset.actor, card]));
      result.actor_ids.forEach(id => { const card = cards.get(id); if (card) { card.classList.add('selected'); $('#generationRoleGrid').append(card); } });
      cards.forEach((card, id) => { if (!result.actor_ids.includes(id)) card.classList.remove('selected'); });
      cards.forEach((card, id) => { if (!result.actor_ids.includes(id)) $('#generationRoleGrid').append(card); });
      updateActorOrder();
    } catch (error) { fail(error); }
  });
  original.openSheet();
};
window.startGeneration = async () => {
  const mode = $('#genMode').value;
  let actor_ids = $$('#sheet [data-actor].selected').map(element => element.dataset.actor);
  if (mode === 'manual' && !actor_ids.length) { window.toast('请至少保留一名 NPC'); return; }
  try {
    let director_hints = {};
    if (mode === 'auto') {
      const routed = await api(`/api/conversations/${path(state.current.id)}/suggest/`, { method: 'POST', body: { force_router: true } });
      actor_ids = routed.actor_ids;
      director_hints = routed.director_hints || {};
    }
    if (!actor_ids.length) { window.toast('Router 没有选择回复角色'); return; }
    const job = await api(`/api/conversations/${path(state.current.id)}/generate/`, { method: 'POST', body: { actor_ids, mode: 'serial', director_hints } });
    window.closeSheet(); window.toast('正在生成'); pollJob(job.job_id).catch(fail);
  } catch (error) { fail(error); }
};

function updateActorOrder() {
  let position = 0;
  $$('#sheet [data-actor]').forEach(card => {
    const selected = card.classList.contains('selected');
    const badge = card.querySelector('.role-order');
    const toggle = card.querySelector('[data-role-toggle]');
    const participation = card.querySelector('.role-participation');
    if (badge) badge.textContent = selected ? ++position : '–';
    if (toggle) toggle.setAttribute('aria-pressed', String(selected));
    if (participation) participation.textContent = selected ? '参与' : '不参与';
  });
}

let editingCharacter = null;
window.openRoleCreator = () => {
  editingCharacter = null;
  $$('#createRolePage input:not([type="checkbox"]), #createRolePage textarea').forEach(field => { field.value = ''; });
  $('#isUserRole').checked = false;
  $('#customStateFields').innerHTML = '';
  $('#roleAffinity').value = 0; $('#roleAffinityValue').textContent = '0%';
  const flatten = nodes => nodes.flatMap(item => [item, ...flatten(item.children || [])]);
  $('#roleCategories').innerHTML = flatten(state.characterCategories).map(item => `<option value="${h(item.id)}">${h(item.name)}</option>`).join('');
  $('#deleteCharacter').hidden = true;
  original.openRoleCreator();
};
function editCharacter(id) {
  if (state.readOnly) return;
  const actor = state.characters.find(character => character.id === id);
  if (!actor) return;
  window.openRoleCreator(); editingCharacter = actor;
  $('#deleteCharacter').hidden = false;
  const fields = $$('#createRolePage .role-field input:not([type="checkbox"]):not([type="range"]), #createRolePage .role-field textarea');
  fields[0].value = actor.name; fields[1].value = actor.summary; fields[2].value = actor.personality;
  fields[3].value = actor.relationship_notes?.关系 || ''; fields[4].value = actor.relationship_notes?.称呼 || '';
  fields[5].value = actor.relationship_notes?.补充 || '';
  fields[6].value = actor.clothing_type || ''; fields[7].value = actor.clothing_state || '';
  fields[8].value = actor.state_fields?.当前状态 || '';
  fields[9].value = actor.speech_habits; fields[10].value = actor.memories;
  $('#isUserRole').checked = actor.is_player_controlled;
  $('#roleAffinity').value = actor.affinity ?? 0; $('#roleAffinityValue').textContent = `${actor.affinity ?? 0}%`;
  [...$('#roleCategories').options].forEach(option => { option.selected = (actor.category_ids || []).includes(option.value); });
  for (const [key, value] of Object.entries(actor.state_fields || {})) {
    if (['服装', '服装状态', '当前状态'].includes(key)) continue;
    window.addRoleStateField();
    const row = $('#customStateFields .custom-state-row:last-child');
    row.querySelectorAll('input')[0].value = key; row.querySelectorAll('input')[1].value = String(value);
  }
}
window.saveRoleCard = async () => {
  const fields = $$('#createRolePage .role-field input:not([type="checkbox"]):not([type="range"]), #createRolePage .role-field textarea');
  const state_fields = { 当前状态: fields[8].value };
  $$('#customStateFields .custom-state-row').forEach(row => { const [key, value] = [...row.querySelectorAll('input')].map(input => input.value.trim()); if (key) state_fields[key] = value; });
  const body = { name: fields[0].value.trim(), summary: fields[1].value, personality: fields[2].value,
    relationship_notes: { 关系: fields[3].value, 称呼: fields[4].value, 补充: fields[5].value },
    state_fields, speech_habits: fields[9].value, memories: fields[10].value,
    is_player_controlled: $('#isUserRole').checked,
    affinity: Number($('#roleAffinity').value), clothing_type: $('#roleClothingType').value, clothing_state: $('#roleClothingState').value,
    category_ids: [...$('#roleCategories').selectedOptions].map(option => option.value) };
  try {
    const saved = await api(editingCharacter ? `/api/characters/${path(editingCharacter.id)}/` : '/api/characters/', { method: editingCharacter ? 'PATCH' : 'POST', body });
    const avatar = $('#roleAvatarInput')?.files?.[0];
    if (avatar) { const form = new FormData(); form.append('avatar', avatar); await api(`/api/characters/${path(saved.id)}/avatar/`, { method: 'POST', body: form }); }
    window.closeRoleCreator(); await refreshLists(); window.toast('角色卡已保存');
  } catch (error) { fail(error); }
};

function renderAccount() {
  $('#account .card .card-head b').textContent = state.me.username;
  $('#account .card .avatar').textContent = state.me.username.slice(0, 1);
  const accountRole = $('#accountRole');
  if (accountRole) accountRole.textContent = state.me.is_admin ? '网站管理员账号' : '普通用户账号';
  const status = $('#account .create-line:nth-child(2) b'); if (status) status.textContent = '云端保存';
  if (!state.readOnly && !state.adminUser) loadAccountWarnings().catch(fail);
}

async function loadAccountWarnings() {
  const panel = $('#accountWarnings');
  if (!panel) return;
  const profile = await api('/api/account/profile/');
  const warnings = profile.warnings || [];
  panel.hidden = warnings.length === 0;
  panel.innerHTML = warnings.length
    ? `<div class="account-warning-head">站点通知 <small>${warnings.length}</small></div>${warnings.map(item => `<p>${h(item.message)}</p>`).join('')}<button type="button" id="acknowledgeAccountWarnings">我已阅读</button>`
    : '';
  $('#acknowledgeAccountWarnings')?.addEventListener('click', async () => {
    await api('/api/account/warnings/read/', { method: 'POST', body: { ids: warnings.map(item => item.id) } });
    await loadAccountWarnings();
  });
}

function splitPreferenceKeywords(value) {
  const source = Array.isArray(value) ? value : String(value || '').split(/[,，;；、\n\r]+/);
  return [...new Set(source.map(item => String(item).trim()).filter(Boolean))].slice(0, 32);
}

function renderAdultPreferencePanel(enabled, keywords = []) {
  const panel = $('#adultPreferencePanel');
  if (!panel) return;
  panel.hidden = !enabled;
  const normalized = splitPreferenceKeywords(keywords);
  $('#adultPreferenceKeywords').value = normalized.join('，');
  const recorded = $('#adultPreferenceRecorded');
  recorded.innerHTML = normalized.map(item => `<span>${h(item)}</span>`).join('');
  recorded.hidden = true;
  $('#adultPreferenceView').textContent = normalized.length ? `查看已记录偏好（${normalized.length}）` : '查看已记录偏好';
}

async function loadSettings() {
  const [settings, catalog] = await Promise.all([api('/api/settings/'), api('/api/model-providers/')]);
  state.settings = settings;
  state.providers = catalog.providers || [];
  const values = state.settings.effective || {};
  $('#modelProvider').innerHTML = state.providers.map(provider => `<option value="${h(provider.id)}">${h(provider.name)}</option>`).join('');
  $('#modelProvider').value = values.provider_id || 'deepseek';
  applyProviderPreset(values.model, values.api_base_url);
  $('#modelApiKey').value = '';
  $('#modelApiKey').placeholder = state.settings.has_api_key ? '已配置（留空保持不变）' : '填写密钥';
  $('#modelName').value = values.model || 'deepseek-chat';
  const model = $$('#modelDetail .setting-row input');
  model[0].value = values.temperature ?? '';
  model[1].value = values.top_p ?? '';
  model[2].value = values.max_tokens ?? '';
  const switches = $$('#modelSettings .switch-row input');
  switches[0].checked = !!values.stream_output;
  switches[1].checked = !!values.save_raw_response;
  $('#globalSettings .global-prompt').value = values.global_prompt || '';
  $$('#globalSettings .toggle-row .switch').forEach((button, index) => {
    button.classList.toggle('on', !!values[['adult_content_preference', 'strict_persona', 'auto_state_extraction'][index]]);
  });
  renderAdultPreferencePanel(!!values.adult_content_preference, values.adult_content_keywords || []);
  $('#globalStreamOutput').classList.toggle('on', !!values.stream_output);
}

function applyProviderPreset(selectedModel = '', savedBaseUrl = '') {
  const provider = state.providers.find(item => item.id === $('#modelProvider').value) || state.providers[0];
  if (!provider) return;
  const custom = provider.id === 'custom';
  $('#modelBaseUrl').readOnly = !custom;
  $('#modelBaseUrl').value = custom ? savedBaseUrl : provider.base_url;
  $('#providerApplyLink').hidden = !provider.apply_url;
  $('#providerApplyLink').href = provider.apply_url || '#';
  const models = [...new Set([...(provider.models || []), selectedModel].filter(Boolean))];
  $('#modelName').innerHTML = models.map(model => `<option value="${h(model)}">${h(model)}</option>`).join('') + '<option value="__custom__">手动添加模型…</option>';
  $('#modelName').value = models.includes(selectedModel) ? selectedModel : (models[0] || '__custom__');
  $('#customModelName').hidden = $('#modelName').value !== '__custom__';
}

window.openSettings = id => {
  if (state.readOnly && id !== 'worldBookSettings') return window.toast('管理员只读查看中，设置不可修改');
  closeSettingsPanels();
  if (id === 'modelSettings' || id === 'globalSettings') loadSettings().catch(fail);
  if (id === 'worldBookSettings') loadWorldBooks().catch(fail);
  $(`#${id}`).classList.add('open');
  if (id === 'modelSettings') {
    refreshProviderUsage().catch(fail);
    clearInterval(state.usagePolling);
    state.usagePolling = setInterval(() => { if ($('#modelSettings').classList.contains('open') && !document.hidden) refreshProviderUsage().catch(() => {}); }, 300000);
  }
};

window.closeSettings = id => {
  original.closeSettings(id);
  if (id === 'modelSettings') { clearInterval(state.usagePolling); state.usagePolling = null; }
};

async function refreshProviderUsage(force = false) {
  const result = await api(`/api/settings/usage/${force ? '?refresh=1' : ''}`);
  const value = result.balance == null ? '' : `${result.balance} ${result.currency || ''}`.trim();
  $('#modelUsageResult').textContent = result.supported ? (value ? `当前余额：${value}${result.used != null ? `；已用：${result.used}` : ''}` : result.reason || '服务商未返回余额') : result.reason;
  $('#modelUsageChecked').textContent = `查询时间 ${new Date(result.checked_at * 1000).toLocaleTimeString()}`;
}

function worldBookModeLabel(mode) {
  return { keyword: '关键词触发', always: '始终启用', manual: '手动启用' }[mode] || mode;
}

function worldBookEntryClass(mode) {
  return { keyword: 'keyword', always: 'always', manual: 'manual' }[mode] || 'default';
}

const worldBookPositionLabel = value => ({ before_character: '角色卡之前', after_character: '角色卡之后', before_recent_messages: '最近对话之前' }[value] || value);
const worldBookScopeLabel = value => ({ global: '所有对话', character: '指定角色', conversation: '指定对话' }[value] || value);

async function loadWorldBooks(preferredId = null) {
  const result = await api(state.readOnly ? `/api/admin/users/${path(state.adminUser.id)}/worldbooks/` : '/api/worldbooks/');
  worldBookState.books = listFrom(result, 'worldbooks');
  const selected = preferredId || worldBookState.current?.id || worldBookState.books[0]?.id;
  $('#worldBookSelect').innerHTML = worldBookState.books.map(item => `<option value="${h(item.id)}">${h(item.name)}</option>`).join('');
  if (!selected) {
    worldBookState.current = null; worldBookState.entries = []; worldBookState.categories = [];
    $('#worldBookDelete').hidden = true;
    renderWorldBooks(); return;
  }
  $('#worldBookSelect').value = selected;
  const detail = state.readOnly ? worldBookState.books.find(item => item.id === selected) : await api(`/api/worldbooks/${path(selected)}/`);
  worldBookState.current = detail;
  worldBookState.entries = detail.entries || [];
  worldBookState.categories = detail.categories || [];
  $('#worldBookCreate').hidden = state.readOnly;
  $('#worldBookDelete').hidden = state.readOnly;
  $('#worldBookManage').hidden = state.readOnly;
  $('#worldBookImport').hidden = state.readOnly;
  $('#worldBookExport').hidden = state.readOnly;
  $('#worldBookAdd').hidden = state.readOnly;
  renderWorldBooks();
}

function categoryDepth(category, byId) {
  let depth = 0, node = category, seen = new Set();
  while (node.parent_id && !seen.has(node.parent_id)) { seen.add(node.parent_id); node = byId.get(node.parent_id); if (!node) break; depth += 1; }
  return depth;
}

function renderWorldBookTree() {
  const byId = new Map(worldBookState.categories.map(item => [item.id, item]));
  const children = new Map();
  worldBookState.categories.forEach(item => { const list = children.get(item.parent_id) || []; list.push(item); children.set(item.parent_id, list); });
  const entryMarkup = entry => `${worldBookState.manage ? `<input type="checkbox" data-worldbook-select="${h(entry.id)}" ${worldBookState.selected.has(entry.id) ? 'checked' : ''}>` : ''}<button type="button" class="worldbook-folder-entry worldbook-entry-${worldBookEntryClass(entry.trigger_mode)}${entry.enabled ? '' : ' worldbook-entry-disabled'}" data-worldbook-edit="${h(entry.id)}"><span class="worldbook-entry-mark" aria-hidden="true"></span><span class="worldbook-entry-copy"><b>${h(entry.name)}</b><small>${entry.enabled ? '已启用' : '已停用'}</small></span><span class="worldbook-entry-kind">${h(worldBookModeLabel(entry.trigger_mode))}</span></button>`;
  const render = parentId => (children.get(parentId) || []).sort((a, b) => a.position - b.position || a.name.localeCompare(b.name)).map(item => {
    const categoryEntries = worldBookState.entries.filter(entry => (entry.category_ids || []).includes(item.id));
    const entries = categoryEntries.map(entryMarkup).join('');
    const categoryLevel = parentId === null ? 'root' : 'nested';
    const deleteLabel = parentId === null ? '删除顶级分类' : '删除分类';
    return `<details class="worldbook-category" data-category-level="${categoryLevel}"><summary><b>${h(item.name)}</b><small class="category-count">${categoryEntries.length} 个条目</small><span class="category-actions">${state.readOnly ? '' : `<button type="button" data-category-add="${h(item.id)}">＋</button><button type="button" data-category-edit="${h(item.id)}">编辑</button><button type="button" class="danger ${parentId === null ? 'root-category-delete' : ''}" aria-label="${deleteLabel}" data-category-delete="${h(item.id)}">删除</button>`}</span></summary>${entries}${render(item.id)}</details>`;
  }).join('');
  const rows = [...worldBookState.categories].sort((a, b) => categoryDepth(a, byId) - categoryDepth(b, byId) || a.position - b.position || a.name.localeCompare(b.name));
  const uncategorized = worldBookState.entries.filter(entry => !(entry.category_ids || []).length).map(entryMarkup).join('');
  $('#worldBookTree').innerHTML = `${state.readOnly ? '' : '<div class="worldbook-category"><button type="button" data-category-add-root>＋ 新建分类</button></div>'}${render(null)}${uncategorized ? `<details class="worldbook-category"><summary><b>未分类条目</b><small class="category-count">${worldBookState.entries.filter(entry => !(entry.category_ids || []).length).length} 个条目</small></summary>${uncategorized}</details>` : ''}`;
  $('#worldBookCategories').innerHTML = rows.map(item => `<option value="${h(item.id)}">${'　'.repeat(categoryDepth(item, byId))}${h(item.name)}</option>`).join('');
}

function renderWorldBooks() {
  const query = $('#worldBookSearch').value.trim().toLowerCase();
  const filter = $('#worldBookFilter').value;
  const rows = worldBookState.entries.filter(entry => {
    const matchesQuery = !query || `${entry.name} ${(entry.keywords || []).join(' ')}`.toLowerCase().includes(query);
    const matchesFilter = filter === 'all' || (filter === 'enabled' ? entry.enabled : entry.trigger_mode === filter);
    return matchesQuery && matchesFilter;
  });
  $('#worldBookTotal').textContent = worldBookState.entries.length;
  $('#worldBookEnabled').textContent = worldBookState.entries.filter(entry => entry.enabled).length;
  $('#worldBookKeyword').textContent = worldBookState.entries.filter(entry => entry.trigger_mode === 'keyword').length;
  $('#worldBookList').innerHTML = rows.map(entry => `<div class="worldbook-row ${entry.enabled ? '' : 'off'} ${worldBookState.manage ? 'manage' : ''}">${worldBookState.manage ? `<input type="checkbox" data-worldbook-select="${h(entry.id)}" ${worldBookState.selected.has(entry.id) ? 'checked' : ''}>` : ''}<button type="button" data-worldbook-edit="${h(entry.id)}"><h3>${h(entry.name)}</h3><p>${h(entry.content.slice(0, 58))}${entry.content.length > 58 ? '…' : ''}</p><div class="worldbook-tags"><span>${h(worldBookModeLabel(entry.trigger_mode))}</span><span>优先级 ${entry.priority}</span><span>${h(worldBookPositionLabel(entry.insertion_position))}</span><span>${h(worldBookScopeLabel(entry.scope_type))}</span></div></button>${worldBookState.manage ? '' : `<button type="button" class="worldbook-toggle ${entry.enabled ? 'on' : ''}" data-worldbook-toggle="${h(entry.id)}" aria-label="${entry.enabled ? '停用' : '启用'}${h(entry.name)}"><i></i></button>`}</div>`).join('') || '<div class="live-empty">没有符合条件的条目</div>';
  $('#worldBookSelectionToolbar').hidden = !worldBookState.manage;
  $('#worldBookSelectionCount').textContent = `已选 ${worldBookState.selected.size} 项`;
  renderWorldBookTree();
}

function openWorldBookEditor(id = null) {
  if (!worldBookState.current) return window.toast('请先新建世界书');
  worldBookEditingId = id;
  const entry = worldBookState.entries.find(item => item.id === id) || { name: '', keywords: [], content: '', trigger_mode: 'keyword', insertion_position: 'before_character', priority: 100, scope_type: 'global', enabled: true, category_ids: [], scoped_character_ids: [], scoped_conversation_ids: [] };
  $('#worldBookEditorTitle').textContent = id ? '编辑条目' : '新建条目';
  $('#worldBookName').value = entry.name;
  $('#worldBookKeys').value = (entry.keywords || []).join('，');
  $('#worldBookContent').value = entry.content;
  $('#worldBookMode').value = entry.trigger_mode;
  $('#worldBookPosition').value = entry.insertion_position;
  $('#worldBookPriority').value = entry.priority;
  $('#worldBookScope').value = entry.scope_type;
  [...$('#worldBookCategories').options].forEach(option => { option.selected = (entry.category_ids || []).includes(option.value); });
  const targetIds = entry.scope_type === 'character' ? entry.scoped_character_ids : entry.scoped_conversation_ids;
  updateWorldBookTargetOptions(targetIds?.[0] || '');
  $('#worldBookEntryEnabled').classList.toggle('on', entry.enabled);
  $('#worldBookRemove').hidden = !id;
  document.body.classList.add('worldbook-editor-open');
  $('#worldBookEditor').classList.add('open');
}

function updateWorldBookTargetOptions(selectedId = '') {
  const scope = $('#worldBookScope').value;
  const field = $('#worldBookTargetField');
  const select = $('#worldBookTarget');
  if (scope === 'global') {
    field.hidden = true;
    select.innerHTML = '';
    return;
  }
  field.hidden = false;
  const source = scope === 'character' ? state.characters : state.conversations;
  $('#worldBookTargetLabel').textContent = scope === 'character' ? '选择角色' : '选择对话';
  select.innerHTML = source.length
    ? `<option value="">请选择</option>${source.map(item => `<option value="${h(item.id)}">${h(item.name || item.title)}</option>`).join('')}`
    : '<option value="">暂无可选项</option>';
  select.value = selectedId;
}

function closeWorldBookEditor() {
  $('#worldBookEditor').classList.remove('open');
  document.body.classList.remove('worldbook-editor-open');
  worldBookEditingId = null;
}

async function saveWorldBookEntry() {
  const name = $('#worldBookName').value.trim();
  if (!name) return window.toast('请填写条目名称');
  const scope = $('#worldBookScope').value;
  const targetId = scope === 'global' ? '' : $('#worldBookTarget').value;
  if (scope !== 'global' && !targetId) return window.toast(`请选择${scope === 'character' ? '角色' : '对话'}`);
  const body = {
    name,
    keywords: splitWorldBookKeywords($('#worldBookKeys').value),
    content: $('#worldBookContent').value.trim(), trigger_mode: $('#worldBookMode').value,
    insertion_position: $('#worldBookPosition').value, priority: Number($('#worldBookPriority').value) || 0,
    scope_type: scope, category_ids: [...$('#worldBookCategories').selectedOptions].map(option => option.value),
    scoped_character_ids: scope === 'character' ? [targetId] : [], scoped_conversation_ids: scope === 'conversation' ? [targetId] : [],
    enabled: $('#worldBookEntryEnabled').classList.contains('on'),
  };
  const url = worldBookEditingId ? `/api/worldbooks/${path(worldBookState.current.id)}/entries/${path(worldBookEditingId)}/` : `/api/worldbooks/${path(worldBookState.current.id)}/entries/`;
  await api(url, { method: worldBookEditingId ? 'PATCH' : 'POST', body });
  closeWorldBookEditor(); await loadWorldBooks(worldBookState.current.id); window.toast('世界书条目已保存');
}

async function renderWorldBookPreview() {
  const text = $('#worldBookPreviewInput').value.trim();
  if (!text) { $('#worldBookPreviewResult').textContent = '输入内容后查看会触发哪些条目。'; return; }
  const body = { text };
  if (state.current) body.conversation_id = state.current.id;
  const result = await api('/api/worldbooks/preview/', { method: 'POST', body });
  const matched = listFrom(result, 'entries');
  $('#worldBookPreviewResult').innerHTML = matched.length ? `将注入：${matched.map(entry => `<b>${h(entry.name)}</b>`).join('、')}` : '当前没有条目被触发。';
}
function modelSettingsBody() {
  const fields = $$('#modelDetail .setting-row input');
  const model = $('#modelName').value === '__custom__' ? $('#customModelName').value.trim() : $('#modelName').value;
  const body = { provider_id: $('#modelProvider').value, api_base_url: $('#modelBaseUrl').value.trim(), model,
    temperature: Number(fields[0].value), top_p: Number(fields[1].value), max_tokens: Number(fields[2].value),
    stream_output: $('#modelSettings .switch-row input').checked,
    save_raw_response: $$('#modelSettings .switch-row input')[1].checked };
  if ($('#modelApiKey').value) body.api_key = $('#modelApiKey').value;
  return body;
}
window.saveSettings = async id => {
  try {
    let body;
    if (id === 'modelSettings') {
      body = modelSettingsBody();
    } else {
      const switches = $$('#globalSettings .toggle-row .switch');
      body = { global_prompt: $('#globalSettings .global-prompt').value,
        adult_content_preference: switches[0].classList.contains('on'),
        adult_content_keywords: splitPreferenceKeywords($('#adultPreferenceKeywords').value),
        strict_persona: switches[1].classList.contains('on'),
        auto_state_extraction: switches[2].classList.contains('on'),
        stream_output: $('#globalStreamOutput').classList.contains('on') };
    }
    state.settings = await api('/api/settings/', { method: 'PUT', body });
    window.closeSettings(id); window.toast('设置已保存');
  } catch (error) { fail(error); }
};

$('#testModelConnection').addEventListener('click', async () => {
  const status = $('#modelStatus'); status.className = 'model-status'; status.textContent = '正在测试连接…';
  try {
    await api('/api/settings/', { method: 'PUT', body: modelSettingsBody() });
    const result = await api('/api/settings/test-connection/', { method: 'POST', body: {} });
    status.classList.add(result.ok ? 'success' : 'error');
    status.textContent = result.reason || (result.ok ? '连接成功，模型可以正常响应' : '连接失败');
  } catch (error) { status.classList.add('error'); status.textContent = `连接失败：${error.message}`; }
});
$('#discoverModels').addEventListener('click', async () => {
  const status = $('#modelStatus'); status.textContent = '正在读取可用模型…';
  try {
    await api('/api/settings/', { method: 'PUT', body: modelSettingsBody() });
    const result = await api('/api/settings/models/', { method: 'POST', body: {} });
    const current = $('#modelName').value;
    const models = [...new Set(result.models)];
    $('#modelName').innerHTML = models.map(model => `<option value="${h(model)}">${h(model)}</option>`).join('') + '<option value="__custom__">手动添加模型…</option>';
    if (models.length) $('#modelName').value = models.includes(current) ? current : models[0];
    status.textContent = result.models.length ? `已读取 ${result.models.length} 个模型，可在上方选择` : '接口没有返回可用模型，可手动填写';
  } catch (error) { status.textContent = error.message; }
});

$('#modelProvider').addEventListener('change', () => {
  applyProviderPreset();
  $('#modelStatus').textContent = '';
});
$('#modelName').addEventListener('change', () => {
  $('#customModelName').hidden = $('#modelName').value !== '__custom__';
  if (!$('#customModelName').hidden) $('#customModelName').focus();
});
$('#refreshModelUsage').addEventListener('click', () => refreshProviderUsage(true).catch(fail));
$('#roleAffinity').addEventListener('input', event => { $('#roleAffinityValue').textContent = `${event.target.value}%`; });
$('#characterCategoryManage').addEventListener('click', async () => {
  const name = window.prompt('新建角色分类名称'); if (!name?.trim()) return;
  await api('/api/character-categories/', { method: 'POST', body: { name: name.trim() } }); await refreshLists();
});
$('#characterImport').addEventListener('click', () => $('#characterImportFile').click());
$('#characterImportFile').addEventListener('change', async event => {
  const file = event.target.files?.[0]; if (!file) return;
  try {
    const isPng = file.type === 'image/png' || /\.png$/i.test(file.name || '');
    const body = isPng ? (() => { const form = new FormData(); form.append('file', file); return form; })() : { payload: await readJsonFile(file) };
    const preview = await api('/api/characters/import/preview/', { method: 'POST', body });
    const names = preview.characters.map(item => `${item.name}${item.conflict ? '（同名）' : ''}`).join('、');
    if (window.confirm(`识别到：${names}\n确认导入吗？`)) { await api('/api/characters/import/commit/', { method: 'POST', body: preview }); await refreshLists(); window.toast('角色卡已导入'); }
  } catch (error) { fail(error); } finally { event.target.value = ''; }
});

$('#logout').addEventListener('click', async () => {
  try { await api('/api/auth/logout/', { method: 'POST', body: {} }); location.assign('/login/'); } catch (error) { fail(error); }
});
$('#changePassword').addEventListener('click', () => window.openSettings('accountSecurity'));
$('#savePassword').addEventListener('click', async () => {
  const old_password = $('#oldPassword').value;
  const new_password = $('#newPassword').value;
  if (!new_password || new_password !== $('#confirmPassword').value) { window.toast('请确认新密码'); return; }
  try {
    await api('/api/account/change-password/', { method: 'POST', body: { old_password, new_password } });
    window.closeSettings('accountSecurity');
    $('#oldPassword').value = $('#newPassword').value = $('#confirmPassword').value = '';
    window.toast('密码已更新');
  } catch (error) { fail(error); }
});

$$('#globalSettings .toggle-row .switch').forEach(button => button.addEventListener('click', () => {
  button.classList.toggle('on');
  if (button.id === 'adultContentPreference') renderAdultPreferencePanel(button.classList.contains('on'), splitPreferenceKeywords($('#adultPreferenceKeywords').value));
}));
$('#adultPreferenceView').addEventListener('click', event => {
  const recorded = $('#adultPreferenceRecorded');
  recorded.hidden = !recorded.hidden;
  event.currentTarget.textContent = recorded.hidden ? `查看已记录偏好${recorded.children.length ? `（${recorded.children.length}）` : ''}` : '收起已记录偏好';
});
$$('[data-adult-example]').forEach(button => button.addEventListener('click', () => {
  const input = $('#adultPreferenceKeywords');
  const values = splitPreferenceKeywords(input.value);
  const example = button.dataset.adultExample;
  if (!values.includes(example)) values.push(example);
  input.value = values.slice(0, 32).join('，');
}));

document.addEventListener('click', event => {
  if (event.target.closest('[data-generation-cancel]')) { event.preventDefault(); event.stopPropagation(); window.closeSheet(); return; }
  if (event.target.closest('[data-clear-generation]')) { $$('#sheet [data-actor]').forEach(card => card.classList.remove('selected')); updateActorOrder(); return; }
  const cancelJob = event.target.closest('[data-cancel-job]');
  if (cancelJob) { api(`/api/generation-jobs/${path(cancelJob.dataset.cancelJob)}/cancel/`, { method: 'POST', body: {} }).then(() => { state.lastJob = null; renderChat(); window.toast('已取消本轮生成'); }).catch(fail); return; }
  const retry = event.target.closest('[data-retry-job]');
  if (retry) { api(`/api/generation-jobs/${path(retry.dataset.retryJob)}/retry/`, { method: 'POST', body: {} }).then(result => { window.toast('已重新生成'); pollJob(result.job_id); }).catch(fail); return; }
  const roleToggle = event.target.closest('#sheet [data-role-toggle]');
  if (roleToggle) {
    event.preventDefault(); event.stopPropagation();
    roleToggle.closest('[data-actor]').classList.toggle('selected');
    updateActorOrder();
    return;
  }
  const exportCharacter = event.target.closest('[data-export-character]');
  if (exportCharacter) { fetch(`/api/characters/${path(exportCharacter.dataset.exportCharacter)}/export/`, { credentials: 'same-origin' }).then(response => { if (!response.ok) throw new Error('角色卡导出失败'); return response.blob(); }).then(blob => { const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'character.json'; link.click(); URL.revokeObjectURL(link.href); }).catch(fail); return; }
  const exportCategory = event.target.closest('[data-export-character-category]');
  if (exportCategory) { fetch(`/api/character-categories/${path(exportCategory.dataset.exportCharacterCategory)}/export/`, { credentials: 'same-origin' }).then(response => { if (!response.ok) throw new Error('分类角色导出失败'); return response.blob(); }).then(blob => { const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'characters.json'; link.click(); URL.revokeObjectURL(link.href); }).catch(fail); return; }
  const charCategoryAdd = event.target.closest('[data-character-category-add]');
  if (charCategoryAdd) { const name = window.prompt('子分类名称'); if (name?.trim()) api('/api/character-categories/', { method: 'POST', body: { name: name.trim(), parent_id: charCategoryAdd.dataset.characterCategoryAdd } }).then(refreshLists).catch(fail); return; }
  const charCategoryEdit = event.target.closest('[data-character-category-edit]');
  if (charCategoryEdit) { const name = window.prompt('新的分类名称'); if (name?.trim()) api(`/api/character-categories/${path(charCategoryEdit.dataset.characterCategoryEdit)}/`, { method: 'PATCH', body: { name: name.trim() } }).then(refreshLists).catch(fail); return; }
  const charCategoryDelete = event.target.closest('[data-character-category-delete]');
  if (charCategoryDelete && window.confirm('删除分类？角色卡会保留，子分类会上移。')) { api(`/api/character-categories/${path(charCategoryDelete.dataset.characterCategoryDelete)}/`, { method: 'DELETE' }).then(refreshLists).catch(fail); return; }
  if (event.target.closest('[data-category-add-root]')) { createWorldBookCategory().catch(fail); return; }
  const worldBookEdit = event.target.closest('[data-worldbook-edit]');
  if (worldBookEdit) { openWorldBookEditor(worldBookEdit.dataset.worldbookEdit); return; }
  const worldBookToggle = event.target.closest('[data-worldbook-toggle]');
  if (worldBookToggle) {
    const entry = worldBookState.entries.find(item => item.id === worldBookToggle.dataset.worldbookToggle);
    if (entry) api(`/api/worldbooks/${path(worldBookState.current.id)}/entries/${path(entry.id)}/`, { method: 'PATCH', body: { enabled: !entry.enabled } }).then(() => loadWorldBooks(worldBookState.current.id)).catch(fail);
    return;
  }
  const categoryAdd = event.target.closest('[data-category-add]');
  if (categoryAdd) { createWorldBookCategory(categoryAdd.dataset.categoryAdd).catch(fail); return; }
  const categoryEdit = event.target.closest('[data-category-edit]');
  if (categoryEdit) { editWorldBookCategory(categoryEdit.dataset.categoryEdit).catch(fail); return; }
  const categoryDelete = event.target.closest('[data-category-delete]');
  if (categoryDelete) { event.preventDefault(); event.stopPropagation(); deleteWorldBookCategory(categoryDelete.dataset.categoryDelete).catch(fail); return; }
  const editConversation = event.target.closest('[data-edit-conversation]');
  if (editConversation) { event.stopPropagation(); editConversationById(editConversation.dataset.editConversation).catch(fail); return; }
  const removeConversation = event.target.closest('[data-delete-conversation]');
  if (removeConversation) { event.stopPropagation(); deleteConversationById(removeConversation.dataset.deleteConversation).catch(fail); return; }
  const editRole = event.target.closest('[data-edit-character]');
  if (editRole) { event.stopPropagation(); editCharacter(editRole.dataset.editCharacter); return; }
  const removeRole = event.target.closest('[data-delete-character]');
  if (removeRole) { event.stopPropagation(); deleteCharacterById(removeRole.dataset.deleteCharacter).catch(fail); return; }
  const conversation = event.target.closest('[data-conversation]');
  if (conversation) loadChat(conversation.dataset.conversation).catch(fail);
  const character = event.target.closest('[data-character]');
  if (character) editCharacter(character.dataset.character);
  const actor = event.target.closest('#sheet [data-actor]');
  const move = event.target.closest('#sheet [data-move]');
  if (move && actor) {
    event.preventDefault(); event.stopPropagation();
    const sibling = move.dataset.move === 'up' ? actor.previousElementSibling : actor.nextElementSibling;
    if (sibling) actor.parentElement.insertBefore(move.dataset.move === 'up' ? actor : sibling, move.dataset.move === 'up' ? sibling : actor);
    updateActorOrder(); return;
  }
  if (event.target.closest('[data-chat-settings]')) openConversationSettings();
  if (event.target.closest('#deleteConversation')) deleteConversation().catch(fail);
  if (event.target.closest('#deleteCharacter')) deleteCharacter().catch(fail);
});

$('#worldBookSearch').addEventListener('input', renderWorldBooks);
$('#worldBookFilter').addEventListener('change', renderWorldBooks);
$('#worldBookPreviewInput').addEventListener('change', () => renderWorldBookPreview().catch(fail));
$('#worldBookSelect').addEventListener('change', event => loadWorldBooks(event.target.value).catch(fail));
$('#worldBookCreate').addEventListener('click', async () => {
  const name = window.prompt('世界书名称');
  if (!name?.trim()) return;
  try { const created = await api('/api/worldbooks/', { method: 'POST', body: { name: name.trim(), description: '' } }); await loadWorldBooks(created.id); } catch (error) { fail(error); }
});
$('#worldBookDelete').addEventListener('click', () => deleteWorldBook().catch(fail));
$('#worldBookAdd').addEventListener('click', () => openWorldBookEditor());
$('#worldBookEditorBack').addEventListener('click', closeWorldBookEditor);
$('#worldBookEntryEnabled').addEventListener('click', event => event.currentTarget.classList.toggle('on'));
$('#worldBookScope').addEventListener('change', () => updateWorldBookTargetOptions());
$('#worldBookSave').addEventListener('click', () => saveWorldBookEntry().catch(fail));
$('#worldBookRemove').addEventListener('click', async () => {
  if (!worldBookEditingId || !window.confirm('确定删除这个世界书条目吗？')) return;
  try { await api(`/api/worldbooks/${path(worldBookState.current.id)}/entries/${path(worldBookEditingId)}/`, { method: 'DELETE' }); closeWorldBookEditor(); await loadWorldBooks(worldBookState.current.id); window.toast('条目已删除'); } catch (error) { fail(error); }
});
$('#worldBookManage').addEventListener('click', () => { worldBookState.manage = true; worldBookState.selected.clear(); renderWorldBooks(); });
$('#worldBookManageDone').addEventListener('click', () => { worldBookState.manage = false; worldBookState.selected.clear(); renderWorldBooks(); });
$('#worldBookList').addEventListener('change', event => {
  const box = event.target.closest('[data-worldbook-select]'); if (!box) return;
  if (box.checked) worldBookState.selected.add(box.dataset.worldbookSelect); else worldBookState.selected.delete(box.dataset.worldbookSelect);
  $('#worldBookSelectionCount').textContent = `已选 ${worldBookState.selected.size} 项`;
});
$('#worldBookBatchCategory').addEventListener('click', async () => {
  if (!worldBookState.selected.size) return window.toast('请先选择条目');
  const label = window.prompt(`输入分类名称：\n${worldBookState.categories.map(item => item.name).join('、')}`);
  const category = worldBookState.categories.find(item => item.name === label?.trim());
  if (!category) return window.toast('没有找到这个分类');
  try { await api(`/api/worldbooks/${path(worldBookState.current.id)}/entries/bulk-categories/`, { method: 'POST', body: { entry_ids: [...worldBookState.selected], category_ids: [category.id], action: 'add' } }); await loadWorldBooks(worldBookState.current.id); } catch (error) { fail(error); }
});
$('#worldBookExport').addEventListener('click', async () => {
  if (!worldBookState.current) return window.toast('请先选择世界书');
  try {
    const payload = await api(`/api/worldbooks/${path(worldBookState.current.id)}/export/`);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8' });
    const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = `${worldBookState.current.name}.json`; link.click(); URL.revokeObjectURL(link.href);
  } catch (error) { fail(error); }
});
$('#worldBookImport').addEventListener('click', () => $('#worldBookImportFile').click());
$('#worldBookImportFile').addEventListener('change', async event => {
  const file = event.target.files?.[0]; if (!file) return;
  try {
    if (file.size > 2 * 1024 * 1024) throw new Error('导入文件不能超过 2 MB');
    const payload = await readJsonFile(file);
    const source_format = payload.format === 'neon-tavern-worldbook' || (payload.worldbook && Array.isArray(payload.categories) && Array.isArray(payload.entries)) ? 'native' : 'sillytavern';
    const preview = await api('/api/worldbooks/import/preview/', { method: 'POST', body: { payload, source_format } });
    const panel = $('#worldBookImportPreview'); panel.hidden = false;
    panel.innerHTML = `<b>${h(preview.worldbook.name)}</b><br>${preview.counts.categories} 个分类 · ${preview.counts.entries} 个条目${preview.warnings.length ? `<br>${preview.warnings.map(h).join('<br>')}` : ''}`;
    if (window.confirm(`导入“${preview.worldbook.name}”？`)) {
      const result = await api('/api/worldbooks/import/commit/', { method: 'POST', body: { payload, source_format, conflict_policy: 'keep_both' } });
      await loadWorldBooks(result.worldbook.id); window.toast('世界书已导入');
    }
  } catch (error) { fail(error); }
  finally { event.target.value = ''; }
});

async function createWorldBookCategory(parentId = null) {
  const name = window.prompt(parentId ? '子分类名称' : '分类名称'); if (!name?.trim()) return;
  await api(`/api/worldbooks/${path(worldBookState.current.id)}/categories/`, { method: 'POST', body: { name: name.trim(), parent_id: parentId } });
  await loadWorldBooks(worldBookState.current.id);
}
async function editWorldBookCategory(id) {
  const category = worldBookState.categories.find(item => item.id === id); const name = window.prompt('修改分类名称', category?.name || ''); if (!name?.trim()) return;
  await api(`/api/worldbooks/${path(worldBookState.current.id)}/categories/${path(id)}/`, { method: 'PATCH', body: { name: name.trim() } }); await loadWorldBooks(worldBookState.current.id);
}
async function deleteWorldBookCategory(id) {
  if (!window.confirm('删除分类？条目会保留，子分类会上移。')) return;
  await api(`/api/worldbooks/${path(worldBookState.current.id)}/categories/${path(id)}/`, { method: 'DELETE' }); await loadWorldBooks(worldBookState.current.id);
}
async function deleteWorldBook() {
  if (state.readOnly || !worldBookState.current) return;
  const name = worldBookState.current.name || '未命名世界书';
  if (!window.confirm(`确定删除世界书“${name}”吗？其中的全部分类和条目都会删除。`)) return;
  await api(`/api/worldbooks/${path(worldBookState.current.id)}/`, { method: 'DELETE' });
  worldBookState.current = null;
  worldBookState.selected.clear();
  await loadWorldBooks();
  window.toast('世界书已删除');
}

function openConversationSettings() {
  if (state.readOnly) return;
  const conversation = state.current;
  state.editing = conversation;
  renderPicker();
  const selected = new Set(conversation.participants.filter(actor => !actor.is_player_controlled).map(actor => actor.id));
  $$('#conversationRoleScroll [data-id]').forEach(button => {
    button.classList.toggle('selected', selected.has(button.dataset.id));
    button.querySelector('span').textContent = button.classList.contains('selected') ? '✓' : '';
  });
  $('#newPlayer').value = conversation.player_character_id;
  $('#newPlayer').disabled = true;
  $('#newTitle').value = conversation.title;
  const fields = $$('#newEnvironment input:not(#newTitle), #newEnvironment textarea');
  fields[0].value = conversation.environment?.地点 || '';
  fields[1].value = conversation.environment?.时间 || '';
  fields[2].value = conversation.environment?.环境描写 || '';
  $('#newOptions [data-auto]').checked = conversation.auto_generate;
  $('#deleteConversation').hidden = false;
  original.openNewConversation();
}
async function deleteConversation() {
  if (!state.editing || !window.confirm('确定删除这段对话和所有消息吗？')) return;
  await api(`/api/conversations/${path(state.editing.id)}/`, { method: 'DELETE' });
  state.editing = null; original.closeNewConversation(); window.showConversations();
}
async function deleteCharacter() {
  if (!editingCharacter || !window.confirm('确定删除这张角色卡吗？')) return;
  await api(`/api/characters/${path(editingCharacter.id)}/`, { method: 'DELETE' });
  editingCharacter = null; window.closeRoleCreator(); await refreshLists();
}

async function editConversationById(id) {
  if (state.readOnly) return;
  state.current = await api(`/api/conversations/${path(id)}/`);
  openConversationSettings();
}

async function deleteConversationById(id) {
  if (state.readOnly || !window.confirm('确定删除这段对话和所有消息吗？')) return;
  await api(`/api/conversations/${path(id)}/`, { method: 'DELETE' });
  await refreshLists();
  window.toast('对话已删除');
}

async function deleteCharacterById(id) {
  if (state.readOnly || !window.confirm('确定删除这张角色卡吗？')) return;
  await api(`/api/characters/${path(id)}/`, { method: 'DELETE' });
  await refreshLists();
  window.toast('角色卡已删除');
}

function enableAdminView(user) {
  state.readOnly = true;
  state.adminUser = user;
  $('#prototypeApp').classList.add('admin-viewing');
  $('#prototypeApp').insertAdjacentHTML('afterbegin', `<div class="admin-view-banner"><span>只读查看：<b>${h(user.username)}</b></span><button type="button" onclick="location.assign('/admin/')">退出查看</button></div>`);
  $('#chatComposer').style.display = 'none';
}

async function boot() {
  state.me = await api('/api/auth/me/');
  if (!state.me.authenticated) { location.assign('/login/'); return; }
  const adminViewId = new URLSearchParams(location.search).get('admin_view');
  if (adminViewId) {
    if (!state.me.is_admin) { location.assign('/app/'); return; }
    const detail = await api(`/api/admin/users/${path(adminViewId)}/`);
    enableAdminView(detail.user);
  }
  await refreshLists();
  original.showConversations();
  $('.header .title').textContent = '霓虹酒馆';
  $('.env span').innerHTML = '<i class="dot"></i>我的故事';
  renderAccount();
  if (state.readOnly) {
    $('#account .card .card-head b').textContent = state.adminUser.username;
    $('#account .card .avatar').textContent = state.adminUser.username.slice(0, 1);
    $$('#account .settings-entry, #account .create-line').forEach(element => { element.hidden = true; });
  }
  $$('#account .card').forEach((card, index) => { if (index > 0) card.remove(); });
  $('#prototypeApp').style.visibility = 'visible';
}
boot().catch(error => {
  $('#conversationHome').innerHTML = `<div class="live-error">${h(error.message)} <button onclick="location.reload()">重试</button></div>`;
  $('#prototypeApp').style.visibility = 'visible';
});
