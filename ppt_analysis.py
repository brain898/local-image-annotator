"""Analysis provenance and structural checks. Never claims to verify model vision."""
import math
from pathlib import Path
import core
from PIL import Image
from ppt_package import intersects

PROTOCOL = 'composite-first-v1'


def object_text(obj):
    return obj['text'] or '\n'.join(c['text'] for c in obj.get('cells', []))


def bounds(obj):
    """Conservative rotated AABB; group/unknown coordinates remain uncertain."""
    b = obj.get('box')
    if not b or obj.get('grouped'): return None
    a = math.radians(obj.get('rotation', 0))
    w = abs(b['w']*math.cos(a)) + abs(b['h']*math.sin(a))
    h = abs(b['w']*math.sin(a)) + abs(b['h']*math.cos(a))
    return dict(x=b['x']+(b['w']-w)/2, y=b['y']+(b['h']-h)/2, w=w, h=h)


def upper_candidates(objects, target, polygon):
    xs, ys = zip(*polygon)
    region = dict(x=min(xs), y=min(ys), w=max(xs)-min(xs), h=max(ys)-min(ys))
    return [o for o in objects if o['z'] > target['z'] and
            (bounds(o) is None or intersects(region, bounds(o)))]


def attach_page(page, rendered, base_sha256):
    path = Path(rendered['path'])
    with Image.open(path) as im: size = list(im.size)
    page['preview_path'] = str(path)
    page['visual_reference'] = dict(role='full_slide_composite', path=str(path),
        sha256=core.sha(path), pixels=size, base_sha256=base_sha256,
        meaning='最终整页渲染，包含原生文字、形状与图片；唯一页面视觉依据')
    page['native_text_objects'] = [dict(object_id=o['id'], z=o['z'], text=object_text(o),
        box=o['box'], supported=o['supported']) for o in page['objects'] if o['text'] or o['kind']=='table']
    page['overlays_semantics'] = '相交候选，不证明覆盖或不透明；目标区域需另算候选并结合整页视觉判断'


def source_reference(obj, path):
    obj['source_reference'] = dict(role='single_picture_source', path=str(path),
        sha256=core.sha(path), media_sha256=obj.get('media_sha256'),
        meaning='单个图片对象原图，不含上层文字或形状，不能代替整页预览')


def validate_reviews(context, manifest, pack):
    """Checks provenance/coverage, not whether a model actually opened a PNG."""
    if manifest.get('context_sha256') != context['_sha256']:
        raise ValueError('计划缺少当前 context_sha256，先运行 context 并读取整页预览')
    rows = manifest.get('page_reviews')
    if not isinstance(rows, list): raise ValueError('需逐页提供 page_reviews，模型观察不是程序视觉验证')
    pages = {p['number']: p for p in context['pages']}
    seen = set()
    for r in rows:
        if not isinstance(r, dict) or type(r.get('page')) is not int or r['page'] not in pages or r['page'] in seen:
            raise ValueError('page_reviews 必须无重复覆盖全部所选页面')
        ref = pages[r['page']]['visual_reference']
        if r.get('preview_sha256') != ref['sha256'] or core.sha(ref['path']) != ref['sha256']:
            raise ValueError('整页预览绑定不一致或文件已变化，请重新 context')
        if not isinstance(r.get('observation'), str) or not r['observation'].strip() or len(r['observation'])>6000:
            raise ValueError('每页需描述实际可见内容及与底图差异，不能只填已查看')
        seen.add(r['page'])
    if seen != set(pages): raise ValueError('page_reviews 必须覆盖全部所选页面')
    for page in pages.values():
        for obj in page['objects']:
            ref=obj.get('source_reference')
            if ref and core.sha(ref['path'])!=ref['sha256']:
                raise ValueError('单图片原图已变化，请重新 context；不能复用旧视觉定位')
    # Independently re-read structure. Context files are hashed by the workflow.
    live = pack.inspect()
    if any(p['objects'] != [{k:v for k,v in o.items() if k not in
            ('source_path','source_reference','ocr')} for o in pages[p['number']]['objects']]
           for p in live['pages'] if p['number'] in pages):
        raise ValueError('上下文对象结构与 PPT 不一致，请重新 context')
    return rows


def validate_target(pack, item, raw):
    if item['method'] == 'confirm': return None
    review = raw.get('target_review')
    if not isinstance(review, dict): raise ValueError('执行项需 target_review，说明实际可见目标与上层对象')
    if not isinstance(review.get('visible_text'), str) or not isinstance(review.get('reason'), str) or not review['reason'].strip():
        raise ValueError('target_review 需 visible_text 和具体 reason')
    if len(review['visible_text'])>6000 or len(review['reason'])>6000: raise ValueError('目标视觉说明过长')
    objects = pack.inspect()['pages'][item['page']-1]['objects']
    target = next(o for o in objects if o['id']==item['object_id'])
    candidates = upper_candidates(objects, target, item['page_polygon'])
    decisions = review.get('upper_objects')
    if not isinstance(decisions, list): raise ValueError('需逐个说明 upper_objects（无候选时为空列表）')
    ids = [r.get('object_id') for r in decisions if isinstance(r, dict)]
    if len(ids)!=len(decisions) or len(set(ids))!=len(ids) or set(ids)!={o['id'] for o in candidates}:
        raise ValueError('上层候选遗漏、多报或重复；按目标区域、z 与旋转边界重新核对')
    for r in decisions:
        if r.get('relation') not in ('separate_from_target','visible_with_target','obscures_target','uncertain') or not isinstance(r.get('reason'),str) or not r['reason'].strip():
            raise ValueError('上层候选需具体关系和原因，相交不能当作完全遮挡')
        if r['relation'] in ('obscures_target','uncertain'):
            raise ValueError('目标被遮挡或无法可靠定位，请改选可见对象或提交 confirm')
    if item['method']=='image':
        ref = review.get('source_sha256')
        if ref != target.get('media_sha256'):
            raise ValueError('图片 target_review.source_sha256 需绑定 PPT 内原图片资源哈希')
        text = ''.join(review['visible_text'].split())
        if item['old'] and text != ''.join(item['old'].split()):
            raise ValueError('图片原内容与整页可见目标描述不一致，请定位可见对象或 confirm')
        # A known native text match is a structural conflict, not an occlusion proof.
        if text and any(text in ''.join(object_text(o).split()) for o in candidates if object_text(o)):
            raise ValueError('可见文字匹配上层原生对象，不能按底图图片改字；请定位原生对象或 confirm')
    else:
        native_text=target['text']
        if target['kind']=='table':
            native_text=next(c['text'] for c in target['cells'] if [c['row'],c['col']]==item['cell'])
        text=''.join(review['visible_text'].split())
        if not text or text not in ''.join(native_text.split()) or ''.join(item['old'].split()) not in text:
            raise ValueError('可见文字与所选原生对象或单元格不一致，请重新定位或 confirm')
    return dict(review, program_checks=dict(candidate_ids=ids, source_binding=item['method']=='image',
        visual_judgment='model_report_only_not_program_verified'))
