from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, urlencode, unquote
import base64, json, mimetypes, os, secrets, argparse, socket, subprocess, sys, tempfile
import core
TOKEN=secrets.token_urlsafe(32)
def handoff(t):
    skill=core.ROOT/'.agents/skills/local-image-edit/SKILL.md'
    prompt=f"执行本地图片批注任务。先读取专用 Skill：{skill}，再读取任务：{Path(t['folder'])/'task.json'} 以及 批注.md。项目目录：{core.ROOT}。任务模式 {t['mode']}。按 Skill 使用 worker.py 领取、执行并写回结果与状态。测试探针只写回执，真实编辑必须调用当前聊天内置图片编辑工具，不能以程序绘图冒充模型编辑；不可用则写失败。不要要求用户复制路径或重新上传图片。此任务只执行图片工作流，不修改工具源代码、README、STATUS、INDEX，也不运行工具开发的单元测试；项目开发由原聊天负责。"
    return 'codex://new?'+urlencode({'path':str(core.ROOT),'prompt':prompt})
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,status,body,ctype='application/json; charset=utf-8'):
        data=json.dumps(body,ensure_ascii=False).encode('utf-8') if ctype.startswith('application/json') else body
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"); self.end_headers(); self.wfile.write(data)
    def validhost(self): return self.headers.get('Host')==f'127.0.0.1:{self.server.server_port}'
    def do_GET(self):
        if not self.validhost(): return self.send(403,{'error':'请通过本机 127.0.0.1 地址访问'})
        path=unquote(urlparse(self.path).path)
        try:
            if path=='/api/state': return self.send(200,core.state())
            if path=='/api/session': return self.send(200,{'token':TOKEN})
            root=core.DATA if path.startswith('/data/') else core.ROOT/'web'
            rel=path[6:] if path.startswith('/data/') else ('index.html' if path=='/' else path.lstrip('/'))
            p=(root/rel).resolve()
            if not p.is_relative_to(root.resolve()) or p.suffix not in ('.html','.js','.css','.png'): return self.send(404,{'error':'文件不存在'})
            self.send(200,p.read_bytes(),mimetypes.guess_type(p)[0] or 'application/octet-stream')
        except (ValueError,FileNotFoundError): self.send(404,{'error':'文件不存在'})
    def do_POST(self):
        origin=self.headers.get('Origin'); expected=f'http://127.0.0.1:{self.server.server_port}'
        if not self.validhost() or self.headers.get('X-Local-Token')!=TOKEN or (origin and origin!=expected): return self.send(403,{'error':'本地会话校验失败，请重新打开网页'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<60_000_000: raise ValueError('请求过大或为空')
            b=json.loads(self.rfile.read(length)); path=urlparse(self.path).path
            if path=='/api/upload': result=core.upload(base64.b64decode(b['data'],validate=True),b['name'])
            elif path=='/api/tasks': result=core.create_task(b)
            elif path=='/api/ocr':
                image=core.read('image',b['image_id']); v=core.read('version',b['base_version_id'])
                if v['image_id']!=image['id'] or v['id']!=image['current_version_id']:raise ValueError('OCR 基准版本已变化，请重新识别')
                with tempfile.TemporaryDirectory(prefix='annotator-ocr-') as tmp:
                    output=Path(tmp)/'result.json'
                    try:subprocess.run([sys.executable,str(core.ROOT/'ocr_local.py'),v['path'],str(output)],capture_output=True,timeout=90,check=False)
                    except subprocess.TimeoutExpired:raise ValueError('本地 OCR 加载或识别超过 90 秒，请重试')
                    if not output.exists():raise ValueError('本地 OCR 无法启动；需要已有 RapidOCR、ONNXRuntime 和中文模型')
                    result=json.loads(output.read_text(encoding='utf-8'))
                    if 'error' in result:raise ValueError('本地 OCR 失败：'+result['error'])
                    result.update(image_id=image['id'],base_version_id=v['id'])
            elif path=='/api/draft': result=core.save_draft(b)
            elif path=='/api/retry': result=core.retry(b['task_id'])
            elif path=='/api/cancel': result=core.cancel(b['task_id'])
            elif path=='/api/restore': result=core.restore(b['image_id'],b['version_id'])
            elif path=='/api/handoff':
                t=core.read('task',b['task_id'])
                if t['status']!='waiting': raise ValueError('仅待发送任务可打开 Codex')
                uri=handoff(t); os.startfile(uri)
                result={'requested_open':True,'prefill_verified':False,'sent':False,'uri':uri,'message':'已请求系统打开 Codex 并预填。请在 Codex 中检查输入框并点击发送；网页尚未确认预填或执行。'}
                core.dump(Path(t['folder'])/'handoff.json',result)
            else: raise ValueError('未知操作')
            self.send(200,result)
        except Exception as e: self.send(400,{'error':str(e)})
class LocalServer(ThreadingHTTPServer):
    allow_reuse_address=False
    def server_bind(self):
        if os.name=='nt': self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()
if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--port',type=int,default=47831); a=p.parse_args(); core.init()
    server=LocalServer(('127.0.0.1',a.port),Handler)
    print(f'http://127.0.0.1:{a.port}',flush=True); server.serve_forever()
