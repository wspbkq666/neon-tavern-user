import {api,escapeHtml as h} from './common.js';

export async function openCharacterContext(actor,reload) {
  const endpoint=`/api/characters/${encodeURIComponent(actor.id)}/context/`;
  let current=await api(endpoint);
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(94vw,640px);max-height:86vh;overflow:auto;padding:20px;border-radius:16px;border:1px solid #cadfe5';
  dialog.innerHTML=`<form><h3>长设定与核心确认：${h(actor.name)}</h3><p>原文完整保存。未确认核心时，全部设定均为必要内容；确认后，核心和勾选的必要段落始终使用，其他段落按关键词和预算选择。更改原文后需要重新确认；现有故事通过“角色状态”同步后生效。</p><label>确认的核心设定<textarea name="core" rows="8" style="height:auto;resize:vertical">${h(current.policy.core_text || '')}</textarea></label><label><input type="checkbox" name="confirmed"${current.policy.confirmed?' checked':''}>我已确认核心覆盖必须遵守的设定与禁止项</label><h4>原文段落</h4>${current.segments.map(row=>`<details data-segment="${h(row.id)}"><summary>${h(row.field==='personality'?'角色设定':'背景记忆')} · 原文第 ${row.start+1}–${row.end} 字</summary><pre style="white-space:pre-wrap">${h(row.text)}</pre><label><input type="checkbox" data-required${row.required?' checked':''}>始终使用此段</label><label>段落标签<input data-label value="${h(row.label || '')}"></label><label>匹配关键词（中文逗号分隔；留空按剩余预算加载）<input data-keywords value="${h((row.keywords || []).join('，'))}"></label></details>`).join('')}<p data-status role="status"></p><button type="submit">保存核心与段落选择</button><button type="button" data-close>关闭</button></form>`;
  document.body.append(dialog);dialog.showModal();
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  dialog.querySelector('form').onsubmit=async event=>{
    event.preventDefault();
    const form=event.currentTarget;
    const segments=Object.fromEntries([...dialog.querySelectorAll('[data-segment]')].map(node=>[node.dataset.segment,{required:node.querySelector('[data-required]').checked,label:node.querySelector('[data-label]').value,keywords:node.querySelector('[data-keywords]').value.split(/[,，;；、\n]/).map(value=>value.trim()).filter(Boolean)}]));
    try {current=await api(endpoint,{method:'PATCH',body:{revision:current.revision,policy:{confirmed:form.elements.confirmed.checked,core_text:form.elements.core.value,segments}}});await reload();dialog.querySelector('[data-status]').textContent='已保存；原文保持完整。';}
    catch(error){dialog.querySelector('[data-status]').textContent=error.message;}
  };
}
