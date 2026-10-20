"""Real composite rendering, program plan checks; model observations are test stubs."""
from pathlib import Path
import copy, json, tempfile, unittest
from unittest.mock import patch
import core, ppt_workflow as ppt, ppt_render, ppt_analysis
from ppt_package import Package
from ppt_fixtures import make_layered_deck, add_mock_analysis


@unittest.skipUnless(ppt_render.available()['available'], '需要真实 PowerPoint 渲染')
class PptAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture=tempfile.TemporaryDirectory(prefix='ppt-analysis-fixture-')
        cls.input=make_layered_deck(Path(cls.fixture.name)/'layers.pptx')
        cls.render=ppt_render.render(cls.input,Path(cls.fixture.name)/'render')

    @classmethod
    def tearDownClass(cls): cls.fixture.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ppt-analysis-');self.old=core.DATA;core.DATA=Path(self.tmp.name);core.init()
        self.did=core.ident();self.vid=core.ident();f=ppt.folder('decks',self.did);f.mkdir(parents=True)
        p=f/'original.pptx';p.write_bytes(self.input.read_bytes());self.pack=Package(p)
        rendered=copy.deepcopy(self.render)
        for r in rendered['pages']:
            out=f/f"slide-{r['number']}.png";out.write_bytes(Path(r['path']).read_bytes());r['path']=str(out)
        self.v=dict(id=self.vid,deck_id=self.did,path=str(p),sha256=core.sha(p),render=rendered,label='原文件')
        self.d=dict(id=self.did,name='layers.pptx',status='ready',structure=self.pack.inspect(),current_version_id=self.vid,
            original_version_id=self.vid,original_path=str(p),original_sha256=core.sha(p),revision=0,active_plan=None)
        with core.tx() as c: core.put(c,'ppt_deck',self.d);core.put(c,'ppt_version',self.v)

    def tearDown(self): core.DATA=self.old;self.tmp.cleanup()

    def prepare(self,page=1,method='native'):
        j=ppt.claim(ppt.create_analysis(dict(deck_id=self.did,pages=str(page),proposal='修改可见标题'))['id'])
        item=dict(page=page,object_id='4' if method=='native' else '2',method=method,
            old='NEW TITLE' if method=='native' else 'OLD TITLE',new='UPDATED TITLE',note='修改可见标题',evidence='隔离程序桩')
        if method=='image': item['source_rect']=dict(x=80,y=40,w=450,h=100)
        m=dict(job_id=j['id'],lease=j['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=[item])
        return j,add_mock_analysis(m)

    def submit(self,j,m):
        path=Path(j['folder'])/'plan.json';core.dump(path,m);return ppt.write_plan(j['id'],j['lease'],path)

    def test_context_prioritizes_typed_composite_and_native_text(self):
        j,m=self.prepare();ctx=json.loads((Path(j['folder'])/'context.json').read_text(encoding='utf-8'))
        self.assertEqual([r['role'] for r in ctx['visual_inputs']],['full_slide_composite','single_picture_source'])
        self.assertEqual(ctx['pages'][0]['native_text_objects'][0]['text'],'NEW TITLE')
        self.assertNotEqual(ctx['visual_inputs'][0]['sha256'],ctx['visual_inputs'][1]['sha256'])
        self.assertIn('相交候选',ctx['pages'][0]['overlays_semantics'])

    def test_upper_native_title_selected_and_stays_editable(self):
        j,m=self.prepare();plan=self.submit(j,m)
        item=plan['items'][0];self.assertEqual((item['object_id'],item['method']),('4','native'))
        pack=Package(j['base_path']);before=pack.parts.copy();pack.text_replace(item)
        self.assertEqual(pack.object(1,'4')['text'],'UPDATED TITLE')
        self.assertEqual(pack.parts[pack.object(1,'2')['media']],before[pack.object(1,'2')['media']])
        self.assertIn('model_report_only',item['target_review']['program_checks']['visual_judgment'])

    def test_image_only_title_is_not_excluded(self):
        j,m=self.prepare(2,'image');plan=self.submit(j,m)
        self.assertEqual(plan['items'][0]['method'],'image');self.assertEqual(plan['items'][0]['target_review']['upper_objects'],[])

    def test_intersection_without_occlusion_keeps_picture_path(self):
        j,m=self.prepare(3,'image');review=m['items'][0]['target_review']
        self.assertEqual([o['object_id'] for o in review['upper_objects']],['3'])
        review['upper_objects'][0].update(relation='visible_with_target',reason='透明框只有外围边线，整页旧标题字形仍完整可见')
        self.assertEqual(self.submit(j,m)['items'][0]['method'],'image')

    def test_missing_context_and_stale_context_or_preview_rejected(self):
        j,m=self.prepare();bad=copy.deepcopy(m);bad.pop('context_sha256')
        with self.assertRaisesRegex(ValueError,'context_sha256'): self.submit(j,bad)
        bad=copy.deepcopy(m);bad['page_reviews'][0]['preview_sha256']='source-hash'
        with self.assertRaisesRegex(ValueError,'整页预览'): self.submit(j,bad)
        path=Path(j['folder'])/'context.json';path.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'分析上下文'): self.submit(j,m)

    def test_all_selected_pages_need_composite_review(self):
        j,m=self.prepare();m['page_reviews']=[]
        with self.assertRaisesRegex(ValueError,'全部所选'): self.submit(j,m)

    def test_missing_target_review_and_omitted_candidates_rejected(self):
        j,m=self.prepare(1,'image');bad=copy.deepcopy(m);bad['items'][0].pop('target_review')
        with self.assertRaisesRegex(ValueError,'target_review'): self.submit(j,bad)
        m['items'][0]['target_review']['upper_objects']=[]
        with self.assertRaisesRegex(ValueError,'上层候选'): self.submit(j,m)

    def test_known_native_title_conflict_and_visible_text_mismatch_rejected(self):
        j,m=self.prepare(1,'image');item=m['items'][0];item['old']='NEW TITLE';item['target_review']['visible_text']='NEW TITLE'
        with self.assertRaisesRegex(ValueError,'上层原生对象'): self.submit(j,m)
        item['old']='OLD TITLE'
        with self.assertRaisesRegex(ValueError,'整页可见目标'): self.submit(j,m)

    def test_occluded_or_uncertain_target_requires_confirm(self):
        j,m=self.prepare(1,'image')
        for relation in ('obscures_target','uncertain'):
            m['items'][0]['target_review']['upper_objects'][0]['relation']=relation
            with self.assertRaisesRegex(ValueError,'confirm'): self.submit(j,m)
        m['items']=[dict(page=1,method='confirm',evidence='整页与底图不同，需定位上层新标题',question='请确认目标标题对象',note='修改标题')]
        self.assertEqual(self.submit(j,m)['items'][0]['method'],'confirm')

    def test_wrong_source_hash_cannot_submit(self):
        j,m=self.prepare(2,'image');m['items'][0]['target_review']['source_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'资源哈希'): self.submit(j,m)

    def test_locate_returns_geometry_not_visibility(self):
        j,m=self.prepare(1,'image');p=Path(j['folder'])/'target.json';core.dump(p,m['items'][0])
        r=ppt.locate(j['id'],j['lease'],p)
        self.assertEqual([o['id'] for o in r['upper_objects']],['3','4']);self.assertIn('不证明遮挡',r['meaning'])

    def test_editing_target_cannot_reuse_review_and_handoff_names_composite(self):
        j,m=self.prepare();plan=self.submit(j,m);i=plan['items'][0]
        with self.assertRaisesRegex(ValueError,'重新分析'):
            ppt.update_plan(dict(job_id=j['id'],plan_revision=1,item_id=i['id'],changes=dict(method='image',object_id='2',source_rect=dict(x=80,y=40,w=450,h=100))))
        plan=ppt.update_plan(dict(job_id=j['id'],plan_revision=1,item_id=i['id'],changes=dict(new='ANOTHER TITLE')))
        self.assertEqual(plan['items'][0]['object_id'],'4')
        ppt.invalidate(self.did);waiting=ppt.create_analysis(dict(deck_id=self.did,pages='1',proposal='更新标题'))
        from urllib.parse import unquote
        self.assertIn('full_slide_composite',unquote(ppt.handoff(waiting['id'])))

    def test_preview_mutation_and_source_file_mutation_fail_closed(self):
        j,m=self.prepare();path=Path(self.v['render']['pages'][0]['path']);path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'文件已变化'):self.submit(j,m)
        Path(j['base_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'基准文件哈希'):self.submit(j,m)

    def test_rotated_and_unknown_upper_geometry_are_conservative(self):
        target=dict(id='2',z=0)
        upper=dict(id='3',z=1,box=dict(x=10,y=10,w=100,h=2),rotation=45)
        unknown=dict(id='4',z=2,box=None)
        result=ppt_analysis.upper_candidates([target,upper,unknown],target,[[50,40],[60,40],[60,50],[50,50]])
        self.assertEqual([o['id'] for o in result],['3','4'])

    def test_alternate_content_is_one_unsupported_candidate(self):
        p=Package(self.input);part=p.slides[0]
        extra=b'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:p14="http://schemas.microsoft.com/office/powerpoint/2010/main"><mc:Choice Requires="p14"><p:contentPart><p14:cNvPr id="9" name="Ink"/></p:contentPart></mc:Choice><mc:Fallback><p:pic><p:nvPicPr><p:cNvPr id="9"/></p:nvPicPr></p:pic></mc:Fallback></mc:AlternateContent>'
        p.parts[part]=p.parts[part].replace(b'</p:spTree>',extra+b'</p:spTree>')
        objects=p.inspect()['pages'][0]['objects'];extension=objects[-1]
        self.assertEqual(extension['id'],'9');self.assertFalse(extension['supported']);self.assertIsNone(extension['box'])
        self.assertEqual(sum(o['id']=='9' for o in objects),1)
        self.assertEqual(ppt_analysis.upper_candidates(objects,objects[-2],[[0,0],[1,0],[1,1],[0,1]])[-1]['id'],'9')

    def test_native_execution_preserves_analysis_provenance_and_picture(self):
        j,m=self.prepare();plan=self.submit(j,m);item=plan['items'][0]
        ppt.confirm(dict(job_id=j['id'],plan_revision=1,item_ids=[item['id']]))
        execution=ppt.claim(ppt.create_execution(dict(job_id=j['id'],plan_revision=1))['id'])
        self.assertEqual(execution['analysis_checks'],plan['analysis_checks'])
        result=ppt.next_item(execution['id'],execution['lease'])
        self.assertEqual(result['item']['status'],'completed',result)
        out=ppt.publish(execution['id'],execution['lease']);pack=Package(out['path'])
        self.assertEqual(out['report']['page_reviews'],plan['page_reviews'])
        self.assertEqual(out['report']['analysis_checks']['visual_judgment'],'model_report_only_not_program_verified')
        self.assertEqual(pack.object(1,'4')['text'],'UPDATED TITLE')
        self.assertEqual(pack.parts[pack.object(1,'2')['media']],self.pack.parts[self.pack.object(1,'2')['media']])

    def test_old_lease_or_request_cannot_reuse_context(self):
        j,m=self.prepare();bad=copy.deepcopy(m);bad['request_hash']='wrong'
        with self.assertRaisesRegex(ValueError,'要求'):self.submit(j,bad)
        ppt.cancel(j['id']);renewed=ppt.claim(ppt.resume(j['id'])['id'])
        m['lease']=renewed['lease']
        with self.assertRaisesRegex(ValueError,'当前令牌'):self.submit(renewed,m)

    def test_single_source_file_mutation_rejected(self):
        j,m=self.prepare(2,'image');ctx=json.loads((Path(j['folder'])/'context.json').read_text(encoding='utf-8'))
        Path(ctx['pages'][0]['objects'][0]['source_path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'单图片原图'):self.submit(j,m)
