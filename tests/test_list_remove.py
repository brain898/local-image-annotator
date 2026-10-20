"""Visibility-only removal. Database changes; every payload byte is retained."""
from pathlib import Path
import io, json, tempfile, threading, unittest, urllib.request, urllib.error
from PIL import Image
import core, server, ppt_workflow as ppt


def payload_hashes():
    return {p.relative_to(core.DATA).as_posix(): core.sha(p)
            for p in core.DATA.rglob('*') if p.is_file()
            and p.name not in ('store.sqlite', 'store.sqlite-wal', 'store.sqlite-shm')}


class ListRemoveTests(unittest.TestCase):
    def setUp(self):
        self.old=core.DATA; self.temp=tempfile.TemporaryDirectory(prefix='list-remove-')
        core.DATA=Path(self.temp.name)/'data'; core.init()
        f=core.DATA/'ppt/decks/test'; f.mkdir(parents=True)
        (f/'original.pptx').write_bytes(b'original retained bytes')
        core.dump(f/'evidence.json', {'keep': True})
        self.deck=dict(id='deck',name='test.pptx',status='ready',current_version_id='version')
        with core.tx() as c:
            core.put(c,'ppt_deck',self.deck)
            core.put(c,'ppt_version',dict(id='version',deck_id='deck',path=str(f/'original.pptx')))
            core.put(c,'ppt_job',dict(id='job',deck_id='deck',kind='analysis',status='completed',items=[]))

    def tearDown(self):
        core.DATA=self.old; self.temp.cleanup()

    def test_active_deck_rejected_without_mutation(self):
        for status in ('waiting','running'):
            with core.tx() as c:
                j=core.get(c,'ppt_job','job'); j['status']=status; core.put(c,'ppt_job',j)
            before=payload_hashes()
            with self.assertRaisesRegex(ValueError,'等待或运行'): core.remove_from_list('ppt_deck','deck')
            self.assertEqual(core.read('ppt_deck','deck'),self.deck)
            self.assertEqual(before,payload_hashes())

    def test_remove_hides_only_deck_retains_jobs_versions_and_all_payloads(self):
        before=payload_hashes(); job=core.read('ppt_job','job'); version=core.read('ppt_version','version')
        core.remove_from_list('ppt_deck','deck')
        self.assertEqual(ppt.state()['decks'],[])
        self.assertTrue(core.read('ppt_deck','deck')['removed'])
        self.assertEqual(core.read('ppt_job','job'),job)
        self.assertEqual(core.read('ppt_version','version'),version)
        self.assertEqual(before,payload_hashes())
        core.remove_from_list('ppt_deck','deck')
        self.assertEqual(before,payload_hashes())

    def test_image_removal_and_active_task_guard(self):
        raw=io.BytesIO(); Image.new('RGB',(30,20),'white').save(raw,format='PNG')
        image=core.upload(raw.getvalue(),'P18.png'); before=payload_hashes()
        with core.tx() as c: core.put(c,'task',dict(id='image-job',image_id=image['id'],status='running'))
        with self.assertRaises(ValueError): core.remove_from_list('image',image['id'])
        with core.tx() as c:
            t=core.get(c,'task','image-job');t['status']='completed';core.put(c,'task',t)
        core.remove_from_list('image',image['id'])
        self.assertEqual(core.state()['images'],[])
        self.assertEqual(core.read('task','image-job')['status'],'completed')
        self.assertEqual(before,payload_hashes())

    def test_importing_deck_rejected(self):
        with core.tx() as c:
            d=core.get(c,'ppt_deck','deck');d['status']='importing';core.put(c,'ppt_deck',d)
        with self.assertRaisesRegex(ValueError,'正在导入'):core.remove_from_list('ppt_deck','deck')

    def test_http_removal_requires_local_token(self):
        http=server.LocalServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            url=f'http://127.0.0.1:{http.server_port}/api/ppt/remove'
            body=json.dumps({'deck_id':'deck'}).encode()
            req=urllib.request.Request(url,body,headers={'Content-Type':'application/json'})
            with self.assertRaises(urllib.error.HTTPError) as failure:urllib.request.urlopen(req)
            self.assertEqual(failure.exception.code,403)
            self.assertFalse(core.read('ppt_deck','deck').get('removed'))
            req.add_header('X-Local-Token',server.TOKEN)
            with urllib.request.urlopen(req) as response:self.assertTrue(json.load(response)['removed'])
        finally:http.shutdown();http.server_close();thread.join()
