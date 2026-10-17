import os, tempfile, unittest, io, json
from pathlib import Path
import core
from PIL import Image, ImageDraw
import numpy as np

class Invariants(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=core.DATA; core.DATA=Path(self.tmp.name); core.init()
        im=Image.new('RGBA',(400,300),'#cad8d2'); d=ImageDraw.Draw(im); d.rectangle((5,5,80,40),fill='#ac2234')
        b=io.BytesIO(); im.save(b,format='PNG'); self.image=core.upload(b.getvalue(),'中文测试.png')
    def tearDown(self): core.DATA=self.old; self.tmp.cleanup()
    def task(self,**kw):
        return core.create_task(dict(image_id=self.image['id'],base_version_id=self.image['current_version_id'],request_key=core.ident(),regions=[dict(x=80,y=70,w=120,h=70,note='改数字为 2026'),dict(x=240,y=180,w=80,h=60,note='删除元素')],overall='保留原风格',**kw))
    def patches(self,t,wrongsize=False):
        refs=core.prepare(t['id'],t['lease']); patches=[]
        for ref in refs:
            im=Image.open(ref['path']).convert('RGBA'); r=ref['allowed']; rr=ref['reference']; d=ImageDraw.Draw(im)
            x=r['x']-rr['x']; y=r['y']-rr['y']; d.rectangle((x+10,y+10,x+25,y+25),fill='#f5a134')
            if wrongsize: im=im.resize((17,17))
            p=Path(t['folder'])/f"patch-{r['number']}.png"; core.png(p,im)
            patches.append(dict(number=r['number'],path=str(p),reviewed=True,review_note='合成单元测试补丁，非模型生成'))
        path=Path(t['folder'])/'patches.json'; core.dump(path,dict(task_id=t['id'],lease=t['lease'],patches=patches)); return path
    def test_utf8_and_two_annotations(self):
        t=self.task(); md=(Path(t['folder'])/'批注.md').read_bytes()
        self.assertFalse(md.startswith(b'\xef\xbb\xbf')); self.assertIn('改数字为 2026',md.decode('utf-8')); self.assertIn('删除元素',md.decode('utf-8'))
        self.assertEqual(t['regions'][1]['x'],240)
    def test_small_reference_margin_expands_context_not_allowed_pixels(self):
        t=core.create_task(dict(image_id=self.image['id'],base_version_id=self.image['current_version_id'],request_key=core.ident(),regions=[dict(x=100,y=100,w=93,h=30,note='test',reference_margin=21)]))
        t=core.claim(t['id']);ref=core.prepare(t['id'],t['lease'])[0]
        with Image.open(ref['path']) as im:self.assertEqual(im.size,(1024,1024))
        self.assertEqual(ref['allowed'],t['regions'][0])
        with Image.open(ref['mask_path']) as im:self.assertEqual(im.size,(93,30))
        self.assertFalse(ref['preflight']['registration_proven'])
    def test_bounds_and_scaled_coordinates_must_be_integer(self):
        t=self.task(); core.cancel(t['id'])
        for rect in [dict(x=-1,y=0,w=20,h=20),dict(x=10.5,y=0,w=20,h=20),dict(x=390,y=0,w=20,h=20)]:
            with self.assertRaises(ValueError): core.validate_rect(rect,400,300)
    def test_request_idempotency(self):
        t=self.task(); b=dict(t,regions=t['regions']); b['base_version_id']=t['base_version_id']
        self.assertEqual(core.create_task(b)['id'],t['id']); self.assertEqual(len(core.state()['tasks']),1)
    def test_serial_and_no_duplicate_claim(self):
        t=self.task()
        with self.assertRaises(ValueError): self.task()
        core.claim(t['id'])
        with self.assertRaises(ValueError): core.claim(t['id'])
    def test_probe_cannot_complete_edit(self):
        t=core.claim(self.task()['id'])
        with self.assertRaises(ValueError): core.receipt(t['id'],t['lease'])
    def test_probe_receipt_preserves_notes(self):
        t=core.claim(self.task(mode='probe')['id']); out=core.receipt(t['id'],t['lease'])
        self.assertEqual(out['receipt']['received_regions'],t['regions']); self.assertIsNone(out['output_version_id'])
    def test_png_outside_zero_idempotent_publish_and_original_immutable(self):
        t=core.claim(self.task()['id']); original_hash=core.sha(t['base_path']); p=self.patches(t)
        out=core.compose(t['id'],t['lease'],p)
        self.assertEqual(out['status'],'completed'); self.assertEqual(out['validation']['outside_changed_pixels'],0)
        self.assertGreater(out['validation']['inside_changed_pixels'],0); self.assertEqual(core.sha(t['base_path']),original_hash)
        core.compose(t['id'],t['lease'],p); self.assertEqual(len(core.state()['versions']),2)
        v=core.read('version',out['output_version_id'])
        with Image.open(v['path']) as saved: self.assertEqual(saved.size,(400,300))
    def test_wrong_dimension_failure_no_version(self):
        t=core.claim(self.task()['id']); out=core.compose(t['id'],t['lease'],self.patches(t,True))
        self.assertEqual(out['status'],'needs_adjustment'); self.assertEqual(len(core.state()['versions']),1)
    def test_moved_background_rejected(self):
        t=core.claim(self.task()['id']); p=self.patches(t); m=json.loads(p.read_text(encoding='utf-8'))
        with Image.open(m['patches'][0]['path']) as im: size=im.size
        core.png(m['patches'][0]['path'],Image.new('RGBA',size,'black'))
        out=core.compose(t['id'],t['lease'],p); self.assertEqual(out['status'],'needs_adjustment')
    def test_missing_visual_review_rejected(self):
        t=core.claim(self.task()['id']); p=self.patches(t); m=json.loads(p.read_text(encoding='utf-8')); m['patches'][0]['reviewed']=False; core.dump(p,m)
        self.assertEqual(core.compose(t['id'],t['lease'],p)['status'],'needs_adjustment')
    def test_stale_worker_cannot_publish_after_cancel(self):
        t=core.claim(self.task()['id']); p=self.patches(t); core.cancel(t['id']); core.retry(t['id']); fresh=core.claim(t['id'])
        with self.assertRaises(ValueError): core.compose(t['id'],t['lease'],p)
        self.assertNotEqual(fresh['lease'],t['lease']); self.assertEqual(len(core.state()['versions']),1)
    def test_old_result_does_not_overwrite_restored_version(self):
        t=core.claim(self.task()['id']); p=self.patches(t); core.restore(self.image['id'],self.image['current_version_id'])
        out=core.compose(t['id'],t['lease'],p); self.assertEqual(out['status'],'completed'); self.assertFalse(out['applied'])
        self.assertEqual(core.read('image',self.image['id'])['current_version_id'],self.image['current_version_id'])
    def test_failure_retry_and_persistence(self):
        t=core.claim(self.task()['id']); core.fail(t['id'],t['lease'],'额度不足'); self.assertEqual(core.read('task',t['id'])['error'],'额度不足')
        core.init(); self.assertEqual(core.read('task',t['id'])['status'],'failed'); self.assertEqual(core.retry(t['id'])['status'],'waiting')
    def test_draft_persistence_and_stale_base_rejected(self):
        t=self.task(); b=dict(image_id=t['image_id'],base_version_id=t['base_version_id'],regions=t['regions'],overall='本地保存')
        core.save_draft(b); self.assertEqual(core.state()['drafts'][0]['overall'],'本地保存')
        t=core.claim(t['id']); core.compose(t['id'],t['lease'],self.patches(t))
        with self.assertRaises(ValueError): core.save_draft(b)
    def test_completed_request_is_idempotent_even_after_current_changes(self):
        t=core.claim(self.task()['id']); core.compose(t['id'],t['lease'],self.patches(t))
        self.assertEqual(core.create_task(dict(t))['id'],t['id']); self.assertEqual(len(core.state()['versions']),2)
    def test_overlapping_regions_rejected(self):
        t=self.task();core.cancel(t['id']);t['regions'][1].update(x=100,y=80)
        with self.assertRaises(ValueError):core.create_task(dict(t,request_key=core.ident()))

if __name__=='__main__': unittest.main()
