'use strict';
// Page boxes are user location hints. Only the reviewed object plan grants
// permission to edit source pixels; no screenshot-to-image guessing occurs here.
P.hints=P.hints||[];
P.frameMode=false;
P.pageDrag=null;
P.hintsKey='';

function canFrame(){
  const d=deck();
  return d?.status==='ready'&&!P.busy&&!activeJobs().length&&(!P.view||P.view===d.current_version_id)&&!$('#show-before').checked;
}
function drawHint(box,number,temporary=false){
  const svg=$('#boxes'),ns='http://www.w3.org/2000/svg';
  const p=document.createElementNS(ns,'polygon');
  p.setAttribute('class',temporary?'draft-box temporary':'draft-box');
  p.setAttribute('points',[[box.x,box.y],[box.x+box.w,box.y],[box.x+box.w,box.y+box.h],[box.x,box.y+box.h]].map(v=>v.join(',')).join(' '));
  svg.append(p);
  if(!temporary){
    const text=document.createElementNS(ns,'text');
    text.setAttribute('class','draft-label');text.setAttribute('x',box.x);text.setAttribute('y',box.y+deck().structure.width/70);
    text.setAttribute('font-size',deck().structure.width/70);text.setAttribute('fill','#983f22');text.textContent='框 '+number;svg.append(text);
    const size=deck().structure.width/95;
    for(const [x,y] of [[box.x,box.y],[box.x+box.w,box.y],[box.x,box.y+box.h],[box.x+box.w,box.y+box.h]]){const handle=document.createElementNS(ns,'rect');handle.setAttribute('class','draft-handle');handle.setAttribute('x',x-size/2);handle.setAttribute('y',y-size/2);handle.setAttribute('width',size);handle.setAttribute('height',size);svg.append(handle);}
  }
}
function renderInputs(){
  const d=deck(),allowed=canFrame();
  $('#boxes').querySelectorAll('.draft-box,.draft-label,.draft-handle').forEach(e=>e.remove());
  $('#draw-box').disabled=!allowed;
  $('#draw-box').textContent=P.frameMode?'框选中，再点退出':'框选修改';
  $('#draw-box').setAttribute('aria-pressed',String(P.frameMode));
  $('#preview').classList.toggle('framing',P.frameMode&&allowed);
  $('#draw-help').textContent=!allowed&&d?.status==='ready'?'查看当前版本且无活动任务时可以框选。':'点击「框选修改」，在页面上拖出范围；框选是定位提示，整理后统一确认实际修改范围。';
  for(const [index,h] of (P.hints||[]).entries())if(h.page===P.page)drawHint(P.pageDrag?.id===h.id&&P.pageDrag.box?P.pageDrag.box:h.box,index+1);
  if(P.pageDrag?.page===P.page&&P.pageDrag.box&&!P.pageDrag.id)drawHint(P.pageDrag.box,0,true);
  const hints=P.hints||[];
  $('#box-section').hidden=!hints.length;$('#box-count').textContent=hints.length+' 处';
  let scope=[];try{scope=selected();}catch{}
  const key=JSON.stringify([hints,scope,activeJobs().map(j=>j.id)]);
  if(P.hintsKey!==key){
    P.hintsKey=key;const host=$('#box-notes');host.replaceChildren();
    hints.forEach((h,index)=>{
      const card=el('article',{className:'hint-card',id:'hint-'+h.id});
      card.append(el('strong',{},`第 ${h.page} 页 · 框 ${index+1}${scope.includes(h.page)?'':'（本次不处理，未勾选此页）'}`));
      const label=el('label',{},'这处怎么改'),input=el('textarea',{value:h.note||'',rows:2,maxLength:6000});
      input.setAttribute('aria-label',`第 ${h.page} 页框 ${index+1} 修改要求`);
      input.placeholder='例如：把这里的日期改为 10 月 20 日；或在上方我的修改要求中统一说明';
      input.disabled=!!activeJobs().length;
      input.oninput=()=>{h.note=input.value;P.dirty=true;saveDraft();P.hintsKey=JSON.stringify([P.hints,scope,activeJobs().map(j=>j.id)]);buttons();};
      input.onchange=()=>{P.invalidating=edited().catch(e=>notice(e.message,true));};
      label.append(input);card.append(label);
      const row=el('div',{className:'row'}),view=el('button',{},'定位到此框'),remove=el('button',{},'删除框选');
      view.onclick=()=>{P.page=h.page;renderThumbs();renderPreview();};
      remove.disabled=!!activeJobs().length;
      remove.onclick=()=>action(async()=>{P.hints=P.hints.filter(v=>v.id!==h.id);P.hintsKey='';await edited();renderPreview();});
      row.append(view,remove);card.append(row);host.append(card);
    });
  }
  if(P.plan?.unchanged_pages?.length&&!$('#kept-pages')){
    const details=el('details',{id:'kept-pages'});details.append(el('summary',{},`保留 ${P.plan.unchanged_pages.length} 页，无需修改`));
    for(const row of P.plan.unchanged_pages)details.append(el('p',{},`第 ${row.page} 页：${row.reason}`));
    $('#plan').append(details);
  }
}
function pagePoint(e){
  const image=$('#page-image'),bounds=image.getBoundingClientRect(),s=deck().structure;
  return {x:Math.round(Math.max(0,Math.min(s.width,(e.clientX-bounds.left)*s.width/bounds.width))),
          y:Math.round(Math.max(0,Math.min(s.height,(e.clientY-bounds.top)*s.height/bounds.height)))};
}
$('#draw-box').onclick=()=>{P.frameMode=!P.frameMode;P.pageDrag=null;renderPreview();};
$('#page-image').draggable=false;
$('#preview').onpointerdown=e=>{
  if(!canFrame()||e.button!==0)return;
  const b=$('#page-image').getBoundingClientRect();
  if(e.clientX<b.left||e.clientX>b.right||e.clientY<b.top||e.clientY>b.bottom)return;
  const point=pagePoint(e),tolerance=deck().structure.width/b.width*10;let hit=null,corner=null;
  for(const hint of [...P.hints].reverse().filter(h=>h.page===P.page)){const r=hint.box;for(const [name,x,y] of [['nw',r.x,r.y],['ne',r.x+r.w,r.y],['sw',r.x,r.y+r.h],['se',r.x+r.w,r.y+r.h]])if(Math.hypot(point.x-x,point.y-y)<=tolerance){hit=hint;corner=name;break;}if(hit)break;if(point.x>=r.x&&point.x<=r.x+r.w&&point.y>=r.y&&point.y<=r.y+r.h){hit=hint;break;}}
  if(!hit&&!P.frameMode)return;
  e.preventDefault();P.pageDrag={start:point,page:P.page,version:deck().current_version_id,id:hit?.id,old:hit?{...hit.box}:null,corner};$('#preview').setPointerCapture(e.pointerId);
};
$('#preview').onpointermove=e=>{
  const drag=P.pageDrag;if(!drag)return;
  const q=pagePoint(e);drag.box=dragBox(drag,q);renderPreview();
};
$('#preview').onpointerup=e=>{
  const drag=P.pageDrag;if(!drag)return;P.pageDrag=null;
  if(!canFrame()||drag.page!==P.page||drag.version!==deck().current_version_id)return renderPreview();
  const q=pagePoint(e),box=dragBox(drag,q);
  const s=deck().structure,b=$('#page-image').getBoundingClientRect();
  if(box.w/s.width*b.width<5||box.h/s.height*b.height<5)return renderPreview();
  if(drag.id&&JSON.stringify(box)===JSON.stringify(drag.old))return renderPreview();
  if(!approveInvalidation())return renderPreview();
  action(async()=>{
    if(drag.id){P.hints.find(h=>h.id===drag.id).box=box;P.hintsKey='';await edited();renderPreview();notice('框选已调整，请核对后重新确认。');return;}
    if(P.hints.length>=100)throw Error('最多 100 个框选，请先删除不需要的框。');
    const hint={id:crypto.randomUUID(),page:P.page,box,note:''};P.hints.push(hint);P.hintsKey='';
    let scope=[];try{scope=selected();}catch{}
    if(!scope.includes(P.page)){P.selectedPages.add(P.page);scopeDisplay();}
    await edited();renderPreview();
    $('#hint-'+hint.id)?.querySelector('textarea')?.focus({preventScroll:true});
    notice('框选已加入。填写这处要求，或在上方我的修改要求中统一说明。');
  });
};
$('#preview').onpointercancel=()=>{P.pageDrag=null;renderPreview();};
$('#proposal').oninput=()=>{P.dirty=true;buttons();saveDraft();};
$('#proposal').onchange=()=>{P.invalidating=edited().catch(e=>notice(e.message,true));};
function dragBox(drag,q){const a=drag.start,r=drag.old,s=deck().structure;if(drag.id&&!drag.corner)return {x:Math.max(0,Math.min(s.width-r.w,r.x+q.x-a.x)),y:Math.max(0,Math.min(s.height-r.h,r.y+q.y-a.y)),w:r.w,h:r.h};const anchor=drag.corner?{x:drag.corner.includes('w')?r.x+r.w:r.x,y:drag.corner.includes('n')?r.y+r.h:r.y}:a;return {x:Math.min(anchor.x,q.x),y:Math.min(anchor.y,q.y),w:Math.abs(anchor.x-q.x),h:Math.abs(anchor.y-q.y)};}
render();
