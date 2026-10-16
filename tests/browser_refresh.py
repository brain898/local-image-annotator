"""Regression for result visibility; isolated synthetic images, no image API."""
from pathlib import Path
import sys, tempfile, subprocess, os, io, json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core
from PIL import Image,ImageDraw
from playwright.sync_api import sync_playwright
observe='--observe' in sys.argv
report={'synthetic_test':True,'at':core.now(),'observations':{}}
with tempfile.TemporaryDirectory(prefix='local-image-refresh-') as folder:
    core.DATA=Path(folder);b=io.BytesIO();Image.new('RGB',(500,400),'#cbdcc8').save(b,format='PNG');image=core.upload(b.getvalue(),'refresh-test.png')
    def new_task():
        im=core.read('image',image['id'])
        return core.claim(core.create_task(dict(image_id=im['id'],base_version_id=im['current_version_id'],request_key=core.ident(),regions=[dict(x=100,y=100,w=140,h=120,note='isolated refresh regression')]))['id'])
    def finish(t,color):
        ref=core.prepare(t['id'],t['lease'])[0];im=Image.open(ref['path']).convert('RGBA');d=ImageDraw.Draw(im);d.rectangle((120,120,200,190),fill=color)
        patch=Path(t['folder'])/'synthetic.png';core.png(patch,im);manifest=Path(t['folder'])/'patches.json';core.dump(manifest,dict(task_id=t['id'],lease=t['lease'],patches=[dict(number=1,path=str(patch),reviewed=True,review_note='Synthetic browser test, no model')]))
        result=core.compose(t['id'],t['lease'],manifest);assert result['status']=='completed';return result
    first=finish(new_task(),'#dd5533')
    env=os.environ.copy();env['ANNOTATOR_DATA']=folder;env['PYTHONIOENCODING']='utf-8'
    server=subprocess.Popen([sys.executable,'server.py','--port','47833'],cwd=core.ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1450,'height':1050});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:47833');page.wait_for_function('() => E.bitmap !== null')
            page.locator('#compare').check();page.locator('#slider').fill('100');page.locator('#slider').dispatch_event('input')
            second=finish(new_task(),'#2255aa');page.wait_for_function(f"() => E.view === '{second['id']}'")
            report['observations']['comparison_hidden_after_new_result']=not page.locator('#compare').is_checked()
            page.locator('#compare').uncheck()
            third=new_task();target=f"**/data/images/{image['id']}/{third['id']}.png";requests={'aborted':0}
            def transient_failure(route):
                if not requests['aborted']:requests['aborted']+=1;route.abort('failed')
                else:route.continue_()
            page.route(target,transient_failure);finish(third,'#dda535')
            page.wait_for_function(f"() => E.data.tasks.some(t=>t.id==='{third['id']}' && t.status==='completed')")
            if observe:page.wait_for_timeout(3500)
            else:page.wait_for_function(f"() => E.bitmap?.src.endsWith('/{third['id']}.png')",timeout=10000)
            report['observations']['retried_failed_png_load']=page.evaluate(f"E.bitmap.src.endsWith('/{third['id']}.png')")
            report['observations']['png_request_failed_once']=requests['aborted']==1
            if not observe:
                page.locator(f'.version-card[data-version-id="{first["id"]}"]').click();fourth=finish(new_task(),'#773399')
                page.wait_for_function(f"() => E.image.current_version_id === '{fourth['id']}'")
                report['observations']['manual_history_not_overwritten']=page.evaluate(f"E.view === '{first['id']}'")
                button=page.locator('#live-status-action');button.wait_for();assert button.inner_text()=='显示最新结果';button.click();page.wait_for_function(f"() => E.view === '{fourth['id']}'")
                report['observations']['new_result_action_visible_and_loads']=True
            report['page_errors']=errors;assert not errors
            browser.close()
    finally:server.terminate();server.wait(timeout=10)
core.dump(core.ROOT/('evidence/refresh-before.json' if observe else 'evidence/refresh-regression.json'),report)
print(json.dumps(report,ensure_ascii=False,indent=2))
if not observe:assert all(report['observations'].values()),report
