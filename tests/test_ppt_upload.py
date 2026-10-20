"""Bounded-memory transfer, lazy ZIP reading and existing security boundaries."""
import io,json,tempfile,threading,unittest,http.client
from pathlib import Path
from unittest.mock import patch
import core,server,ppt_workflow as ppt
from ppt_package import Package,ZipParts,LIMIT_UPLOAD,LIMIT_EXPANDED,LIMIT_MEMBER
from ppt_fixtures import make_deck

class BoundedReader:
    def __init__(self,size): self.remaining=size;self.maximum=0
    def read(self,n):
        self.maximum=max(self.maximum,n)
        if n>1024*1024: raise AssertionError('upload reads must be chunked')
        result=b'x'*min(n,self.remaining);self.remaining-=len(result);return result

class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='ppt-upload-test-');self.old=core.DATA;core.DATA=Path(self.tmp.name);core.init()
    def tearDown(self): core.DATA=self.old;self.tmp.cleanup()
    def test_large_stream_saved_exactly_without_base64(self):
        size=41*1024*1024+7;reader=BoundedReader(size)
        with patch('ppt_workflow.schedule') as schedule: d=ppt.upload_stream(reader,size,'中文大文件.pptx')
        self.assertEqual(Path(d['original_path']).stat().st_size,size);self.assertEqual(core.sha(d['original_path']),d['original_sha256']);self.assertLessEqual(reader.maximum,1024*1024);schedule.assert_called_once_with(d['id'])
    def test_truncated_stream_not_registered_and_partial_removed(self):
        with self.assertRaisesRegex(ValueError,'中断'):ppt.upload_stream(io.BytesIO(b'abc'),100,'broken.pptx')
        self.assertFalse(ppt.state()['decks']);self.assertFalse(list(core.DATA.rglob('upload.part')))
    def test_upload_limit_keeps_body_unread(self):
        reader=BoundedReader(1)
        with self.assertRaisesRegex(ValueError,'512 MiB'):ppt.upload_stream(reader,LIMIT_UPLOAD+1,'large.pptx')
        self.assertEqual(reader.maximum,0);self.assertEqual(LIMIT_EXPANDED,2*1024**3);self.assertEqual(LIMIT_MEMBER,256*1024**2)
    def test_lazy_zip_opaque_members_not_loaded_and_export_preserves(self):
        import zipfile
        p=make_deck(core.DATA/'test.pptx')
        with zipfile.ZipFile(p,'a') as z:z.writestr('opaque.bin',b'x'*(2*1024*1024))
        original=zipfile.ZipFile.read;reads=[]
        def read(z,name,*args,**kw):reads.append(name);return original(z,name,*args,**kw)
        with patch('zipfile.ZipFile.read',read): pack=Package(p)
        self.assertIsInstance(pack.parts,ZipParts);self.assertNotIn('opaque.bin',reads);self.assertEqual(pack.parts.edits,{})
        pack.text_replace(dict(page=1,object_id='2',old='2025',new='2026'));out=core.DATA/'out.pptx';saved=pack.save(out)
        self.assertEqual(pack.parts.sha('opaque.bin'),saved.parts.sha('opaque.bin'));self.assertEqual(saved.object(1,'2')['text'],'Plan 2026')
    def test_binary_http_token_size_filename_and_truncation(self):
        instance=server.LocalServer(('127.0.0.1',0),server.Handler);threading.Thread(target=instance.serve_forever,daemon=True).start()
        def request(body,headers):
            c=http.client.HTTPConnection('127.0.0.1',instance.server_port,timeout=5);c.request('POST','/api/ppt/upload-file',body,headers);r=c.getresponse();value=r.status,json.loads(r.read());c.close();return value
        try:
            self.assertEqual(request(b'abc',{})[0],403)
            headers={'X-Local-Token':server.TOKEN,'X-File-Name':'fixture.pptx'}
            status,b=request(b'',dict(headers,**{'Content-Length':str(LIMIT_UPLOAD+1)}));self.assertEqual(status,413)
            self.assertEqual(request(b'abc',dict(headers,**{'X-File-Name':'test.pdf'}))[0],400)
            with patch('ppt_workflow.schedule'):
                status,d=request(b'body',headers)
            self.assertEqual(status,200);self.assertEqual(Path(d['original_path']).read_bytes(),b'body')
        finally:instance.shutdown();instance.server_close()

if __name__=='__main__':unittest.main()
