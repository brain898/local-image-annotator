"""Isolated browser checks with synthetic patches, explicitly NOT model evidence."""
from pathlib import Path
import tempfile, subprocess, os, json, sys, time, io, base64, hashlib
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
result={'kind':'synthetic_browser_state_tests','model_edit':False,'handoff_stubbed':True,'checks':{},'at':core.now()}
with tempfile.TemporaryDirectory(prefix='local-image-state-test-') as folder:
    core.DATA=Path(folder); b=io.BytesIO();Image.new('RGB',(500,400),'#cbdcc8').save(b,format='PNG');image=core.upload(b.getvalue(),'状态验收.png')
    env=os.environ.copy();env['ANNOTATOR_DATA']=folder;env['PYTHONIOENCODING']='utf-8'
    server=subprocess.Popen([sys.executable,'server.py','--port','47832'],cwd=core.ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1450,'height':1050})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/api/handoff',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'requested_open':False,'sent':False,'message':'状态测试桩，未打开 Codex'})))
            page.goto('http://127.0.0.1:47832');page.wait_for_function('() => E.bitmap !== null')
            tr=page.evaluate('() => {const r=canvas.getBoundingClientRect();return {x:r.x+E.tx,y:r.y+E.ty,s:E.scale}}')
            page.mouse.move(tr['x']+100*tr['s'],tr['y']+100*tr['s']);page.mouse.down();page.mouse.move(tr['x']+240*tr['s'],tr['y']+220*tr['s'],steps=10);page.mouse.up()
            page.get_by_role('textbox',name='选区 1 修改要求',exact=True).fill('仅用于失败与合成状态测试，不调用模型')
            def mouse_drag(x1,y1,x2,y2):
                tr=page.evaluate('() => {const r=canvas.getBoundingClientRect();return {x:r.x+E.tx,y:r.y+E.ty,s:E.scale}}')
                page.mouse.move(tr['x']+x1*tr['s'],tr['y']+y1*tr['s']);page.mouse.down();page.mouse.move(tr['x']+x2*tr['s'],tr['y']+y2*tr['s'],steps=10);page.mouse.up()
            mouse_drag(240,220,270,240);assert page.evaluate('E.regions[0].w')==170;assert page.evaluate('E.regions[0].h')==140
            mouse_drag(270,240,240,220);assert page.evaluate('E.regions[0].w')==140
            mouse_drag(160,155,180,170);assert page.evaluate('E.regions[0].x')==120;assert page.evaluate('E.regions[0].y')==115
            mouse_drag(180,170,160,155);assert page.evaluate('E.regions[0].x')==100
            result['checks']['mouse_resize_and_move']=True
            page.locator('#pan-tool').click();old=page.evaluate('({x:E.tx,y:E.ty})');bb=page.locator('#canvas').bounding_box()
            page.mouse.move(bb['x']+100,bb['y']+80);page.mouse.down();page.mouse.move(bb['x']+140,bb['y']+105,steps=8);page.mouse.up()
            assert page.evaluate('E.tx')==old['x']+40;assert page.evaluate('E.ty')==old['y']+25;assert page.evaluate('E.regions[0].x')==100
            result['checks']['pan_does_not_change_image_coordinates']=True
            page.locator('#fit').click();page.locator('#draw-tool').click();page.locator('#submit').click()
            page.wait_for_function("() => E.data.tasks.length === 1");t=core.state()['tasks'][0];t=core.claim(t['id']);core.fail(t['id'],t['lease'],'验收注入：图片工具不可用，不能误报完成')
            page.locator('.status.failed').wait_for();assert page.locator('.task .error').inner_text().startswith('验收注入');assert page.locator('.version-card').count()==1
            result['checks']['failure_visible_no_false_completion']=True
            page.get_by_role('button',name='重试并去 Codex',exact=True).click();page.locator('.status.waiting').wait_for();result['checks']['retry_waiting']=True
            t=core.claim(t['id']);refs=core.prepare(t['id'],t['lease']);ref=refs[0];r=ref['allowed'];rr=ref['reference'];edit=Image.open(ref['path']).convert('RGBA');d=ImageDraw.Draw(edit);d.rectangle((r['x']-rr['x']+15,r['y']-rr['y']+15,r['x']-rr['x']+80,r['y']-rr['y']+75),fill='#d95437')
            patch=Path(t['folder'])/'synthetic.png';core.png(patch,edit);manifest=Path(t['folder'])/'patches.json';core.dump(manifest,{'task_id':t['id'],'lease':t['lease'],'patches':[{'number':1,'path':str(patch),'reviewed':True,'review_note':'程序生成的浏览器测试补丁，不是模型输出'}]})
            out=core.compose(t['id'],t['lease'],manifest);assert out['status']=='completed'
            page.wait_for_function('() => E.view === E.image.current_version_id && E.data.versions.length === 2 && E.regions.length === 0')
            page.locator('.status.completed').wait_for();assert page.locator('.status.completed').count()==1;result['checks']['new_version_auto_update_without_reload']=True
            page.locator('#compare').check();page.locator('#slider').fill('0');page.locator('#slider').dispatch_event('input');after=page.evaluate('canvas.toDataURL()')
            page.locator('#slider').fill('100');page.locator('#slider').dispatch_event('input');before=page.evaluate('canvas.toDataURL()');assert after!=before;result['checks']['comparison_slider_changes_render']=True
            with page.expect_download() as got: page.locator('#export').click()
            download=Path(folder)/'export.png';got.value.save_as(str(download))
            with Image.open(download) as saved: assert saved.size==(500,400)
            assert core.sha(download)==core.read('version',out['output_version_id'])['sha256'];result['checks']['export_original_size_lossless_png']=True
            page.locator(f'.version-card[data-version-id="{image["original_version_id"]}"]').click();page.locator('#restore').click()
            page.wait_for_function(f"() => E.image.current_version_id === '{image['original_version_id']}'")
            assert page.locator('.version-card').count()==2;result['checks']['restore_preserves_history']=True
            page.reload();page.wait_for_function('() => E.bitmap !== null');assert page.locator('.version-card').count()==2;assert page.locator('.task').count()==1;result['checks']['history_persists_after_reopen']=True
            jpg=Path(folder)/'jpg-input.jpg';Image.new('RGB',(240,160),'#e8bf7b').save(jpg,format='JPEG')
            page.locator('#file').set_input_files(str(jpg));page.wait_for_function("() => E.image?.name === 'jpg-input.jpg' && E.bitmap?.width === 240")
            jpeg_image=core.read('image',page.evaluate('E.image.id'));jpeg_version=core.read('version',jpeg_image['current_version_id'])
            with Image.open(jpeg_version['path']) as normalized:assert normalized.format=='PNG';assert normalized.size==(240,160)
            assert core.sha(core.DATA/'images'/jpeg_image['id']/'original.upload')==core.sha(jpg)
            result['checks']['jpeg_import_original_preserved_png_baseline']=True
            result['page_errors']=errors;assert not errors
            browser.close()
    finally:
        server.terminate();server.wait(timeout=10)
core.dump(core.ROOT/'evidence/browser-states.json',result);print(json.dumps(result,ensure_ascii=False,indent=2))
