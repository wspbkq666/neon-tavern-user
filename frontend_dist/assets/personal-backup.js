import {api,escapeHtml as h} from './common.js';
const labels={Character:'角色卡',CharacterCategory:'角色分类',Worldbook:'世界书',WorldbookCategory:'世界书分类',WorldbookEntry:'世界书条目',Conversation:'对话',ConversationParticipant:'参与角色',ConversationActorState:'故事角色状态',ConversationWorldbookConfig:'对话世界书设置',Message:'消息',StoryCheckpoint:'剧情存档',StoryMemoryRevision:'记忆历史',GenerationJob:'生成任务',GenerationContextTrace:'实际设定使用记录',ImportTask:'导入任务',ImportSegment:'导入分段',ImportTaskEvent:'识别日志',ImportBatch:'导入历史'};

export async function openPersonalBackup(reload) {
  const dialog=document.createElement('dialog');
  dialog.style.cssText='width:min(94vw,640px);max-height:86vh;overflow:auto;padding:20px;border-radius:16px;border:1px solid #cadfe5';
  dialog.innerHTML='<h3>个人完整备份与恢复</h3><p>包含角色、世界书、对话、状态、记忆、存档、导入来源和日志；账号密码、连接密钥和管理员设置不进入备份。恢复新增资料，不删除现有数据。</p><p>后台备份任务保留七天，可主动删除；下载到本机的备份不受此期限限制。上传和解压总量各不超过 100 MiB，最多 10,000 个文件，压缩比不超过 100。</p><button data-export>生成个人备份</button><label>上传备份 ZIP<input data-file type="file" accept=".zip"></label><button data-preview>上传并检查恢复内容</button><p data-status role="status"></p><div data-jobs></div><button data-close>关闭</button>';
  document.body.append(dialog);dialog.showModal();
  let timer=null;
  async function load() {
    const result=await api('/api/personal-backups/');
    dialog.querySelector('[data-jobs]').innerHTML=result.jobs.map(job=>`<div class="card"><b>${h(({export:'导出备份',preview:'恢复预览',restore:'恢复资料'})[job.kind])}</b> · ${h(({queued:'等待后台',running:'处理中',done:'完成',failed:'失败'})[job.status])}<p>${h(job.error)}</p><p>保留至 ${h(new Date(job.expires_at).toLocaleString())}</p>${job.status!=='running'?`<button data-delete="${h(job.id)}">删除此备份任务</button>`:''}${job.status==='done'&&job.kind==='export'?`<a href="/api/personal-backups/${encodeURIComponent(job.id)}/download/">下载个人备份 ZIP</a>`:''}${job.status==='done'&&job.kind==='preview'?`<pre style="white-space:pre-wrap">${Object.entries(job.result.counts).map(([key,value])=>`${h(labels[key] || '资料')}：${value}`).join('\n')}</pre><p>${(job.result.warnings || []).map(h).join('<br>')}</p><button data-restore="${h(job.id)}">确认新增恢复这些资料</button>`:''}</div>`).join('');
  }
  dialog.onclick=async event=>{
    const button=event.target.closest('button');if(!button)return;
    try {
      if(button.hasAttribute('data-close')) {dialog.close();return;}
      if(button.dataset.delete) {if(!window.confirm('删除此后台备份任务及备份文件？现有角色和对话不受影响。'))return;await api(`/api/personal-backups/${encodeURIComponent(button.dataset.delete)}/`,{method:'DELETE'});}
      if(button.hasAttribute('data-export')) await api('/api/personal-backups/',{method:'POST',body:{action:'export'}});
      if(button.hasAttribute('data-preview')) {const file=dialog.querySelector('[data-file]').files[0];if(!file)throw new Error('请选择个人备份 ZIP');if(file.size>100*1024*1024)throw new Error('文件不能超过 100 MiB');const form=new FormData();form.append('file',file);await api('/api/personal-backups/',{method:'POST',body:form});}
      if(button.dataset.restore && window.confirm('确认将预览中的资料新增到当前账号？现有资料保留，连接密钥不恢复。'))await api('/api/personal-backups/',{method:'POST',body:{action:'restore',preview_id:button.dataset.restore}});
      await load();dialog.querySelector('[data-status]').textContent='任务已提交，后台状态会自动刷新。';
    }catch(error){dialog.querySelector('[data-status]').textContent=error.message;}
  };
  await load();timer=setInterval(()=>load().catch(error=>dialog.querySelector('[data-status]').textContent=error.message),2000);
  dialog.addEventListener('close',()=>{clearInterval(timer);dialog.remove();reload().catch(()=>{});},{once:true});
}
