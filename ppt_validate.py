"""Separate native-text/layout and source-image invariants."""
from pathlib import Path
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from ppt_package import Package, digest, image_rect

PAGE_EDGE_GUARD_PIXELS=3

def page_change_audit(before,after,points):
    """Guard the entire rasterized polygon, including corners, by 3 pixels.

    A thick open stroke omits corner pixels. Dilation gives every polygon
    pixel the same bounded sampling neighborhood; it does not alter sources.
    """
    if before.shape!=after.shape: raise ValueError('输出渲染尺寸变化')
    region=Image.new('L',(before.shape[1],before.shape[0]));ImageDraw.Draw(region).polygon(points,fill=255)
    inside=np.asarray(region)!=0
    allowed=np.asarray(region.filter(ImageFilter.MaxFilter(PAGE_EDGE_GUARD_PIXELS*2+1)))!=0
    changed=np.any(after!=before,axis=2); guard=changed & allowed & ~inside
    outside=int(np.count_nonzero(changed & ~allowed))
    if outside: raise ValueError(f'页面修改范围外出现 {outside} 个变化像素（不含 {PAGE_EDGE_GUARD_PIXELS} 像素边缘采样保护），拒绝应用')
    if not np.any(changed & inside): raise ValueError('页面实际修改区域显示无变化，不能标为完成')
    return {'page_outside_changed_pixels':outside,'page_edge_guard_pixels':PAGE_EDGE_GUARD_PIXELS,
            'page_edge_guard_method':'polygon-square-dilation',
            'page_edge_guard_changed_pixels':int(np.count_nonzero(guard)),
            'page_edge_guard_max_channel_delta':int(np.abs(after.astype(np.int16)-before.astype(np.int16))[guard].max()) if guard.any() else 0}

def render_differences(before,after):
    """Exact raster audit, also rejects missing, duplicate or reordered pages."""
    bp=before['pages']; ap=after['pages']
    expected=list(range(1,len(bp)+1))
    if not bp or [p['number'] for p in bp]!=expected or [p['number'] for p in ap]!=expected:
        raise ValueError('渲染页数或页序不一致，拒绝应用')
    differences=[]
    for b,a in zip(bp,ap):
        with Image.open(b['path']) as bi,Image.open(a['path']) as ai:
            if bi.size!=ai.size: raise ValueError('对照页面渲染尺寸变化')
            count=int(np.count_nonzero(np.any(np.asarray(bi.convert('RGBA'))!=np.asarray(ai.convert('RGBA')),axis=2)))
        if count: differences.append({'page':b['number'],'changed_pixels':count})
    return differences

def verify_pair(before_path,after_path,historical,pair,item):
    import ppt_render
    if pair.get('protocol')!='fresh-before-after-before' or ppt_render.file_sha(before_path)!=pair['before_sha256'] or ppt_render.file_sha(after_path)!=pair['after_sha256']:
        raise ValueError('同批渲染与输入 PPT 哈希不匹配')
    if len(pair['before']['pages'])!=len(Package(before_path).slides): raise ValueError('对照渲染页数不完整')
    unstable=render_differences(pair['before'],pair['before_check'])
    if unstable: raise ValueError('PowerPoint 基准重复渲染不稳定，未应用修改：'+str(unstable))
    validation=verify(before_path,after_path,pair['before'],pair['after'],item)
    validation['render_baseline']={'protocol':pair['protocol'],'repeat_exact':True,'historical_preview_differences':render_differences(historical,pair['before'])}
    return validation

def verify(before_path,after_path,before_render,after_render,item):
    before=Package(before_path); after=Package(after_path)
    if len(before_render['pages'])!=len(before.slides) or len(after_render['pages'])!=len(after.slides): raise ValueError('渲染页数不完整')
    render_differences(before_render,after_render)
    touched=item['page']; part=before.slides[touched-1]
    allowed={part}
    if item['method']=='image': allowed.update({before.relpath(part),'[Content_Types].xml'})
    unexpected=[n for n in before.parts if n not in allowed and (n not in after.parts or before.parts.sha(n)!=after.parts.sha(n))]
    if unexpected: raise ValueError('未修改的 OOXML 成员变化：'+','.join(unexpected))
    unchanged=[]
    for b,a in zip(before_render['pages'],after_render['pages']):
        if b['number']!=touched:
            with Image.open(b['path']) as bi,Image.open(a['path']) as ai:
                if bi.size!=ai.size or not np.array_equal(np.asarray(bi),np.asarray(ai)): raise ValueError(f"未修改的第 {b['number']} 页渲染变化")
            unchanged.append(b['number'])
    obj=before.object(touched,item['object_id'])
    if item['method']=='native':
        key=obj['id']+(':'+':'.join(map(str,item['cell'])) if item.get('cell') is not None else '')
        b=before_render['pages'][touched-1]['metrics'].get(key); a=after_render['pages'][touched-1]['metrics'].get(key)
        if not a or not b: raise ValueError('缺少 PowerPoint 原生文本尺寸检查，待调整')
        if a['lines']>b['lines']: raise ValueError('替换后出现新增换行，待调整；未缩小字号')
        if a['w']>a['shape_w']-a['margin_x']+1 or a['h']>a['shape_h']-a['margin_y']+1: raise ValueError('替换后文字溢出，待调整；未应用该项')
        if item['new'] not in a['text']: raise ValueError('PowerPoint 重新打开后目标文字不匹配')
        bx=obj['box'] if not item.get('cell') else next(c['box'] for c in obj['cells'] if [c['row'],c['col']]==item['cell'])
        angle=math.radians(obj['rotation']); co=math.cos(angle); si=math.sin(angle); cx=bx['x']+bx['w']/2; cy=bx['y']+bx['h']/2
        polygon=[[cx+(x-cx)*co-(y-cy)*si,cy+(x-cx)*si+(y-cy)*co] for x,y in [(bx['x'],bx['y']),(bx['x']+bx['w'],bx['y']),(bx['x']+bx['w'],bx['y']+bx['h']),(bx['x'],bx['y']+bx['h'])]]
        # Higher objects intersecting the changed text could hide it. Preserve
        # the candidate for adjustment rather than silently declaring success.
        from ppt_package import intersects
        if any(o['z']>obj['z'] and o['box'] and intersects(bx,o['box']) for o in before.inspect()['pages'][touched-1]['objects']): raise ValueError('文字与上层对象相交，需检查遮挡后重新规划')
    else: polygon=image_rect(obj,item['source_rect'])
    with Image.open(before_render['pages'][touched-1]['path']) as bi,Image.open(after_render['pages'][touched-1]['path']) as ai:
        b=np.asarray(bi.convert('RGBA')); a=np.asarray(ai.convert('RGBA'))
        points=[(x/before.width*bi.width,y/before.height*bi.height) for x,y in polygon]
        audit=page_change_audit(b,a,points)
    return {'native_editable':item['method']=='native','unchanged_members_verified':True,'unchanged_pages':unchanged,
            **audit,'source_pixels_validated_separately':item['method']=='image'}
