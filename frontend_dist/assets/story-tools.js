import {api, escapeHtml as h} from './common.js';

function rowsMarkup(object, prefix) {
  return Object.entries(object || {}).map(([key,value]) => `<label style="display:grid;grid-template-columns:1fr 2fr;gap:8px;margin:8px 0"><span>${h(key)}</span><input data-object="${prefix}" data-key="${h(key)}" data-kind="${typeof value}" value="${h(typeof value==='object'?JSON.stringify(value):String(value))}"></label>`).join('');
}

function readObject(form,prefix) {
  const object={};
  for(const input of form.querySelectorAll(`[data-object="${prefix}"]`)) {
    const key=input.dataset.key || input.closest('label').querySelector('[data-new-key]')?.value.trim();
    if(!key) continue;
    const kind=input.dataset.kind;
    object[key]=kind==='number'?Number(input.value):kind==='boolean'?input.value==='true':kind==='object'?JSON.parse(input.value):input.value;
  }
  return object;
}

export async function openStoryState(conversation,reload) {
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(92vw,540px);max-height:85vh;overflow:auto;border:1px solid #c9dfe5;border-radius:16px;padding:18px;box-sizing:border-box;color:#34535d';
  dialog.innerHTML=`<form><h3>本次对话的角色状态</h3><p>修改只影响当前故事，角色卡初始设定保持不变。</p><select name="actor" style="width:100%;padding:8px">${conversation.participants.map(actor=>`<option value="${h(actor.id)}">${h(actor.name)}</option>`).join('')}</select><div data-state-body></div><p data-error role="status"></p><div style="display:flex;gap:10px"><button type="submit">保存状态</button><button type="button" data-close>关闭</button></div></form>`;
  document.body.append(dialog);
  const form=dialog.querySelector('form');
  let current=null;
  const endpoint=()=>`/api/conversations/${encodeURIComponent(conversation.id)}/states/${encodeURIComponent(form.elements.actor.value)}/`;
  async function load() {
    current=await api(endpoint());
    dialog.querySelector('[data-state-body]').innerHTML=`<p>状态来源：${h(current.initialization_source)}</p><h4>当前状态</h4><div data-state-rows>${rowsMarkup(current.state_fields,'state_fields')}</div><button type="button" data-add>＋ 添加状态</button><label style="display:block;margin:12px 0">好感度 <input name="affinity" type="number" min="0" max="100" value="${current.affinity}"></label><label style="display:block">衣着类型 <input name="clothing_type" value="${h(current.clothing_type)}"></label><label style="display:block;margin:12px 0">衣着状态 <input name="clothing_state" value="${h(current.clothing_state)}"></label><h4>关系备注</h4>${rowsMarkup(current.relationship_notes,'relationship_notes')}<details><summary>角色卡更新（${Object.keys(current.template_changes).length} 项变化）</summary>${Object.entries(current.template_changes).map(([key,value])=>`<div><label><input type="checkbox" data-template-key="${h(key)}" checked>同步此项</label><b>${h(({name:'名字',summary:'摘要',personality:'角色设定',speech_habits:'说话习惯',memories:'背景',relationship_notes:'关系备注',state_fields:'初始状态',affinity:'初始好感度',clothing_type:'衣着类型',clothing_state:'衣着状态',scenario:'开局场景',first_mes:'开场白',alternate_greetings:'备用开场白',character_worldbook:'专属世界书',context_policy:'长设定加载配置'})[key]||key)}</b><p style="white-space:pre-wrap">原内容：${h(typeof value.before==='object'?JSON.stringify(value.before):value.before || '')}</p><p style="white-space:pre-wrap">新内容：${h(typeof value.after==='object'?JSON.stringify(value.after):value.after || '')}</p></div>`).join('')}<label><input name="reset_state" type="checkbox">同时重置为角色卡的初始状态</label><button type="button" data-sync>应用角色卡更新</button></details>`;
  }
  const showError=error=>dialog.querySelector('[data-error]').textContent=error.message;
  form.elements.actor.addEventListener('change',()=>load().catch(showError));
  dialog.addEventListener('click',async event=>{
    if(event.target.closest('[data-close]')) dialog.close();
    if(event.target.closest('[data-add]')) {
      const label=document.createElement('label');
      label.style.cssText='display:flex;gap:8px;margin:8px 0';
      label.innerHTML='<input data-new-key placeholder="状态名称"><input data-object="state_fields" data-kind="string" placeholder="状态内容">';
      dialog.querySelector('[data-state-rows]').append(label);
    }
    if(event.target.closest('[data-sync]')) {
      if(!confirm('将应用上方列出的角色卡更新，是否继续？')) return;
      try { await api(endpoint(),{method:'POST',body:{revision:current.revision,reset_state:form.elements.reset_state.checked,selected_fields:[...form.querySelectorAll("[data-template-key]:checked")].map(input=>input.dataset.templateKey)}}); await reload(); await load(); }
      catch(error) {showError(error);}
    }
  });
  form.addEventListener('submit',async event=>{
    event.preventDefault();
    try {
      await api(endpoint(),{method:'PATCH',body:{revision:current.revision,state_fields:readObject(form,'state_fields'),relationship_notes:readObject(form,'relationship_notes'),affinity:Number(form.elements.affinity.value),clothing_type:form.elements.clothing_type.value,clothing_state:form.elements.clothing_state.value}});
      await reload(); await load(); dialog.querySelector('[data-error]').textContent='状态已保存';
    } catch(error) {showError(error);}
  });
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.showModal();
  await load().catch(showError);
}

export async function openStoryMemory(conversation,reload) {
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(92vw,560px);max-height:85vh;overflow:auto;border:1px solid #c9dfe5;border-radius:16px;padding:18px;box-sizing:border-box;color:#34535d';
  document.body.append(dialog);
  const endpoint=`/api/conversations/${encodeURIComponent(conversation.id)}/memory/`;
  let current;
  let polling=null;
  const showError=error=>{const target=dialog.querySelector('[data-error]');if(target)target.textContent=error.message;};
  function factMarkup(fact) {
    return `<label style="display:flex;gap:8px;margin:10px 0"><textarea data-fact-id="${h(fact.id)}" rows="2" style="flex:1">${h(fact.text)}</textarea><button type="button" data-remove-fact>解除锁定</button></label>`;
  }
  async function load() {
    current=await api(endpoint);
    dialog.innerHTML=`<form><h3>故事记忆</h3><p>锁定事实会随每轮回复传入，自动总结只更新下方事件记忆。</p><p>总结状态：${h(({idle:'尚未总结',queued:'等待处理',running:'正在总结',done:'总结完成',failed:'总结失败'})[current.status]||'等待处理')} · 已整理 ${current.message_count} 条消息</p>${current.error?`<p role="alert">${h(current.error)}</p>`:''}<label>事件记忆<textarea name="text" rows="8" maxlength="20000" style="display:block;width:100%;box-sizing:border-box">${h(current.text)}</textarea></label><h4>锁定事实</h4><div data-facts>${current.locked_facts.map(factMarkup).join('')}</div><button type="button" data-add-fact>＋ 添加锁定事实</button><details style="margin:12px 0"><summary>记忆修改记录（最近 ${current.history.length} 次）</summary>${current.history.map(item=>`<article><b>版本 ${item.revision} · ${h(item.source)}</b><p style="white-space:pre-wrap">${h(item.text)}</p><small>消息范围截至第 ${item.message_count} 条</small></article>`).join('')}</details><p data-error role="status"></p><div style="display:flex;gap:8px"><button type="submit">保存记忆</button><button type="button" data-retry-memory>重新总结</button><button type="button" data-close>关闭</button></div></form>`;
    if(['queued','running'].includes(current.status) && polling===null) polling=setInterval(async()=>{
      try { const latest=await api(endpoint); if(!['queued','running'].includes(latest.status)){clearInterval(polling);polling=null;await load();await reload();} }
      catch(error){showError(error);clearInterval(polling);polling=null;}
    },2000);
  }
  dialog.addEventListener('click',async event=>{
    if(event.target.closest('[data-close]')) dialog.close();
    if(event.target.closest('[data-remove-fact]')) event.target.closest('label').remove();
    if(event.target.closest('[data-add-fact]')) dialog.querySelector('[data-facts]').insertAdjacentHTML('beforeend',factMarkup({id:crypto.randomUUID(),text:''}));
    if(event.target.closest('[data-retry-memory]')) {
      if(!confirm('重新总结会使用模型服务。未保存的编辑不会参与总结，是否继续？'))return;
      try {await api(endpoint,{method:'POST',body:{revision:current.revision}});await load();}
      catch(error){showError(error);}
    }
  });
  dialog.addEventListener('submit',async event=>{
    event.preventDefault();
    try {
      const facts=[...dialog.querySelectorAll('[data-fact-id]')].map(input=>({id:input.dataset.factId,text:input.value}));
      await api(endpoint,{method:'PATCH',body:{revision:current.revision,text:dialog.querySelector('[name=text]').value,locked_facts:facts}});
      await reload();await load();dialog.querySelector('[data-error]').textContent='记忆已保存';
    } catch(error){showError(error);}
  });
  dialog.addEventListener('close',()=>{if(polling!==null)clearInterval(polling);dialog.remove();});
  dialog.showModal();
  await load().catch(showError);
}

export async function openStoryCheckpoints(conversation,reload,openBranch) {
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(92vw,600px);max-height:85vh;overflow:auto;border:1px solid #c9dfe5;border-radius:16px;padding:18px;box-sizing:border-box;color:#34535d';
  const endpoint=`/api/conversations/${encodeURIComponent(conversation.id)}/checkpoints/`;
  dialog.innerHTML=`<h3>剧情存档与分支</h3><p>回档前会创建“回档前自动存档”，保存当前消息、状态、记忆及世界书内容。</p><form data-create><label>存档名称<input name="name" maxlength="120" required style="display:block;width:100%;box-sizing:border-box"></label><label>存档节点<select name="through_message_id" style="display:block;width:100%;padding:8px"><option value="">当前完整节点</option>${(conversation.messages||[]).filter(message=>message.kind!=='state').map(message=>`<option value="${h(message.id)}"${message.has_story_snapshot?'':' disabled'}>${h(message.content.slice(0,50))}${message.has_story_snapshot?'':'（旧节点无历史状态）'}</option>`).join('')}</select></label><button type="submit" style="margin:10px 0">创建存档</button></form><div data-checkpoints></div><div data-preview></div><p data-error role="status"></p><button type="button" data-close>关闭</button>`;
  document.body.append(dialog);
  let selected=null;
  let preview=null;
  const showError=error=>dialog.querySelector('[data-error]').textContent=error.message;
  async function load() {
    const result=await api(endpoint);
    dialog.querySelector('[data-checkpoints]').innerHTML=result.items.map(item=>`<div style="display:flex;gap:10px;margin:10px 0"><span style="flex:1">${h(item.name)} · ${item.message_count} 条消息</span><button type="button" data-checkpoint="${h(item.id)}">查看与操作</button></div>`).join('')||'<p>尚未创建存档。</p>';
  }
  dialog.querySelector('[data-create]').addEventListener('submit',async event=>{
    event.preventDefault();
    const form=event.target;
    try {await api(endpoint,{method:'POST',body:{name:form.elements.name.value,through_message_id:form.elements.through_message_id.value||null}});await load();dialog.querySelector('[data-error]').textContent='存档已创建';}
    catch(error){showError(error);}
  });
  dialog.addEventListener('click',async event=>{
    if(event.target.closest('[data-close]')) dialog.close();
    const checkpoint=event.target.closest('[data-checkpoint]');
    if(checkpoint) {
      selected=checkpoint.dataset.checkpoint;
      try {
        preview=await api(endpoint+encodeURIComponent(selected)+'/');
        dialog.querySelector('[data-preview]').innerHTML=`<h4>${h(preview.name)}</h4><p>消息数：当前 ${preview.current_message_count} → 存档 ${preview.message_count}</p>${Object.entries(preview.changes).map(([name,difference])=>`<details><summary>${h(name)}发生变化</summary><p>当前内容</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${h(JSON.stringify(difference.before,null,2))}</pre><p>恢复内容</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${h(JSON.stringify(difference.after,null,2))}</pre></details>`).join('')}<label>新分支名称<input data-branch-title maxlength="120" value="${h(preview.name+'的分支')}"></label><div style="display:flex;gap:8px;margin:10px 0"><button type="button" data-fork>创建独立分支</button><button type="button" data-restore>回到此存档</button></div>`;
      } catch(error){showError(error);}
    }
    if(event.target.closest('[data-fork]')&&selected) {
      try {const result=await api(endpoint+encodeURIComponent(selected)+'/',{method:'POST',body:{action:'fork',title:dialog.querySelector('[data-branch-title]').value}});dialog.close();await openBranch(result.conversation_id);}
      catch(error){showError(error);}
    }
    if(event.target.closest('[data-restore]')&&selected) {
      if(!confirm('将按上方预览恢复本故事，并使当前生成任务作废；回档前状态会自动存档。是否继续？'))return;
      try {await api(endpoint+encodeURIComponent(selected)+'/',{method:'POST',body:{action:'restore',revision:preview.revision}});await reload();await load();dialog.querySelector('[data-preview]').replaceChildren();dialog.querySelector('[data-error]').textContent='已回档，可选择回档前自动存档恢复';}
      catch(error){showError(error);}
    }
  });
  dialog.addEventListener('close',()=>dialog.remove());
  dialog.showModal();
  await load().catch(showError);
}
