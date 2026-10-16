let token,current,snapshot; const $=s=>document.querySelector(s);
async function api(path,body,recover=true){token??=(await(await fetch('/api/session')).json()).token;const r=await fetch('/api/'+path,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body)});const d=await r.json();if(r.status===403&&recover){token=null;return api(path,body,false);}if(!r.ok)throw Error(d.error);return d;}
$('#probe').onclick=async()=>{const button=$('#probe');button.disabled=true;try{
 await refresh(); let t=current;
 if(t&&['failed','needs_adjustment','cancelled'].includes(t.status)) t=await api('retry',{task_id:t.id});
 if(!t){const im=snapshot.images[0];if(!im)throw Error('缺少测试图片');t=await api('tasks',{image_id:im.id,base_version_id:im.current_version_id,request_key:crypto.randomUUID(),mode:'probe',overall:'这是接入探针，读取两处中文批注并写回测试结果，不生成图片。',regions:[{x:60,y:60,w:350,h:180,note:'第一处：将「试用版」改成「正式版」，仅测试传输。'},{x:520,y:400,w:350,h:160,note:'第二处：将数字 2025 改成 2026，仅测试传输。'}]});}
 const result=await api('handoff',{task_id:t.id});$('#message').textContent=result.message;$('#opened').textContent='系统打开请求已返回；预填待桌面确认'; await refresh();
}catch(e){$('#message').textContent=e.message;}finally{button.disabled=false;}};
$('#cancel').onclick=async()=>{try{await api('cancel',{task_id:current.id});await refresh();}catch(e){$('#message').textContent=e.message;}};
const labels={waiting:'待发送',running:'Codex 执行中',completed:'接入回执已完成',failed:'执行失败',needs_adjustment:'需要调整',cancelled:'已取消'};
async function refresh(){try{
 snapshot=await(await fetch('/api/state')).json(); current=snapshot.tasks.find(t=>t.mode==='probe');
 if($('#message').textContent.startsWith('连接本地服务失败'))$('#message').textContent='本地服务连接已恢复';
 if(!current){$('#badge').textContent='尚无任务';return;}
 $('#saved').textContent='任务、两处批注与预览已落盘';$('#badge').textContent=labels[current.status];$('#task-id').textContent='任务 '+current.id;
 $('#preview').src=current.preview_url;$('#image-info').textContent=`原尺寸 ${current.width} × ${current.height}`;
 $('#notes').replaceChildren(...current.regions.map(r=>{const p=document.createElement('p');p.className='note';p.textContent=`${r.number}　${r.note}`;return p;}));
 $('#probe').textContent=['failed','needs_adjustment','cancelled'].includes(current.status)?'重试并去 Codex':'再次打开 Codex 草稿';
 $('#probe').hidden=['running','completed'].includes(current.status);$('#cancel').hidden=!['waiting','running'].includes(current.status);
 $('#received').textContent=current.status==='waiting'?'待你在 Codex 点击发送':current.status==='running'?'Codex 已通过执行令牌领取任务':current.status==='completed'?'收到 Codex 的两条批注回执':'任务未完成';
 if(current.error)$('#message').textContent=current.error;
 if(current.receipt){$('#receipt').hidden=false;$('#receipt').textContent=JSON.stringify(current.receipt,null,2);$('#help').textContent='已收到回执。该结果只验证交接，不代表真实图片修改已完成。';}
}catch(e){$('#message').textContent='连接本地服务失败：'+e.message;}}
refresh();setInterval(refresh,1200);
