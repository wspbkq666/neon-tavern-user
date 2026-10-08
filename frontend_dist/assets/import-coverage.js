import {api,escapeHtml as h} from './common.js';

const fieldLabels={name:'名称',description:'简介',summary:'一句话介绍',personality:'完整设定',scenario:'场景',memories:'背景经历',speech_habits:'行为和说话规则',state_fields:'状态',relationship_notes:'关系',entries:'世界书条目',first_mes:'开场白',alternate_greetings:'备用开场白',character_worldbook:'角色专属世界书',keywords:'关键词',content:'正文'};
const fieldLabel=value=>value.replace(/^fields\./,'').split('.').map(part=>fieldLabels[part] || part).join(' · ');
export async function openImportCoverage(taskId,refreshDrafts) {
  const endpoint=`/api/import-tasks/${encodeURIComponent(taskId)}/coverage/`;
  let report=await api(endpoint);
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(95vw,1000px);max-height:88vh;overflow:auto;border-radius:16px;padding:20px;border:1px solid #c9dfe5';
  document.body.append(dialog);
  function render() {
    const filter=dialog.querySelector('[data-filter]')?.value || '';
    dialog.innerHTML=`<h3>导入查漏与来源复核</h3><p>${h(report.note)}</p><label>筛选<select data-filter><option value="">所有段落</option><option value="待确认">待确认</option><option value="重点核对">重点核对</option></select></label><div class="coverage-columns" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:16px"><section><h4>原文段落与字段来源</h4>${report.paragraphs.filter(row=>!filter || row.status===filter || row.priority===filter).map(row=>`<details data-paragraph="${h(row.id)}"><summary>第 ${row.start+1}–${row.end} 字 · ${h(row.decision || row.status)} · ${h(row.priority)}</summary><pre style="white-space:pre-wrap">${h(row.text)}</pre><p>${h(row.reason)}</p>${row.references.map(ref=>`<button data-source-draft="${ref.draft_index}" data-field="${h(ref.field)}">草稿 ${ref.draft_index+1} · ${h(fieldLabel(ref.field))}</button>`).join('')}<label>人工复核<select data-decision><option value="">请选择</option>${['已核对','格式要求','需补充'].map(value=>`<option${row.decision===value?' selected':''}>${value}</option>`).join('')}</select></label><button data-review>保存复核</button><button data-rescan>仅重新识别此段</button></details>`).join('')}</section><section><h4>清理记录与无法定位的字段</h4>${report.filter_records.map(row=>`<details><summary>原文 ${row.start+1}–${row.end} 字</summary><pre style="white-space:pre-wrap">${h(row.original)}</pre><p>${h(row.reason)}</p></details>`).join('')}${report.unmatched_fields.map(row=>`<p>草稿 ${row.draft_index+1} · ${h(fieldLabel(row.field))}：${h(row.reason)}</p>`).join('')}<h4>局部重识别建议</h4>${report.rescans.map(task=>`<div><b>${h(task.filename)}</b> · ${h(({queued:'等待',running:'识别中',done:'完成',failed:'失败',canceled:'取消'})[task.status])}${task.status==='done'?`<button data-suggestion="${h(task.id)}">查看差异并接受新增草稿</button>`:''}</div>`).join('')}</section></div><p data-status role="status"></p><button data-refresh>刷新建议</button><button data-close>关闭</button>`;
    dialog.querySelector('[data-filter]').value=filter;
    dialog.querySelector('[data-filter]').onchange=render;
    dialog.querySelector('[data-close]').onclick=()=>dialog.close();
  }
  render();dialog.showModal();dialog.addEventListener('close',()=>{dialog.remove();refreshDrafts().catch(()=>{});},{once:true});
  dialog.onclick=async event=>{
    const button=event.target.closest('button');if(!button)return;
    try {
      if(button.dataset.sourceDraft!==undefined) {dialog.close();const card=document.querySelector(`[data-smart-import-card="${button.dataset.sourceDraft}"]`);card?.scrollIntoView({behavior:'smooth'});card?.querySelector(`[data-smart-import-field="${button.dataset.field.split('.')[1]?.split('[')[0]}"]`)?.focus();return;}
      if(button.hasAttribute('data-refresh')) {report=await api(endpoint);render();return;}
      const paragraph=button.closest('[data-paragraph]');
      let body;
      if(button.hasAttribute('data-review'))body={action:'review',revision:report.revision,paragraph_id:paragraph.dataset.paragraph,decision:paragraph.querySelector('[data-decision]').value};
      if(button.hasAttribute('data-rescan'))body={action:'rescan',revision:report.revision,paragraph_id:paragraph.dataset.paragraph};
      if(button.dataset.suggestion) {
        const child=await api(`/api/import-tasks/${encodeURIComponent(button.dataset.suggestion)}/`);
        const existing=document.querySelectorAll('[data-smart-import-card]').length;
        if(!window.confirm(`当前有 ${existing} 条草稿，建议新增 ${child.result.drafts.length} 条。\n${child.result.drafts.map(row=>`${row.fields.name || '待确认'}：${JSON.stringify(row.fields)}`).join('\n')}\n接受后保留原草稿，同名内容需人工确认。`))return;
        body={action:'accept_rescan',revision:report.revision,child_id:button.dataset.suggestion};
      }
      if(!body)return;
      const result=await api(endpoint,{method:'POST',body});
      report=await api(endpoint);render();
      dialog.querySelector('[data-status]').textContent=result.message || '已保存复核';
      if(body.action==='accept_rescan')await refreshDrafts();
    }catch(error){dialog.querySelector('[data-status]').textContent=error.message;}
  };
}
