"""Small, conservative OOXML editor. Untouched ZIP member payloads stay identical.

No python-pptx save roundtrip: themes, notes, extensions and relationships are
retained. Only text node bytes, an instance's blip reference and its rels change.
"""
from pathlib import Path, PurePosixPath
import io, zipfile, re, math, hashlib, posixpath
from collections.abc import MutableMapping
import copy, shutil
from xml.sax.saxutils import escape
from lxml import etree as ET
from PIL import Image

NS={'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'rel':'http://schemas.openxmlformats.org/package/2006/relationships'}
LIMIT_UPLOAD=512*1024*1024
LIMIT_EXPANDED=2*1024*1024*1024
LIMIT_MEMBER=256*1024*1024
LIMIT_XML=16*1024*1024
MAX_SLIDES=300

class ZipParts(MutableMapping):
    """Immutable ZIP backing plus changed parts; never retain the whole deck."""
    def __init__(self,path,names): self.path=path; self.names=dict.fromkeys(names); self.edits={}; self.removed=set()
    def __getitem__(self,name):
        if name in self.removed: raise KeyError(name)
        if name in self.edits: return self.edits[name]
        if name not in self.names: raise KeyError(name)
        with zipfile.ZipFile(self.path) as z: return z.read(name)
    def __setitem__(self,name,value): self.removed.discard(name); self.edits[name]=value
    def __delitem__(self,name):
        if name not in self: raise KeyError(name)
        self.removed.add(name); self.edits.pop(name,None)
    def __contains__(self,name): return name not in self.removed and (name in self.edits or name in self.names)
    def __iter__(self): return iter(n for n in dict.fromkeys([*self.names,*self.edits]) if n not in self.removed)
    def __len__(self): return sum(1 for _ in self)
    def copy(self): return dict(self.items())
    def sha(self,name):
        if name in self.edits: return digest(self.edits[name])
        h=hashlib.sha256()
        with zipfile.ZipFile(self.path) as z,z.open(name) as source:
            for chunk in iter(lambda:source.read(1024*1024),b''): h.update(chunk)
        return h.hexdigest()

def xml(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper(): raise ValueError('PPT XML 不允许 DTD / 实体')
    return ET.fromstring(raw,ET.XMLParser(resolve_entities=False,no_network=True,load_dtd=False))

def digest(raw): return hashlib.sha256(raw).hexdigest()

class Package:
    def __init__(self,path):
        self.path=Path(path)
        if self.path.stat().st_size>LIMIT_UPLOAD: raise ValueError('PPTX 超过 512 MiB 上限')
        with zipfile.ZipFile(self.path) as z:
            infos=z.infolist(); names=[i.filename for i in infos]
            if len(names)!=len(set(names)) or len(names)>10000: raise ValueError('重复成员或 ZIP 文件数超过 10000')
            total=0
            for i in infos:
                n=i.filename; q=PurePosixPath(n)
                if '\\' in n or ':' in n or q.is_absolute() or '..' in q.parts or n.startswith('/') or i.flag_bits&1 or (i.external_attr>>16)&0o170000==0o120000:
                    raise ValueError('PPTX 含危险路径、符号链接或加密成员')
                total+=i.file_size
                if i.file_size>LIMIT_MEMBER or total>LIMIT_EXPANDED: raise ValueError('PPTX 解压规模超过限制（单文件 256 MiB，总计 2 GiB）')
                if n.endswith(('.xml','.rels')) and i.file_size>LIMIT_XML: raise ValueError('PPTX 单个 XML 成员超过 16 MiB')
            if any('vbaproject' in n.lower() for n in names): raise ValueError('不支持含宏的文件，请另存为无宏 PPTX')
            self.parts=ZipParts(self.path,names); self.infos=infos
        for n in self.parts:
            if n.endswith(('.xml','.rels')): xml(self.parts[n])
        p=xml(self.parts['ppt/presentation.xml']); size=p.find('p:sldSz',NS)
        self.width=int(size.get('cx')); self.height=int(size.get('cy'))
        rel=self.rels('ppt/presentation.xml')
        self.slides=[rel[e.get('{'+NS['r']+'}id')] for e in p.findall('p:sldIdLst/p:sldId',NS)]
        if not 1<=len(self.slides)<=MAX_SLIDES: raise ValueError('仅支持 1 至 300 页 PPTX')
        if min(self.width,self.height)<=0: raise ValueError('页面尺寸无效')

    def relpath(self,part):
        p=PurePosixPath(part); return str(p.parent/'_rels'/(p.name+'.rels'))

    def rels(self,part):
        path=self.relpath(part)
        if path not in self.parts: return {}
        out={}
        for e in xml(self.parts[path]):
            if e.get('TargetMode')=='External': continue
            target=e.get('Target','')
            target=posixpath.normpath(posixpath.join(posixpath.dirname(part),target)) if not target.startswith('/') else target.lstrip('/')
            if target.startswith('../') or target not in self.parts: raise ValueError('PPTX 关系目标无效：'+target)
            out[e.get('Id')]=target
        return out

    def inspect(self):
        pages=[]
        for number,part in enumerate(self.slides,1):
            root=xml(self.parts[part]); rel=self.rels(part); objects=[]
            tree=root.find('p:cSld/p:spTree',NS)
            def walk(parent,group=False):
                for s in parent:
                    tag=ET.QName(s).localname
                    if tag=='AlternateContent':
                        # Choice/fallback render as one object. Do not silently
                        # drop ink/extensions, nor treat a fallback as editable.
                        nv=next((e for e in s.iter() if ET.QName(e).localname=='cNvPr'),None)
                        off=s.find('.//a:off',NS);ext=s.find('.//a:ext',NS)
                        box={k:int(e.get(a)) for k,e,a in [('x',off,'x'),('y',off,'y'),('w',ext,'cx'),('h',ext,'cy')]} if off is not None and ext is not None else None
                        objects.append(dict(id=nv.get('id') if nv is not None else 'extension-'+str(len(objects)),name=nv.get('name','') if nv is not None else '',
                            z=len(objects),kind='alternateContent',box=box,grouped=group,rotation=0,flipH=False,flipV=False,
                            supported=False,issue='Office Choice/Fallback 扩展对象，只作保守候选，需视觉确认',paragraphs=[],text='',cells=[]))
                        continue
                    if tag not in ('sp','pic','graphicFrame','grpSp','cxnSp','contentPart'): continue
                    nv=s.find('.//p:cNvPr',NS); oid=nv.get('id') if nv is not None else str(len(objects)+1)
                    xf=s.find('p:spPr/a:xfrm',NS)
                    if xf is None: xf=s.find('p:xfrm',NS)
                    off=xf.find('a:off',NS) if xf is not None else None
                    ext=xf.find('a:ext',NS) if xf is not None else None
                    box={k:int(e.get(a)) for k,e,a in [('x',off,'x'),('y',off,'y'),('w',ext,'cx'),('h',ext,'cy')]} if off is not None and ext is not None else None
                    obj={'id':oid,'name':nv.get('name','') if nv is not None else '', 'z':len(objects),'kind':tag,'box':box,'grouped':group,
                         'rotation':int(xf.get('rot',0))/60000 if xf is not None else 0,
                         'flipH':xf is not None and xf.get('flipH') in ('1','true'),'flipV':xf is not None and xf.get('flipV') in ('1','true')}
                    obj['supported']=bool(box and not group and tag in ('sp','pic','graphicFrame'))
                    obj['paragraphs']=[''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in s.findall('p:txBody/a:p',NS)]
                    obj['text']='\n'.join(obj['paragraphs']); obj['cells']=[]
                    table=s.find('.//a:tbl',NS)
                    if table is not None and box:
                        widths=[int(c.get('w')) for c in table.findall('a:tblGrid/a:gridCol',NS)]; y=box['y']
                        for ri,row in enumerate(table.findall('a:tr',NS)):
                            h=int(row.get('h')); x=box['x']
                            for ci,cell in enumerate(row.findall('a:tc',NS)):
                                cb={'x':x,'y':y,'w':widths[ci],'h':h}; x+=widths[ci]
                                paras=[''.join(t.text or '' for t in p.findall('.//a:t',NS)) for p in cell.findall('a:txBody/a:p',NS)]
                                obj['cells'].append({'row':ri,'col':ci,'paragraphs':paras,'text':'\n'.join(paras),'box':cb,
                                                     'supported':not any(cell.get(k) not in (None,'0','1' if k in ('gridSpan','rowSpan') else '0') for k in ('gridSpan','rowSpan','hMerge','vMerge'))})
                            y+=h
                        obj['kind']='table'
                        if obj['rotation'] or obj['flipH'] or obj['flipV']: obj['supported']=False; obj['issue']='旋转或翻转的表格暂不编辑'
                    elif tag=='graphicFrame': obj['supported']=False; obj['issue']='图表 / SmartArt / 嵌入对象暂不编辑'
                    if tag=='pic':
                        blip=s.find('p:blipFill/a:blip',NS); rid=blip.get('{'+NS['r']+'}embed') if blip is not None else None
                        media=rel.get(rid); obj.update(media=media,rid=rid)
                        crop=s.find('p:blipFill/a:srcRect',NS)
                        obj['crop']={k:int(crop.get(k,0))/100000 if crop is not None else 0 for k in ('l','t','r','b')}
                        geom=s.find('p:spPr/a:prstGeom',NS)
                        fill=s.find('p:blipFill/a:stretch/a:fillRect',NS)
                        if fill is not None and any(int(fill.get(k,0))!=0 for k in ('l','t','r','b')): obj['supported']=False; obj['issue']='非默认填充矩形暂不编辑'
                        if (geom is not None and geom.get('prst')!='rect') or s.find('p:spPr/a:custGeom',NS) is not None or s.find('p:spPr/a:scene3d',NS) is not None or s.find('p:spPr/a:sp3d',NS) is not None or s.find('p:blipFill/a:tile',NS) is not None:
                            obj['supported']=False; obj['issue']='非矩形、平铺或 3D 图片暂不编辑'
                        try:
                            with Image.open(io.BytesIO(self.parts[media])) as im:
                                if im.width*im.height>40_000_000: raise ValueError('图片超过 4000 万像素')
                                obj.update(pixels=[im.width,im.height],media_sha256=digest(self.parts[media]))
                                if im.format not in ('PNG','JPEG'): obj['supported']=False; obj['issue']='暂只支持 PNG / JPG 图片对象'
                        except (KeyError,TypeError,OSError): obj['supported']=False; obj['issue']='缺少内嵌位图或图片格式不支持'
                        c=obj['crop']
                        if any(v<0 or v>=1 for v in c.values()) or c['l']+c['r']>=1 or c['t']+c['b']>=1: obj['supported']=False; obj['issue']='图片裁剪包含外扩或无效范围'
                    if group or not box: obj['supported']=False; obj['issue']='组合对象或继承布局坐标，需人工确认'
                    objects.append(obj)
                    if tag=='grpSp': walk(s,True)
            walk(tree)
            for o in objects:
                if o['kind']=='pic' and o['box']:
                    o['overlays']=[v['id'] for v in objects if v['z']>o['z'] and v['box'] and intersects(o['box'],v['box'])]
            pages.append({'number':number,'part':part,'objects':objects})
        return {'width':self.width,'height':self.height,'pages':pages,'fidelity':'仅补丁修改 OOXML；未改 ZIP 成员逐字节保留，不承诺所有 Office 扩展均可正确显示'}

    def object(self,page,oid):
        return next(o for o in self.inspect()['pages'][page-1]['objects'] if o['id']==str(oid))

    def shape(self,page,oid):
        root=xml(self.parts[self.slides[page-1]])
        for s in root.findall('.//p:sp',NS)+root.findall('.//p:pic',NS)+root.findall('.//p:graphicFrame',NS):
            n=s.find('.//p:cNvPr',NS)
            if n is not None and n.get('id')==str(oid): return root,s
        raise ValueError('对象不存在')

    def text_replace(self,item):
        page=item['page']; obj=self.object(page,item['object_id'])
        if not obj['supported'] or obj['kind'] not in ('sp','table'): raise ValueError('该对象不支持直接改字')
        root,s=self.shape(page,item['object_id']); body=s.find('p:txBody',NS)
        if obj['kind']=='table':
            row,col=item['cell']; cell=next(c for c in obj['cells'] if c['row']==row and c['col']==col)
            if not cell['supported']: raise ValueError('暂不支持合并单元格')
            body=s.findall('.//a:tr',NS)[row].findall('a:tc',NS)[col].find('a:txBody',NS)
        paras=body.findall('a:p',NS); old=item['old']; new=item['new']
        if not old or old==new or '\n' in old or '\n' in new: raise ValueError('需提供不同的单段原文字与目标文字')
        matches=[]
        for p in paras:
            # Fields and explicit linebreaks must not be rewritten as ordinary runs.
            if p.find('a:fld',NS) is not None or p.find('a:br',NS) is not None: continue
            ts=p.findall('a:r/a:t',NS); text=''.join(t.text or '' for t in ts)
            for m in re.finditer(re.escape(old),text): matches.append((ts,m.start(),m.end()))
        if len(matches)!=1: raise ValueError('原文字未找到或同一对象多次出现，需缩小到唯一目标')
        ts,start,end=matches[0]; remaining=new; pos=0
        for t in ts:
            v=t.text or ''; a=max(0,start-pos); b=min(len(v),end-pos)
            if a<b:
                last=pos+len(v)>=end
                n=len(remaining) if last else min(b-a,len(remaining))
                t.text=v[:a]+remaining[:n]+v[b:]; remaining=remaining[n:]
            pos+=len(v)
        # Locate original text node order in raw XML; only replace changed node
        # contents. Preserve every namespace, extension and run property verbatim.
        part=self.slides[page-1]; original=xml(self.parts[part]); before=original.findall('.//a:t',NS); after=root.findall('.//a:t',NS)
        edits={i:(a.text or '') for i,(b,a) in enumerate(zip(before,after)) if b.text!=a.text}
        pat=rb'<a:t\b[^>]*?/\s*>|<a:t\b[^>]*>.*?</a:t\s*>'
        spans=list(re.finditer(pat,self.parts[part],re.S))
        # Select only DrawingML text nodes, using source offsets from lxml's
        # lexical namespace declarations; standard PPTX uses a:t. Reject unusual
        # serializations rather than touching an unrelated extension's t node.
        if len(spans)!=len(before): raise ValueError('非标准文本命名空间或扩展节点，暂不编辑')
        raw=self.parts[part]
        for i in sorted(edits,reverse=True):
            m=spans[i]; fragment=m.group(); opening=fragment[:fragment.index(b'>')+1]
            if opening.rstrip().endswith(b'/>'): opening=re.sub(rb'/\s*>$',b'>',opening)
            if edits[i]!=edits[i].strip() and b'xml:space=' not in opening: opening=opening[:-1]+b' xml:space="preserve">'
            raw=raw[:m.start()]+opening+escape(edits[i]).encode('utf-8')+b'</a:t>'+raw[m.end():]
        self.parts[part]=raw

    def image_replace(self,item,png_bytes):
        page=item['page']; obj=self.object(page,item['object_id'])
        if not obj['supported'] or obj['kind']!='pic': raise ValueError('该图片对象暂不支持局部修改')
        part=self.slides[page-1]; root,s=self.shape(page,item['object_id'])
        newmedia='ppt/media/localedit_'+digest(png_bytes)+'.png'; self.parts[newmedia]=png_bytes
        rp=self.relpath(part); relroot=xml(self.parts[rp]); ids={e.get('Id') for e in relroot}; rid='rIdLocal'+digest(png_bytes)[:12]
        while rid in ids: rid+='x'
        entry=ET.Element('{'+NS['rel']+'}Relationship',Id=rid,Type=NS['r']+'/image',Target='../media/'+PurePosixPath(newmedia).name)
        # Clone only this instance's relationship, leaving all shared media and
        # other uses of its old rId untouched.
        raw=self.parts[part]; blip=s.find('p:blipFill/a:blip',NS)
        old=blip.get('{'+NS['r']+'}embed'); blip.set('{'+NS['r']+'}embed',rid)
        original_nodes=xml(raw).findall('.//a:blip',NS); index=next(i for i,b in enumerate(original_nodes) if b.get('{'+NS['r']+'}embed')==old and b.getparent().getparent().find('.//p:cNvPr',NS).get('id')==obj['id'])
        spans=list(re.finditer(rb'<a:blip\b[^>]*>',raw)); m=spans[index]
        frag,n=re.subn(rb'(\br:embed\s*=\s*["\'])'+re.escape(old.encode())+rb'(["\'])',lambda x:x.group(1)+rid.encode()+x.group(2),m.group(),count=1)
        if n!=1: raise ValueError('图片引用序列化不支持')
        self.parts[part]=raw[:m.start()]+frag+raw[m.end():]
        relroot.append(entry); self.parts[rp]=ET.tostring(relroot,encoding='UTF-8',xml_declaration=True)
        ct=xml(self.parts['[Content_Types].xml']); ns=ET.QName(ct).namespace
        if not any(e.get('Extension')=='png' for e in ct):
            ct.append(ET.Element('{'+ns+'}Default',Extension='png',ContentType='image/png'))
            self.parts['[Content_Types].xml']=ET.tostring(ct,encoding='UTF-8',xml_declaration=True)

    def save(self,path):
        path=Path(path)
        if path.resolve()==self.path.resolve(): raise ValueError('禁止覆盖输入 PPTX，请另存新文件')
        with zipfile.ZipFile(self.path) as original,zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
            seen=set()
            for info in self.infos:
                if info.filename not in self.parts: continue
                if info.filename in self.parts.edits: z.writestr(copy.copy(info),self.parts[info.filename])
                else:
                    with original.open(info.filename) as src,z.open(copy.copy(info),'w') as dst: shutil.copyfileobj(src,dst,1024*1024)
                seen.add(info.filename)
            for n in self.parts:
                if n not in seen: z.writestr(n,self.parts[n])
        check=Package(path)
        if check.slides!=self.slides: raise ValueError('导出页序变化')
        return check

def intersects(a,b):
    return a['x']<b['x']+b['w'] and b['x']<a['x']+a['w'] and a['y']<b['y']+b['h'] and b['y']<a['y']+a['h']

def source_to_page(obj,x,y,inverse=False):
    b=obj['box']; pw,ph=obj['pixels']; c=obj['crop']; angle=math.radians(obj['rotation']); co=math.cos(angle); si=math.sin(angle)
    if inverse:
        dx=x-(b['x']+b['w']/2); dy=y-(b['y']+b['h']/2)
        u=(dx*co+dy*si)/b['w']+.5; v=(-dx*si+dy*co)/b['h']+.5
        if obj['flipH']: u=1-u
        if obj['flipV']: v=1-v
        return ((c['l']+u*(1-c['l']-c['r']))*pw,(c['t']+v*(1-c['t']-c['b']))*ph)
    u=(x/pw-c['l'])/(1-c['l']-c['r']); v=(y/ph-c['t'])/(1-c['t']-c['b'])
    if obj['flipH']: u=1-u
    if obj['flipV']: v=1-v
    dx=(u-.5)*b['w']; dy=(v-.5)*b['h']
    return (b['x']+b['w']/2+dx*co-dy*si,b['y']+b['h']/2+dx*si+dy*co)

def image_rect(obj,r):
    import core
    core.validate_rect(r,*obj['pixels']); w,h=obj['pixels']; c=obj['crop']
    if r['x']<c['l']*w or r['y']<c['t']*h or r['x']+r['w']>(1-c['r'])*w or r['y']+r['h']>(1-c['b'])*h: raise ValueError('源像素选区超出页面实际显示的裁剪范围')
    pts=[source_to_page(obj,x,y) for x,y in [(r['x'],r['y']),(r['x']+r['w'],r['y']),(r['x']+r['w'],r['y']+r['h']),(r['x'],r['y']+r['h'])]]
    return [[float(x),float(y)] for x,y in pts]

def pages(value,count):
    if not isinstance(value,str) or not value.strip(): raise ValueError('请输入页码，如 3,5,8-12')
    out=set()
    for part in value.split(','):
        if not re.fullmatch(r'\s*\d+(?:\s*-\s*\d+)?\s*',part): raise ValueError('页码格式错误，请用英文逗号和短横线')
        bounds=[int(x) for x in part.split('-')]; a=bounds[0]; b=bounds[-1]
        if not 1<=a<=b<=count: raise ValueError('页码越界或范围倒序')
        out.update(range(a,b+1))
    return sorted(out)
