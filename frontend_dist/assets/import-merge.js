import {api,escapeHtml as h} from './common.js';
const labels={fields:'字段',categories:'分类',state_fields:'状态',relationship_notes:'关系',summary:'一句话介绍',name:'名称',first_mes:'开场白',affinity:'好感度',clothing_type:'衣着类型',clothing_state:'衣着状态'};
const label=value=>value.split('.').map(key=>labels[key]||key).join(' · ');

export async function openImportMerge(taskId,refreshDrafts) {
  const endpoint=`/api/import-tasks/${encodeURIComponent(taskId)}/merge/`;
  let current=await api(endpoint),preview=null;
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(94vw,800px);max-height:86vh;overflow:auto;padding:20px;border-radius:16px';
  dialog.innerHTML=`<h3>合并同名分段</h3><p>仅在你确认这些分段属于同一个角色或世界书后合并。文字设定保留各段原文；状态、分类和开场白的冲突必须逐项选择。原分段仍保存在任务中。</p><select data-group><option value="">选择需要合并的同名项</option>${current.groups.map((row,index)=>`<option value="${index}">${h(row.name)} · ${row.indices.length} 段 · ${h(({npc:'NPC角色',player:'玩家卡',worldbook:'世界书'})[row.type])}</option>`).join('')}</select><div data-preview></div><p data-status role="status"></p><button data-commit disabled>确认属于同一素材并合并</button><button data-close>关闭</button>`;
  document.body.append(dialog);dialog.showModal();
  dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
  const status=message=>dialog.querySelector('[data-status]').textContent=message;
  if(!current.groups.length)status('没有同类型、同原文名称的重复草稿。');
  dialog.querySelector('[data-group]').onchange=async event=>{
    preview=null;dialog.querySelector('[data-commit]').disabled=true;
    if(!event.target.value)return;
    try {
      const group=current.groups[Number(event.target.value)];
      preview=await api(endpoint,{method:'POST',body:{action:'preview',revision:current.revision,indices:group.indices}});
      dialog.querySelector('[data-preview]').innerHTML=`<h4>原始分段与合并后的设定</h4>${preview.originals.map((row,index)=>`<details><summary>原分段 ${index+1}</summary><pre style="white-space:pre-wrap">${h(JSON.stringify(row.fields,null,2))}</pre></details>`).join('')}<details><summary>合并后预览</summary><pre style="white-space:pre-wrap">${h(JSON.stringify(preview.draft.fields,null,2))}</pre></details><h4>需要选择的冲突</h4>${preview.conflicts.map(row=>`<label style="display:block;margin:10px 0">${h(label(row.field))}<select data-conflict="${h(row.field)}"><option value="">请选择保留的原值</option>${row.values.map((value,index)=>`<option value="${index}">${h(JSON.stringify(value))}</option>`).join('')}</select></label>`).join('')}`;
      dialog.querySelector('[data-commit]').disabled=false;status('检查原分段和合并后的内容，再明确确认。');
    }catch(error){status(error.message);}
  };
  dialog.querySelector('[data-commit]').onclick=async()=>{
    if(!preview)return;
    try {
      const inputs=[...dialog.querySelectorAll('[data-conflict]')];
      if(inputs.some(input=>input.value===''))throw new Error('请逐项选择所有冲突，未选内容不会自动覆盖。');
      const choices=Object.fromEntries(inputs.map(input=>[input.dataset.conflict,Number(input.value)]));
      const group=current.groups[Number(dialog.querySelector('[data-group]').value)];
      await api(endpoint,{method:'POST',body:{action:'commit',revision:current.revision,indices:group.indices,choices}});
      await refreshDrafts();dialog.close();
    }catch(error){status(error.message);}
  };
}
