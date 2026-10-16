"""Persistent local task protocol. SQLite serializes both web and worker writes."""
from pathlib import Path
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib, io, json, os, sqlite3, uuid, math
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('ANNOTATOR_DATA', ROOT / 'data')).resolve()

def now(): return datetime.now(timezone.utc).isoformat()
def ident(): return uuid.uuid4().hex
def dump(p, obj):
    p=Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    q=p.with_name(p.name+'.'+ident()+'.tmp')
    q.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(q,p)
def png(p, im):
    p=Path(p); p.parent.mkdir(parents=True,exist_ok=True)
    q=p.with_name(p.name+'.'+ident()+'.tmp'); im.save(q,format='PNG'); os.replace(q,p)
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def init():
    DATA.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(DATA/'store.sqlite')) as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, body TEXT, PRIMARY KEY(kind,id))')
        c.commit()
@contextmanager
def tx():
    init(); c=sqlite3.connect(DATA/'store.sqlite',timeout=30)
    try:
        c.execute('BEGIN IMMEDIATE'); yield c; c.commit()
    except: c.rollback(); raise
    finally: c.close()
def get(c,kind,id):
    row=c.execute('SELECT body FROM records WHERE kind=? AND id=?',(kind,id)).fetchone()
    if row is None: raise ValueError('记录不存在')
    return json.loads(row[0])
def put(c,kind,obj): c.execute('INSERT OR REPLACE INTO records VALUES (?,?,?)',(kind,obj['id'],json.dumps(obj,ensure_ascii=False)))
def allof(c,kind): return [json.loads(r[0]) for r in c.execute('SELECT body FROM records WHERE kind=? ORDER BY rowid DESC',(kind,))]
def read(kind,id):
    with tx() as c: return get(c,kind,id)
def state():
    with tx() as c: return {k:allof(c,k[:-1]) for k in ('images','versions','tasks','drafts')}
def save_draft(body):
    with tx() as c:
        image=get(c,'image',body['image_id']); base=get(c,'version',body['base_version_id'])
        if base['image_id']!=image['id']: raise ValueError('草稿版本不属于图片')
        if base['id']!=image['current_version_id']: raise ValueError('草稿基准已变化，旧草稿不能覆盖新版本')
        regions=body.get('regions',[])
        if len(regions)>20: raise ValueError('最多 20 个选区')
        for r in regions:
            validate_rect(r,image['width'],image['height'])
            region_extra(r)
            if len(str(r.get('note','')))>6000: raise ValueError('每条批注最多 6000 字')
        draft={'id':image['id'],'base_version_id':base['id'],'regions':regions,'overall':str(body.get('overall',''))[:10000],'updated_at':now()}
        put(c,'draft',draft); return draft
def fileurl(p): return '/data/'+Path(p).relative_to(DATA).as_posix()
def upload(raw,name):
    with Image.open(io.BytesIO(raw)) as src:
        if src.format not in ('PNG','JPEG'): raise ValueError('仅支持 PNG / JPG')
        if src.width*src.height>40_000_000: raise ValueError('图片超过 4000 万像素')
        im=ImageOps.exif_transpose(src).convert('RGBA')
    iid,vid=ident(),ident(); folder=DATA/'images'/iid
    folder.mkdir(parents=True); (folder/'original.upload').write_bytes(raw)
    p=folder/(vid+'.png'); png(p,im)
    v={'id':vid,'image_id':iid,'task_id':None,'base_version_id':None,'path':str(p),'url':fileurl(p),'created_at':now(),'label':'原图','sha256':sha(p)}
    image={'id':iid,'name':Path(name).name[:180],'width':im.width,'height':im.height,'original_version_id':vid,'current_version_id':vid,'revision':0,'created_at':now()}
    with tx() as c: put(c,'version',v); put(c,'image',image)
    return image
def validate_rect(r,w,h):
    if any(type(r.get(k)) is not int for k in ('x','y','w','h')): raise ValueError('选区坐标必须为原图整数')
    if r['x']<0 or r['y']<0 or r['w']<2 or r['h']<2 or r['x']+r['w']>w or r['y']+r['h']>h: raise ValueError('选区越界或过小')
def region_mask(r):
    """Binary native-resolution mask; stroke points are relative to the region."""
    m=Image.new('L',(r['w'],r['h']),255 if 'mask' not in r else 0)
    if 'mask' in r:
        strokes=r['mask'].get('strokes',[])
        if r['mask'].get('kind')!='brush' or not isinstance(strokes,list) or len(strokes)>1000: raise ValueError('无效画笔遮罩')
        d=ImageDraw.Draw(m)
        for stroke in strokes:
            width=stroke.get('width'); points=stroke.get('points'); mode=stroke.get('mode')
            if type(width) is not int or not 1<=width<=1000 or mode not in ('paint','erase') or not isinstance(points,list) or not 1<=len(points)<=10000: raise ValueError('无效遮罩笔划')
            pts=[]
            for pt in points:
                if not isinstance(pt,list) or len(pt)!=2 or any(type(v) is not int for v in pt) or not 0<=pt[0]<r['w'] or not 0<=pt[1]<r['h']: raise ValueError('遮罩笔划超出选区')
                pts.append(tuple(pt))
            color=255 if mode=='paint' else 0
            samples=list(pts[:1])
            for (ax,ay),(bx,by) in zip(pts,pts[1:]):
                steps=max(abs(bx-ax),abs(by-ay),1)
                samples.extend((math.floor(ax+(bx-ax)*i/steps+.5),math.floor(ay+(by-ay)*i/steps+.5)) for i in range(1,steps+1))
            for x,y in samples:
                left=x-width//2;top=y-width//2
                d.rectangle((left,top,left+width-1,top+width-1),fill=color)

    return m

def region_extra(r):
    extra={}
    margin=r.get('reference_margin',96)
    if type(margin) is not int or not 0<=margin<=1500: raise ValueError('参考边距须为 0 至 1500 原图像素')
    
    if 'reference_margin' in r:extra['reference_margin']=margin
    if 'mask' in r:
        if not np.asarray(region_mask(r)).any():raise ValueError('画笔遮罩为空，请绘制允许修改的范围')
        extra['mask']=r['mask']
    for k in ('original_text','target_text','ocr_text'):
        if k in r:extra[k]=str(r[k])[:6000]
    if 'ocr_polygon' in r:extra['ocr_polygon']=r['ocr_polygon']
    if 'ocr_bounds' in r:extra['ocr_bounds']=r['ocr_bounds']
    return extra

def create_task(body):
    with tx() as c:
        image=get(c,'image',body['image_id']); base=get(c,'version',body['base_version_id'])
        if base['image_id']!=image['id']: raise ValueError('基准版本不属于该图片')
        key=str(body.get('request_key',''))
        if not key or len(key)>100: raise ValueError('缺少提交幂等标识')
        for t in allof(c,'task'):
            if t['request_key']==key and t['image_id']==image['id']: return t
        if base['id']!=image['current_version_id']: raise ValueError('基准版本已变化，请重新加载')
        if any(t['status'] in ('waiting','running') for t in allof(c,'task')): raise ValueError('已有待发送或执行中的任务，请先完成或取消')
        regions=body.get('regions',[])
        if not 1<=len(regions)<=20: raise ValueError('请选择 1 至 20 个区域')
        clean=[]
        for i,r in enumerate(regions):
            validate_rect(r,image['width'],image['height'])
            note=str(r.get('note','')).strip()
            if not note or len(note)>6000: raise ValueError('每个选区需填写要求，最多 6000 字')
            clean.append({**{k:r[k] for k in ('x','y','w','h')},'number':i+1,'note':note,**region_extra(r)})
        for i,a in enumerate(clean):
            for b in clean[i+1:]:
                if a['x']<b['x']+b['w'] and b['x']<a['x']+a['w'] and a['y']<b['y']+b['h'] and b['y']<a['y']+a['h']: raise ValueError('选区重叠，请调整为互不重叠的修改范围')
        tid=ident(); folder=DATA/'tasks'/tid; folder.mkdir(parents=True)
        t={'id':tid,'image_id':image['id'],'base_version_id':base['id'],'base_revision':image['revision'],'base_path':base['path'],'base_sha256':sha(base['path']),'width':image['width'],'height':image['height'],'regions':clean,'overall':str(body.get('overall',''))[:10000],'request_key':key,'mode':body.get('mode','edit'),'status':'waiting','created_at':now(),'output_version_id':None,'error':None,'attempt':0,'folder':str(folder)}
        if t['mode'] not in ('edit','probe'): raise ValueError('未知任务模式')
        preview=Image.open(base['path']).convert('RGB'); d=ImageDraw.Draw(preview)
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',max(18,min(44,image['width']//40)))
        for r in clean:
            x,y,w,h=(r[k] for k in ('x','y','w','h')); d.rectangle((x,y,x+w-1,y+h-1),outline='#ff375f',width=3); d.rectangle((x,y,x+45,y+40),fill='#d92343'); d.text((x+8,y+3),str(r['number']),font=font,fill='white')
        for r in clean:
            if 'mask' in r:
                overlay=Image.new('RGB',(r['w'],r['h']),'#ff7391'); preview.paste(Image.blend(preview.crop((r['x'],r['y'],r['x']+r['w'],r['y']+r['h'])),overlay,.3),(r['x'],r['y']),region_mask(r))
        png(folder/'preview.png',preview); t['preview_url']=fileurl(folder/'preview.png')
        dump(folder/'task.json',t)
        md=f"# 图片批注任务 {tid}\n\n模式：{t['mode']}\n\n基准：{base['id']}\n\n基准文件：{base['path']}\n\n原尺寸：{image['width']} × {image['height']}\n\n整体要求：{t['overall']}\n"
        for r in clean: md+=f"\n## 选区 {r['number']}\n\n原图坐标：x={r['x']}, y={r['y']}, w={r['w']}, h={r['h']}，右下边界不包含。\n\n{r['note']}\n\n允许范围：{'画笔二值遮罩（笔划相对选区坐标；非整个矩形）' if 'mask' in r else '整个矩形'}；参考边距 {r.get('reference_margin',96)} 原图像素。\n原文字：{r.get('original_text','')}\n目标文字：{r.get('target_text','')}\n"
        (folder/'批注.md').write_text(md,encoding='utf-8'); put(c,'task',t); dump(folder/'state.json',t)
        return t
def update(c,t):
    put(c,'task',t); dump(Path(t['folder'])/'state.json',t)
    event={k:t.get(k) for k in ('id','status','attempt','error','output_version_id')};event['at']=now()
    with (Path(t['folder'])/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(event,ensure_ascii=False)+'\n')
def claim(tid):
    with tx() as c:
        t=get(c,'task',tid)
        if t['status']=='completed': return t
        if t['status']!='waiting': raise ValueError('任务不是待执行状态，不能重复领取')
        t.update(status='running',attempt=t['attempt']+1,lease=ident(),started_at=now(),error=None); update(c,t); return t
def checked(c,tid,lease):
    t=get(c,'task',tid)
    if t['status']!='running' or t.get('lease')!=lease: raise ValueError('任务已取消、重试或执行令牌过期')
    return t
def fail(tid,lease,error):
    with tx() as c:
        t=checked(c,tid,lease); t.update(status='failed',error=str(error)[:4000]); update(c,t); return t
def retry(tid):
    with tx() as c:
        t=get(c,'task',tid)
        if t['status'] not in ('failed','needs_adjustment','cancelled'): raise ValueError('仅失败、待调整或已取消任务可重试')
        image=get(c,'image',t['image_id'])
        if image['current_version_id']!=t['base_version_id'] or image['revision']!=t['base_revision']: raise ValueError('图片版本已变化，请基于当前版本创建新任务')
        if any(a['status'] in ('waiting','running') for a in allof(c,'task')): raise ValueError('已有活动任务')
        t.update(status='waiting',error=None,lease=None); update(c,t); return t
def cancel(tid):
    with tx() as c:
        t=get(c,'task',tid)
        if t['status'] not in ('waiting','running'): raise ValueError('任务已结束')
        t.update(status='cancelled',lease=None,error='用户取消'); update(c,t); return t
def restore(iid,vid):
    with tx() as c:
        image=get(c,'image',iid); v=get(c,'version',vid)
        if v['image_id']!=iid: raise ValueError('版本不属于图片')
        image.update(current_version_id=vid,revision=image['revision']+1); put(c,'image',image); return image
def receipt(tid,lease):
    with tx() as c:
        t=checked(c,tid,lease)
        if t['mode']!='probe': raise ValueError('图片任务不能以测试回执完成')
        result={'kind':'handoff_probe','task_id':tid,'received_regions':t['regions'],'received_overall':t['overall'],'message':'Codex 已读取任务并写回真实测试回执；未修改图片','at':now()}
        dump(Path(t['folder'])/'receipt.json',result); t.update(status='completed',completed_at=now(),receipt=result); update(c,t); return t
def prepare(tid,lease):
    with tx() as c: t=checked(c,tid,lease)
    im=Image.open(t['base_path']).convert('RGBA'); refs=[]
    for r in t['regions']:
        # Context is separate and always larger than the only writable rectangle.
        margin=r.get('reference_margin',96)
        rw=r['w']+margin*2; rh=r['h']+margin*2
        if 'reference_margin' not in r:rw=max(1024,((rw+15)//16)*16);rh=max(1024,((rh+15)//16)*16)
        if max(rw,rh)>3840: raise ValueError('参考裁图超过 3840 像素，请拆分选区后重试；工具不会缩小原图')
        x=max(0,min(r['x']-margin,im.width-rw)); y=max(0,min(r['y']-margin,im.height-rh))
        x2=min(im.width,x+rw); y2=min(im.height,y+rh)
        canvas=Image.new('RGBA',(rw,rh),'white'); canvas.paste(im.crop((x,y,x2,y2)),(0,0))
        p=Path(t['folder'])/f"reference-{r['number']}.png"; png(p,canvas)
        mask_path=Path(t['folder'])/f"mask-{r['number']}.png"; png(mask_path,region_mask(r))
        refs.append({'mask_path':str(mask_path),'mask_coordinates':'relative to allowed x,y; native resolution; white writable', 'number':r['number'],'path':str(p),'reference':{'x':x,'y':y,'w':rw,'h':rh},'padding':'参考图可有白色补边，未缩放原图','allowed':r})
    dump(Path(t['folder'])/'references.json',{'task_id':tid,'lease':lease,'references':refs}); return refs
def compose(tid,lease,manifest):
    with tx() as c:
        t=get(c,'task',tid)
        if t['status']=='completed': return t  # Exactly one published version per task.
        t=checked(c,tid,lease)
        try:
            if t['mode']!='edit': raise ValueError('接入探针不能发布图片')
            m=json.loads(Path(manifest).read_text(encoding='utf-8'))
            if m.get('task_id')!=tid or m.get('lease')!=lease: raise ValueError('输出清单任务或执行令牌不匹配')
            refs=json.loads((Path(t['folder'])/'references.json').read_text(encoding='utf-8'))
            if refs['lease']!=lease: raise ValueError('参考裁图已过期')
            patches=m.get('patches',[])
            if sorted(p['number'] for p in patches)!=list(range(1,len(t['regions'])+1)): raise ValueError('必须覆盖所有选区且不可重复')
            if sha(t['base_path'])!=t['base_sha256']: raise ValueError('基准文件校验失败')
            base=Image.open(t['base_path']).convert('RGBA'); result=base.copy(); quality=[]
            for p in patches:
                ref=refs['references'][p['number']-1]; r=ref['allowed']; rr=ref['reference']
                if p.get('reviewed') is not True or not str(p.get('review_note','')).strip(): raise ValueError('缺少尺寸、内容、排版、接缝人工视觉检查记录')
                proof=None
                if p.get('alignment'):
                    proof=json.loads(Path(p['alignment']).read_text(encoding='utf-8'))
                    if proof.get('task_id')!=tid or proof.get('lease')!=lease or proof.get('number')!=p['number'] or proof.get('accepted') is not True:raise ValueError('配准证据与当前任务不匹配')
                    if proof['output_sha256']!=sha(p['path']) or proof['source_sha256']!=sha(proof['source_path']) or proof['reference_sha256']!=sha(ref['path']):raise ValueError('配准证据文件哈希不匹配')
                edited=Image.open(p['path']).convert('RGBA')
                if edited.size!=(rr['w'],rr['h']): raise ValueError(f"选区 {p['number']} 返回尺寸 {edited.size} 与参考裁图不符，禁止强行缩放")
                before=Image.open(ref['path']).convert('RGBA'); a=np.asarray(before); b=np.asarray(edited)
                xx=r['x']-rr['x']; yy=r['y']-rr['y']
                outside=np.ones(a.shape[:2],bool); outside[yy:yy+r['h'],xx:xx+r['w']]=False
                # Reject shifted/relaid-out reference content; compression/model noise has tolerance.
                delta=np.max(np.abs(a.astype(np.int16)-b.astype(np.int16)),axis=2)
                changed=float(np.mean(delta[outside]>36)) if outside.any() else 0
                if changed>0.03: raise ValueError(f"选区 {p['number']} 参考背景发生较大变化 ({changed:.1%})，需重新生成或调整选区")
                patch=edited.crop((xx,yy,xx+r['w'],yy+r['h']))
                if np.array_equal(np.asarray(base.crop((r['x'],r['y'],r['x']+r['w'],r['y']+r['h']))),np.asarray(patch)): raise ValueError('选区没有变化，不能标记修改成功')
                local_before=np.asarray(base.crop((r['x'],r['y'],r['x']+r['w'],r['y']+r['h'])))
                if not np.any(np.any(local_before!=np.asarray(patch),axis=2)&(np.asarray(region_mask(r))>0)):raise ValueError('允许遮罩内没有变化，不能标记修改成功')
                result.paste(patch,(r['x'],r['y']),region_mask(r)); quality.append({'number':p['number'],'reference_changed_fraction':changed,'review_note':p['review_note'],'patch_reference_sha256':sha(p['path']),'model_source_sha256':proof['source_sha256'] if proof else sha(p['path']),'alignment':p.get('alignment')})
            mask=np.zeros((t['height'],t['width']),bool)
            for r in t['regions']: mask[r['y']:r['y']+r['h'],r['x']:r['x']+r['w']] |= np.asarray(region_mask(r))>0
            changed=np.any(np.asarray(base)!=np.asarray(result),axis=2)
            outside_count=int(np.count_nonzero(changed & ~mask))
            if outside_count: raise ValueError('范围外像素验证失败')
            vid=tid; output=DATA/'images'/t['image_id']/(vid+'.png'); png(output,result)
            saved=Image.open(output).convert('RGBA')
            if saved.size!=base.size or not np.array_equal(np.asarray(saved),np.asarray(result)): raise ValueError('PNG 落盘回读验证失败')
            validation={'task_id':tid,'base_version_id':t['base_version_id'],'outside_changed_pixels':outside_count,'inside_changed_pixels':int(np.count_nonzero(changed & mask)),'size':list(saved.size),'quality':quality,'at':now()}
            dump(Path(t['folder'])/'validation.json',validation)
            v={'id':vid,'image_id':t['image_id'],'task_id':tid,'base_version_id':t['base_version_id'],'path':str(output),'url':fileurl(output),'sha256':sha(output),'created_at':now(),'label':'局部修改 '+tid[:6],'validation':validation}
            put(c,'version',v); image=get(c,'image',t['image_id'])
            applied=image['current_version_id']==t['base_version_id'] and image['revision']==t['base_revision']
            if applied: image.update(current_version_id=vid,revision=image['revision']+1); put(c,'image',image)
            t.update(status='completed',output_version_id=vid,completed_at=now(),validation=validation,applied=applied); update(c,t)
        except Exception as e:
            t.update(status='needs_adjustment',error=str(e)); update(c,t)
        return t
