"""PPT invariants on isolated data. Synthetic image patches are NOT model proof."""
from pathlib import Path
from unittest.mock import patch
import unittest, tempfile, io, json, copy, zipfile, math
import numpy as np
from PIL import Image,ImageDraw
import core, ppt_workflow as ppt, ppt_render
from ppt_package import Package, pages, image_rect, source_to_page, NS
from ppt_fixtures import make_deck, submit_mock_plan

class PptPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='ppt-package-'); cls.path=make_deck(Path(cls.temp.name)/'fixture.pptx')
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()
    def test_page_range_validation(self):
        self.assertEqual(pages('3,5,1-2,2',7),[1,2,3,5])
        for text in ['', '0','8','2-1','1,,3','3;5','1.5','-1','1-99999999']:
            with self.subTest(text=text),self.assertRaises(ValueError): pages(text,7)
    def test_real_structure_mixed_table_and_shared_resource(self):
        p=Package(self.path); s=p.inspect()['pages']
        self.assertEqual(s[1]['objects'][0]['kind'],'table')
        self.assertEqual([o['kind'] for o in s[3]['objects']],['pic','sp'])
        self.assertEqual(p.object(3,'2')['media'],p.object(4,'2')['media'])
        self.assertEqual(p.object(3,'2')['media'],p.object(5,'2')['media'])
        self.assertEqual(p.object(4,'2')['overlays'],['3'])
    def test_cross_run_text_preserves_run_properties_and_other_bytes(self):
        p=Package(self.path); original=p.parts.copy(); part=p.slides[0]
        from ppt_package import xml
        before=xml(p.parts[part]).findall('.//a:rPr',NS)
        p.text_replace(dict(page=1,object_id='2',method='native',old='2025',new='2026',cell=None))
        after=xml(p.parts[part]).findall('.//a:rPr',NS)
        from lxml import etree
        self.assertEqual([etree.tostring(t) for t in before],[etree.tostring(t) for t in after])
        self.assertEqual([t.text for t in xml(p.parts[part]).findall('.//a:t',NS)][:3],['Plan ','20','26'])
        self.assertEqual([n for n,b in original.items() if b!=p.parts[n]],[part])
    def test_table_cell_only_and_text_remains_editable(self):
        p=Package(self.path); p.text_replace(dict(page=2,object_id='2',method='native',old='2025',new='2026',cell=[1,0]));o=p.object(2,'2')
        self.assertEqual([c['text'] for c in o['cells']],['Year','Value','2026','123'])
        self.assertEqual(o['kind'],'table')
    def test_repeated_text_is_not_guessed(self):
        with self.assertRaises(ValueError): Package(self.path).text_replace(dict(page=6,object_id='2',old='2025',new='2026',cell=None))
    def test_geometry_roundtrip_crop_scale_rotation_flip(self):
        o=Package(self.path).object(5,'2')
        for flip in (False,True):
            o['flipH']=flip; o['flipV']=flip
            for x,y in [(200,120),(500,320),(900,500)]:
                px,py=source_to_page(o,x,y); sx,sy=source_to_page(o,px,py,True)
                self.assertAlmostEqual(sx,x,places=7);self.assertAlmostEqual(sy,y,places=7)
        self.assertEqual(len(image_rect(o,dict(x=450,y=300,w=100,h=80))),4)
        with self.assertRaises(ValueError): image_rect(o,dict(x=0,y=0,w=50,h=50))
    def test_image_instance_clone_preserves_shared_media(self):
        p=Package(self.path); before=p.parts.copy(); old=p.object(3,'2'); im=Image.open(io.BytesIO(p.parts[old['media']])).convert('RGBA');ImageDraw.Draw(im).rectangle((450,300,460,310),fill='red');b=io.BytesIO();im.save(b,format='PNG')
        p.image_replace(dict(page=3,object_id='2'),b.getvalue())
        self.assertNotEqual(p.object(3,'2')['media'],old['media']);self.assertEqual(p.parts[old['media']],before[old['media']]);self.assertEqual(p.object(4,'2')['media'],old['media']);self.assertEqual(p.parts[p.slides[4]],before[p.slides[4]])
        for k in ('box','rotation','crop','z','flipH','flipV'): self.assertEqual(p.object(3,'2')[k],old[k])
        out=Path(self.temp.name)/'instance.pptx'; p.save(out); Package(out)
    def test_zip_traversal_entity_duplicate_and_expansion_rejected(self):
        original=Package(self.path)
        for name,raw in [('../escape',b'x'),('evil.xml',b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///sensitive">]><x>&e;</x>'),('ppt/presentation.xml',b'<bad/>'),('huge.bin',b'0'*(65*1024*1024))]:
            with self.subTest(name=name):
                out=Path(self.temp.name)/'bad.pptx'
                with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                    for n,b in original.parts.items(): z.writestr(n,b)
                    z.writestr(name,raw)
                with patch('ppt_package.LIMIT_MEMBER',64*1024*1024),self.assertRaises(ValueError): Package(out)

@unittest.skipUnless(ppt_render.available()['available'],'需要本机 PowerPoint，不能伪造渲染验收')
class PptWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory(prefix='ppt-fixture-'); p=make_deck(Path(cls.fixture.name)/'input.pptx');cls.raw=p.read_bytes()
        cls.render=ppt_render.render(p,Path(cls.fixture.name)/'render')
    @classmethod
    def tearDownClass(cls): cls.fixture.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ppt-tests-');self.old=core.DATA;core.DATA=Path(self.tmp.name);core.init()
        self.did=core.ident();f=ppt.folder('decks',self.did);f.mkdir(parents=True);p=f/'original.pptx';p.write_bytes(self.raw);self.pack=Package(p)
        render=copy.deepcopy(self.render)
        for page in render['pages']:
            out=f/f"slide-{page['number']}.png";out.write_bytes(Path(page['path']).read_bytes());page.update(path=str(out),url=core.fileurl(out),thumbnail_url=core.fileurl(out))
        self.vid=core.ident();self.d=dict(id=self.did,name='隔离测试.pptx',status='ready',structure=self.pack.inspect(),original_path=str(p),original_sha256=core.sha(p),current_version_id=self.vid,original_version_id=self.vid,revision=0,active_plan=None)
        self.v=dict(id=self.vid,deck_id=self.did,path=str(p),url=core.fileurl(p),sha256=core.sha(p),render=render,label='原始文件',report=None)
        with core.tx() as c: core.put(c,'ppt_deck',self.d);core.put(c,'ppt_version',self.v)
    def tearDown(self): core.DATA=self.old;self.tmp.cleanup()
    def native(self,page=1,old='2025',new='2026',**kw): return dict(page=page,object_id='2',method='native',old=old,new=new,cell=None,note='更新年份',evidence='隔离样例原生对象唯一匹配',**kw)
    def image(self,page=3,rect=None): return dict(page=page,object_id='2',method='image',old='测试色块',new='新色块',note='程序模拟补丁，用于验证像素约束，不是模型结果',evidence='隔离样例源像素坐标',source_rect=rect or dict(x=450,y=300,w=180,h=100))
    def plan(self,items):
        ns=sorted({i['page'] for i in items});j=ppt.create_analysis(dict(deck_id=self.did,pages=','.join(map(str,ns)),overall='隔离测试要求'))
        claimed=ppt.claim(j['id']);f=Path(j['folder'])/'plan-test.json';core.dump(f,dict(job_id=j['id'],lease=claimed['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=items));return submit_mock_plan(j['id'],claimed['lease'],f)
    def execute(self,plan):
        ids=[i['id'] for i in plan['items'] if i['method']!='confirm'];plan=ppt.confirm(dict(job_id=plan['id'],plan_revision=plan['plan_revision'],item_ids=ids));job=ppt.create_execution(dict(job_id=plan['id'],plan_revision=plan['plan_revision']));return ppt.claim(job['id'])
    def synthetic_image(self,result):
        from alignment import normalize
        t=result['image_task'];t=core.claim(t['id']) if t['status']=='waiting' else t
        ref=core.prepare(t['id'],t['lease'])[0];im=Image.open(ref['path']).convert('RGBA');r=ref['allowed'];rr=ref['reference'];x=r['x']-rr['x'];y=r['y']-rr['y'];ImageDraw.Draw(im).rectangle((x+10,y+10,x+22,y+22),fill=(25,90+t['attempt'],220,255));raw=Path(t['folder'])/'synthetic-not-model.png';core.png(raw,im)
        job=next(j for j in ppt.state()['jobs'] if j['kind']=='execution' and j['status']=='running' and any(i.get('image_task_id')==t['id'] for i in j['items']))
        recorded=ppt.record_model(job['id'],job['lease'],result['item']['id'],raw)
        norm=normalize(t['id'],t['lease'],1,recorded['model_path'],False);manifest=Path(t['folder'])/'synthetic-manifest.json'
        core.dump(manifest,dict(task_id=t['id'],lease=t['lease'],patches=[dict(number=1,path=norm['path'],alignment=norm['alignment'],reviewed=True,review_note='程序模拟结果，非真实模型编辑')]))
        completed=core.compose(t['id'],t['lease'],manifest);self.assertEqual(completed['status'],'completed',completed.get('error'));return completed
    def test_native_and_table_full_export_reopen(self):
        table=self.native(page=2);table['cell']=[1,0];job=self.execute(self.plan([self.native(),table]));a=ppt.next_item(job['id'],job['lease']);self.assertEqual(a['item']['status'],'completed',a)
        b=ppt.next_item(job['id'],job['lease']);self.assertEqual(b['item']['status'],'completed',b)
        self.assertTrue(ppt.next_item(job['id'],job['lease'])['finished']);v=ppt.publish(job['id'],job['lease']);out=Package(v['path']);self.assertEqual(out.object(1,'2')['text'],'Plan 2026');self.assertEqual(out.object(2,'2')['cells'][2]['text'],'2026');self.assertFalse(v['report']['partial']);self.assertEqual(core.sha(self.d['original_path']),self.d['original_sha256'])
        with self.assertRaises(ValueError): ppt.publish(job['id'],job['lease'])
    def test_stale_import_preview_is_audited_not_used_as_acceptance_baseline(self):
        job=self.execute(self.plan([self.native()]))
        old=core.read('ppt_job',job['id'])['working_render']['pages'][6]['path']
        with Image.open(old) as source: im=source.convert('RGBA')
        im.putpixel((5,5),(255,0,0,255));core.png(old,im)
        result=ppt.next_item(job['id'],job['lease']);self.assertEqual(result['item']['status'],'completed',result)
        evidence=result['item']['validation']['render_baseline']
        self.assertTrue(evidence['repeat_exact']);self.assertEqual(evidence['historical_preview_differences'],[dict(page=7,changed_pixels=1)])
        v=ppt.publish(job['id'],job['lease']);self.assertNotEqual(v['before_render']['pages'][6]['path'],old)
    def test_unstable_fresh_baseline_and_candidate_unexpected_pixels_are_rejected(self):
        real=ppt_render.render_pair
        def noisy(before,after,folder):
            pair=real(before,after,folder); p=pair[self.noise_target]['pages'][6]['path']
            with Image.open(p) as source: im=source.convert('RGBA')
            im.putpixel((5,5),(255,0,0,255));core.png(p,im);return pair
        job=self.execute(self.plan([self.native()]))
        for target,error in [('before_check','基准重复渲染不稳定'),('after','未修改的第 7 页渲染变化')]:
            self.noise_target=target
            with patch('ppt_render.render_pair',side_effect=noisy): result=ppt.next_item(job['id'],job['lease'])
            self.assertEqual(result['item']['status'],'failed');self.assertIn(error,result['item']['error'])
            self.assertEqual(core.read('ppt_job',job['id'])['working_sha256'],self.v['sha256'])
            if target=='before_check':
                ppt.cancel(job['id']);job=ppt.claim(ppt.resume(job['id'])['id'])
    def test_two_completed_images_resume_across_other_page_change_without_regeneration(self):
        job=self.execute(self.plan([self.image(),self.image(page=5)]));children=[]
        for _ in range(2):
            result=ppt.next_item(job['id'],job['lease']);children.append(self.synthetic_image(result)['id'])
            ppt.fail_item(job['id'],job['lease'],result['item']['id'],'模拟历史预览校验失败，子任务已完成')
        ppt.publish(job['id'],job['lease']);job=ppt.claim(ppt.resume(job['id'])['id'])
        for tid in children:
            result=ppt.next_item(job['id'],job['lease']);self.assertEqual(result['image_task']['id'],tid)
            done=ppt.accept_image(job['id'],job['lease'],result['item']['id']);self.assertEqual(done['item']['status'],'completed',done)
            self.assertEqual(core.read('task',tid)['attempt'],1)
        v=ppt.publish(job['id'],job['lease']);self.assertEqual(len(v['report']['successful']),2)
        audit=v['report']['successful'][1]['validation']['image_validation'];self.assertNotEqual(audit['model_basis_ppt_sha256'],audit['applied_basis_ppt_sha256'])
    def test_render_page_count_and_hash_binding_fail_closed(self):
        from ppt_validate import render_differences,verify_pair
        job=self.execute(self.plan([self.native()]))
        pair=ppt_render.render_pair(self.v['path'],self.v['path'],Path(job['folder'])/'hash-test')
        for damaged in [dict(pair['after'],pages=pair['after']['pages'][:-1]),dict(pair['after'],pages=pair['after']['pages'][::-1])]:
            with self.assertRaisesRegex(ValueError,'页数|页序'):render_differences(pair['before'],damaged)
        pair['before_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'哈希'):verify_pair(self.v['path'],self.v['path'],self.v['render'],pair,self.native())
    def test_completed_image_cannot_rebase_over_a_changed_target_slide(self):
        job=self.execute(self.plan([self.image()]));result=ppt.next_item(job['id'],job['lease']);child=self.synthetic_image(result)
        pack=Package(self.v['path']);part=pack.slides[2];pack.parts[part]=pack.parts[part].replace(b'<p:cSld>',b'<p:cSld name="changed">')
        path=Path(job['folder'])/'changed-target.pptx';pack.save(path)
        with core.tx() as c:
            live=core.get(c,'ppt_job',job['id']);live.update(working_path=str(path),working_sha256=core.sha(path));ppt.save(c,live)
        rejected=ppt.accept_image(job['id'],job['lease'],result['item']['id']);self.assertEqual(rejected['item']['status'],'failed');self.assertIn('目标页或图片对象已变化',rejected['item']['error'])
        self.assertEqual(core.read('task',child['id'])['attempt'],1)
    def test_sampling_guard_does_not_allow_source_image_outside_changes(self):
        job=self.execute(self.plan([self.image()]));result=ppt.next_item(job['id'],job['lease']);child=self.synthetic_image(result)
        v=core.read('version',child['output_version_id'])
        with Image.open(v['path']) as source:im=source.convert('RGBA')
        im.putpixel((0,0),(255,0,0,255));core.png(v['path'],im)
        with core.tx() as c:v['sha256']=core.sha(v['path']);core.put(c,'version',v)
        rejected=ppt.accept_image(job['id'],job['lease'],result['item']['id']);self.assertEqual(rejected['item']['status'],'failed');self.assertIn('图片允许范围外像素变化必须为 0',rejected['item']['error'])
    def test_selective_resume_preserves_unrelated_failure_and_completed_items(self):
        table=self.native(page=2);table['cell']=[1,0];job=self.execute(self.plan([self.native(),table]))
        with patch('ppt_render.render_pair',side_effect=ValueError('模拟暂时失败')):
            ppt.next_item(job['id'],job['lease']);ppt.next_item(job['id'],job['lease'])
        ppt.publish(job['id'],job['lease']);before=core.read('ppt_job',job['id']);first,second=before['items']
        for bad in ([],['unknown'],[second['id'],second['id']],[42]):
            with self.subTest(ids=bad),self.assertRaises(ValueError):ppt.resume(job['id'],bad)
        job=ppt.claim(ppt.resume(job['id'],[second['id']])['id']);result=ppt.next_item(job['id'],job['lease'])
        self.assertEqual(result['item']['page'],2);self.assertEqual(result['item']['status'],'completed');ppt.publish(job['id'],job['lease'])
        self.assertEqual(core.read('ppt_job',job['id'])['items'][0],first)
        with self.assertRaises(ValueError):ppt.resume(job['id'],[second['id']])
    def test_free_proposal_accepts_all_pages_without_fixed_page_format(self):
        proposal='全篇年份更新；第3页删右下角二维码，标题保持。第1页的年份改为2026。'
        j=ppt.create_analysis(dict(deck_id=self.did,pages='',proposal=proposal,base_version_id=self.vid))
        self.assertEqual(j['pages'],list(range(1,8)));self.assertEqual(j['proposal'],proposal);self.assertEqual(j['hints'],[])
        self.assertTrue(all(j['requirements'].values()))
    def test_page_boxes_are_validated_hints_and_bound_to_request_hash(self):
        hint=dict(page=3,box=dict(x=3_000_000,y=2_000_000,w=2_000_000,h=1_000_000),note='仅修改框内年份')
        body=dict(deck_id=self.did,pages='3',hints=[hint],base_version_id=self.vid)
        j=ppt.create_analysis(body);self.assertEqual(j['hints'][0]['candidate_objects'],[dict(object_id='2',kind='pic',supported=True)]);self.assertEqual(j['hints'][0]['box'],hint['box'])
        ppt.cancel(j['id']);moved=copy.deepcopy(body);moved['hints'][0]['box']['x']+=10;j2=ppt.create_analysis(moved);self.assertNotEqual(j['request_hash'],j2['request_hash'])
    def test_invalid_page_boxes_and_stale_base_rejected(self):
        for hint in [dict(page=2,box=dict(x=0,y=0,w=100,h=100),note='wrong scope'),dict(page=1,box=dict(x=-1,y=0,w=100,h=100)),dict(page=1,box=dict(x=1.5,y=0,w=100,h=100)),dict(page=1,box=dict(x=0,y=0,w=99_000_000,h=100))]:
            with self.subTest(hint=hint),self.assertRaises(ValueError):ppt.create_analysis(dict(deck_id=self.did,pages='1',proposal='修改框内目标',hints=[hint]))
        with self.assertRaisesRegex(ValueError,'基准'):ppt.create_analysis(dict(deck_id=self.did,pages='1',proposal='改标题',base_version_id='old'))
    def test_free_proposal_plan_preserves_uninvolved_pages_and_exports_complete(self):
        j=ppt.create_analysis(dict(deck_id=self.did,proposal='第1页把2025改为2026，其他页不变'));claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'free-plan.json'
        unchanged=[dict(page=n,reason='用户方案不涉及该页，保留原样') for n in range(2,8)]
        core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=[self.native()],unchanged_pages=unchanged))
        plan=submit_mock_plan(j['id'],claim['lease'],manifest);self.assertEqual(plan['unchanged_pages'],unchanged);job=self.execute(plan);out=ppt.next_item(job['id'],job['lease']);self.assertEqual(out['item']['status'],'completed',out)
        v=ppt.publish(job['id'],job['lease']);self.assertFalse(v['report']['partial']);self.assertEqual(len(v['report']['successful']),1);self.assertEqual(v['report']['unchanged_pages'],unchanged)
    def test_unchanged_page_coverage_cannot_hide_or_duplicate_modified_page(self):
        j=ppt.create_analysis(dict(deck_id=self.did,pages='1-2',proposal='修改第1页年份'));claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'bad-plan.json'
        for unchanged in [[],[dict(page=1,reason='overlaps')],[dict(page=2,reason='keep'),dict(page=2,reason='duplicate')]]:
            core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=[self.native()],unchanged_pages=unchanged))
            with self.assertRaises(ValueError):submit_mock_plan(j['id'],claim['lease'],manifest)
    def test_all_unchanged_plan_has_no_executable_modifications(self):
        j=ppt.create_analysis(dict(deck_id=self.did,pages='7',proposal='如果有2025就改为2026'));claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'empty-plan.json'
        core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=[],unchanged_pages=[dict(page=7,reason='本页没有2025，保留原样')]))
        plan=submit_mock_plan(j['id'],claim['lease'],manifest);self.assertEqual(plan['items'],[])
        with self.assertRaises(ValueError):ppt.create_execution(dict(job_id=plan['id'],plan_revision=plan['plan_revision']))
    def test_approval_invalidated_by_item_and_requirement_changes(self):
        plan=self.plan([self.native()]);plan=ppt.confirm(dict(job_id=plan['id'],plan_revision=1,item_ids=[plan['items'][0]['id']]));self.assertTrue(plan['items'][0]['confirmed']);edited=ppt.update_plan(dict(job_id=plan['id'],plan_revision=1,item_id=plan['items'][0]['id'],changes={'new':'2027'}));self.assertIsNone(edited['items'][0]['confirmed'])
        with self.assertRaises(ValueError): ppt.create_execution(dict(job_id=plan['id'],plan_revision=1))
        with self.assertRaises(ValueError): ppt.create_execution(dict(job_id=plan['id'],plan_revision=2))
        ppt.invalidate(self.did)
        with self.assertRaises(ValueError): ppt.confirm(dict(job_id=plan['id'],plan_revision=2,item_ids=[plan['items'][0]['id']]))
    def test_cancel_old_lease_and_resume_skip_completed_items(self):
        table=self.native(page=2);table['cell']=[1,0];job=self.execute(self.plan([self.native(),table]));first=ppt.next_item(job['id'],job['lease']);self.assertEqual(first['item']['status'],'completed',first);ppt.cancel(job['id'])
        with self.assertRaises(ValueError): ppt.next_item(job['id'],job['lease'])
        resumed=ppt.claim(ppt.resume(job['id'])['id']);self.assertNotEqual(resumed['lease'],job['lease']);self.assertEqual(resumed['items'][0]['attempt'],1);second=ppt.next_item(resumed['id'],resumed['lease']);self.assertEqual(second['item']['page'],2);self.assertEqual(second['item']['status'],'completed',second);v=ppt.publish(resumed['id'],resumed['lease']);self.assertEqual(len(v['report']['successful']),2)
    def test_length_increase_needs_adjustment_partial_export(self):
        job=self.execute(self.plan([self.native(new='2026 '+('long text '*80))]));result=ppt.next_item(job['id'],job['lease']);self.assertEqual(result['item']['status'],'failed');self.assertRegex(result['item']['error'],'换行|溢出');v=ppt.publish(job['id'],job['lease']);self.assertTrue(v['report']['partial']);self.assertEqual(v['sha256'],self.v['sha256']);self.assertEqual(core.read('ppt_job',job['id'])['status'],'failed');self.assertEqual(v['label'],'未完成（0 项成功）')
    def test_transient_failure_retry_preserves_success_and_reopens(self):
        table=self.native(page=2);table['cell']=[1,0];job=self.execute(self.plan([self.native(),table]));ppt.next_item(job['id'],job['lease'])
        with patch('ppt_render.render_pair',side_effect=ValueError('模拟渲染依赖暂时失败')): failed=ppt.next_item(job['id'],job['lease'])
        self.assertEqual(failed['item']['status'],'failed');v=ppt.publish(job['id'],job['lease']);self.assertEqual(len(v['report']['successful']),1)
        resumed=ppt.claim(ppt.resume(job['id'])['id']);done=ppt.next_item(resumed['id'],resumed['lease']);self.assertEqual(done['item']['page'],2);out=ppt.publish(resumed['id'],resumed['lease']);self.assertFalse(out['report']['partial']);self.assertEqual(core.read('ppt_job',job['id'])['items'][0]['attempt'],1)
    def test_image_crop_rotation_and_shared_instance_only_outside_zero(self):
        job=self.execute(self.plan([self.image(page=5)]));result=ppt.next_item(job['id'],job['lease']);child=self.synthetic_image(result);self.assertEqual(child['validation']['outside_changed_pixels'],0)
        applied=ppt.accept_image(job['id'],job['lease'],result['item']['id']);self.assertEqual(applied['item']['status'],'completed',applied)
        out=ppt.publish(job['id'],job['lease']);pack=Package(out['path']);self.assertEqual(pack.parts[pack.slides[2]],self.pack.parts[self.pack.slides[2]]);self.assertEqual(pack.object(3,'2')['media'],self.pack.object(4,'2')['media']);self.assertNotEqual(pack.object(5,'2')['media'],pack.object(4,'2')['media'])
        self.assertFalse(core.state()['images']);self.assertFalse(core.state()['tasks'])
    def test_image_resume_reuses_completed_child_without_regeneration(self):
        job=self.execute(self.plan([self.image()]));result=ppt.next_item(job['id'],job['lease']);child=self.synthetic_image(result);ppt.cancel(job['id']);resumed=ppt.claim(ppt.resume(job['id'])['id']);again=ppt.next_item(resumed['id'],resumed['lease']);self.assertEqual(again['image_task']['id'],child['id']);self.assertEqual(again['image_task']['status'],'completed');done=ppt.accept_image(resumed['id'],resumed['lease'],again['item']['id']);self.assertEqual(done['item']['status'],'completed');self.assertEqual(core.read('task',child['id'])['attempt'],1)
    def test_failed_image_releases_queue_and_allows_explicit_retry(self):
        job=self.execute(self.plan([self.image(),self.image(page=5)]));first=ppt.next_item(job['id'],job['lease']);ppt.fail_item(job['id'],job['lease'],first['item']['id'],'模拟工具不可用')
        self.assertEqual(core.read('task',first['image_task']['id'])['status'],'cancelled')
        second=ppt.next_item(job['id'],job['lease']);self.assertEqual(second['item']['page'],5);self.assertNotEqual(second['image_task']['id'],first['image_task']['id']);ppt.cancel(job['id'])
        resumed=ppt.claim(ppt.resume(job['id'])['id']);again=ppt.next_item(resumed['id'],resumed['lease']);self.assertEqual(again['image_task']['id'],first['image_task']['id']);retry=ppt.retry_image(resumed['id'],resumed['lease'],again['item']['id']);self.assertEqual(retry['status'],'waiting')
    def test_single_image_retry_cannot_race_ppt_execution(self):
        data=io.BytesIO();Image.new('RGB',(500,300),'white').save(data,format='PNG');im=core.upload(data.getvalue(),'普通图片.png');t=core.create_task(dict(image_id=im['id'],base_version_id=im['current_version_id'],request_key=core.ident(),regions=[dict(x=10,y=10,w=40,h=40,note='修改')]))
        t=core.claim(t['id']);core.fail(t['id'],t['lease'],'模拟失败');job=self.execute(self.plan([self.native()]))
        with self.assertRaisesRegex(ValueError,'PPT'): core.retry(t['id'])
    def test_same_image_overlap_serial_bases(self):
        job=self.execute(self.plan([self.image(),self.image(rect=dict(x=460,y=310,w=180,h=100))]));first=ppt.next_item(job['id'],job['lease']);self.synthetic_image(first);ppt.accept_image(job['id'],job['lease'],first['item']['id']);second=ppt.next_item(job['id'],job['lease']);self.assertNotEqual(first['item']['source_sha256'],second['item']['source_sha256']);self.synthetic_image(second);applied=ppt.accept_image(job['id'],job['lease'],second['item']['id']);self.assertEqual(applied['item']['status'],'completed',applied)
    def test_duplicate_submit_confirm_gate_and_version_competition(self):
        plan=self.plan([self.native()]);body=dict(job_id=plan['id'],plan_revision=1)
        with self.assertRaises(ValueError): ppt.create_execution(body)
        ppt.confirm(dict(**body,item_ids=[plan['items'][0]['id']]));one=ppt.create_execution(body);two=ppt.create_execution(body);self.assertEqual(one['id'],two['id']);job=ppt.claim(one['id']);ppt.cancel(job['id']);ppt.invalidate(self.did);new=ppt.create_analysis(dict(deck_id=self.did,pages='1',overall='新要求'))
        with self.assertRaises(ValueError): ppt.resume(job['id'])
        self.assertEqual(core.read('ppt_deck',self.did)['active_plan'],new['id'])
    def test_plan_not_bound_to_input_and_ambiguous_target_rejected(self):
        with self.assertRaises(ValueError): self.plan([self.native(page=6)])
    def test_missing_renderer_and_other_formats_are_explicit(self):
        with self.assertRaisesRegex(ValueError,'转换'): ppt.upload(b'x','bad.pdf')
        with patch('ppt_render.available',return_value={'available':False,'error':'缺少 PowerPoint 测试桩'}):
            did=core.ident();f=ppt.folder('decks',did);f.mkdir(parents=True);p=f/'original.pptx';p.write_bytes(self.raw)
            with core.tx() as c: core.put(c,'ppt_deck',dict(id=did,status='importing',original_path=str(p)))
            ppt.import_deck(did);d=core.read('ppt_deck',did);self.assertEqual(d['status'],'failed');self.assertIn('缺少 PowerPoint',d['error']);self.assertTrue(p.exists())

if __name__=='__main__':unittest.main()
