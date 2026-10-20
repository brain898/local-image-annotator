"""Offline PowerPoint renderer, isolated subprocess and cross-process COM lock.

Never saves the source presentation or quits an existing user's application.
"""
from pathlib import Path
import json, sys, os, time, subprocess, contextlib, hashlib

def file_sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

@contextlib.contextmanager
def office_lock():
    import msvcrt, tempfile
    p=Path(tempfile.gettempdir())/'local-annotator-powerpoint.lock'
    with p.open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); start=time.monotonic()
        while True:
            try: f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); break
            except OSError:
                if time.monotonic()-start>180: raise ValueError('PowerPoint 本地渲染队列等待超时')
                time.sleep(.3)
        try: yield
        finally: f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)

def available():
    if os.name!='nt': return {'available':False,'engine':None,'error':'需要 Windows Microsoft PowerPoint 与 pywin32；不会上传外部转换服务'}
    try:
        import winreg, win32com.client
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,r'PowerPoint.Application\CLSID'): pass
        return {'available':True,'engine':'Microsoft PowerPoint COM（本机只读渲染）'}
    except (ImportError,OSError) as e: return {'available':False,'engine':None,'error':'缺少 Microsoft PowerPoint COM 或 pywin32：'+str(e)}

def render(path,folder):
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'
    try:
        proc=subprocess.run([sys.executable,str(Path(__file__)),str(Path(path).resolve()),str(folder.resolve())],capture_output=True,timeout=360,env=env)
    except subprocess.TimeoutExpired: raise ValueError('PowerPoint 渲染超过 360 秒；未伪造预览，请稍后重试')
    report=folder/'render.json'
    if not report.exists(): raise ValueError('PowerPoint 渲染未返回结果：'+proc.stderr.decode('utf-8',errors='replace')[-1500:])
    result=json.loads(report.read_text(encoding='utf-8'))
    if result.get('error'): raise ValueError(result['error'])
    return result

def render_pair(before,after,folder):
    """Fresh before/after/before under one COM lock; no historical PNG baseline."""
    folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
    report=folder/'pair.json'
    report.unlink(missing_ok=True)
    env=os.environ.copy(); env['PYTHONIOENCODING']='utf-8'
    try:
        proc=subprocess.run([sys.executable,str(Path(__file__)),'--pair',str(Path(before).resolve()),str(Path(after).resolve()),str(folder.resolve())],capture_output=True,timeout=720,env=env)
    except subprocess.TimeoutExpired: raise ValueError('PowerPoint 同批对照渲染超过 720 秒；未应用修改')
    if not report.exists(): raise ValueError('PowerPoint 同批对照未返回结果：'+proc.stderr.decode('utf-8',errors='replace')[-1500:])
    result=json.loads(report.read_text(encoding='utf-8'))
    if result.get('error'): raise ValueError(result['error'])
    if proc.returncode: raise ValueError('PowerPoint 同批对照进程异常退出')
    return result

def direct_pair(before,after,folder):
    import pythoncom
    pythoncom.CoInitialize()
    try:
        with office_lock():
            bh=file_sha(before); ah=file_sha(after); results={}
            for key,path in [('before',before),('after',after),('before_check',before)]:
                sub=folder/key; sub.mkdir(parents=True,exist_ok=True)
                result=direct_locked(path,sub)
                (sub/'render.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                results[key]=result
            if file_sha(before)!=bh or file_sha(after)!=ah: raise ValueError('对照渲染期间 PPT 文件发生变化')
            results.update(before_sha256=bh,after_sha256=ah,protocol='fresh-before-after-before')
            return results
    finally: pythoncom.CoUninitialize()

def measure(shape,cell=None):
    try:
        tf=shape.TextFrame2; t=tf.TextRange
        return {'text':t.Text,'x':float(t.BoundLeft),'y':float(t.BoundTop),'w':float(t.BoundWidth),'h':float(t.BoundHeight),
                'lines':int(t.Lines.Count),'shape_x':float(shape.Left),'shape_y':float(shape.Top),
                'shape_w':float(shape.Width),'shape_h':float(shape.Height),
                'margin_x':float(tf.MarginLeft+tf.MarginRight),'margin_y':float(tf.MarginTop+tf.MarginBottom)}
    except Exception: return None

def direct(path,folder):
    import pythoncom
    pythoncom.CoInitialize()
    try:
        with office_lock(): return direct_locked(path,folder)
    finally: pythoncom.CoUninitialize()

def direct_locked(path,folder):
    import win32com.client
    pres=None; app=None; old_security=None; old_alert=None
    try:
        app=win32com.client.DispatchEx('PowerPoint.Application')
        old_security=app.AutomationSecurity; old_alert=app.DisplayAlerts
        app.AutomationSecurity=3; app.DisplayAlerts=1
        pres=app.Presentations.Open(str(path),ReadOnly=True,Untitled=False,WithWindow=False)
        w=float(pres.PageSetup.SlideWidth); h=float(pres.PageSetup.SlideHeight)
        width=1600; height=round(width*h/w); results=[]
        for number in range(1,pres.Slides.Count+1):
            slide=pres.Slides(number); output=folder/f'slide-{number}.png'
            slide.Export(str(output),'PNG',width,height)
            metrics={}
            for shape in slide.Shapes:
                oid=str(shape.Id)
                if shape.HasTable:
                    for row in range(1,shape.Table.Rows.Count+1):
                        for col in range(1,shape.Table.Columns.Count+1): metrics[f'{oid}:{row-1}:{col-1}']=measure(shape.Table.Cell(row,col).Shape)
                elif shape.HasTextFrame: metrics[oid]=measure(shape)
            results.append({'number':number,'path':str(output),'metrics':metrics})
            (folder/'progress.json').write_text(json.dumps({'stage':'PowerPoint 本地渲染','done':number,'total':pres.Slides.Count}),encoding='utf-8')
        return {'engine':'Microsoft PowerPoint COM','width':width,'height':height,'pages':results,'read_only':True,'source_saved':False}
    finally:
        if pres is not None: pres.Close()
        if app is not None:
            if old_security is not None: app.AutomationSecurity=old_security
            if old_alert is not None: app.DisplayAlerts=old_alert
            # PowerPoint may reuse an application even for DispatchEx. Do not
            # Quit: the user may have an unrelated unsaved deck open.

if __name__=='__main__':
    paired=sys.argv[1]=='--pair'
    folder=Path(sys.argv[4] if paired else sys.argv[2]).resolve(); folder.mkdir(parents=True,exist_ok=True)
    try: result=direct_pair(Path(sys.argv[2]).resolve(),Path(sys.argv[3]).resolve(),folder) if paired else direct(Path(sys.argv[1]).resolve(),folder)
    except Exception as e: result={'error':'本地 PowerPoint 渲染失败：'+str(e)}
    (folder/('pair.json' if paired else 'render.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    sys.exit(1 if result.get('error') else 0)
