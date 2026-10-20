"""Local OCR and mask acceptance, isolated data and Chrome; no model or handoff."""
from pathlib import Path
import sys,tempfile,threading,json,io
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core,server,numpy as np
from PIL import Image,ImageDraw,ImageFont
from playwright.sync_api import sync_playwright

def run():
    old=core.DATA;report={'at':core.now(),'synthetic_image':True,'model_called':False,'checks':{}}
    with tempfile.TemporaryDirectory(prefix='annotator-ocr-mask-') as folder:
        core.DATA=Path(folder);core.init()
        im=Image.new('RGB',(1600,900),'white');d=ImageDraw.Draw(im);font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',64)
        d.text((120,150),'年度计划 2025',font=font,fill='black');d.text((160,400),'试用版本',font=font,fill='black')
        raw=io.BytesIO();im.save(raw,format='PNG');image=core.upload(raw.getvalue(),'中文PPT.png');other=core.upload(raw.getvalue(),'另一张.png')
        http=server.LocalServer(('127.0.0.1',0),server.Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1500,'height':1100});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/handoff',lambda route:route.fulfill(json={'intercepted':True}))
                page.goto(f'http://127.0.0.1:{http.server_port}');page.wait_for_function('()=>E.bitmap!==null')
                page.evaluate('(id)=>selectImage(id)',image['id']);page.wait_for_function('(id)=>E.view===id',arg=image['current_version_id'])
                page.locator('#ocr').click();page.wait_for_function("()=>document.querySelector('#ocr-rows').children.length>0",timeout=95000)
                rows=page.evaluate('E.ocrRows');assert any('2025' in r['text'] for r in rows),rows
                assert all(0<=r['x']<1600 and 0<=r['y']<900 for r in rows);report['ocr_rows']=rows
                card=page.locator('#ocr-rows .region').filter(has=page.locator('input[value=""]')) if False else page.locator('#ocr-rows .region').first
                card.locator('button').click();assert page.evaluate('E.regions.length')==0
                card.locator('input').nth(0).fill('年度计划 2025');card.locator('input').nth(1).fill('年度计划 2026');card.locator('button').click()
                r=page.evaluate('E.regions[0]');assert r['ocr_bounds']['x']==rows[0]['x'] and r['ocr_bounds']['y']==rows[0]['y'] and r['x']<rows[0]['x'] and r['original_text']=='年度计划 2025' and r['target_text']=='年度计划 2026'
                report['checks']['real_chinese_ocr_original_coordinates_edit_list_unchanged_excluded']=True
                page.locator('#regions .region').first.locator('details.advanced summary').click()
                assert page.locator('#regions input[aria-label="选区 1 参考边距"]').count()==0
                page.locator('#brush-tool').click()
                point=page.evaluate('()=>{const r=E.regions[0],b=canvas.getBoundingClientRect();return {x:b.left+E.tx+(r.x+r.w*.3)*E.scale,y:b.top+E.ty+(r.y+r.h*.5)*E.scale,dx:r.w*.4*E.scale}}')
                page.mouse.move(point['x'],point['y']);page.mouse.down();page.mouse.move(point['x']+point['dx'],point['y'],steps=15);page.mouse.up()
                page.locator('#erase-tool').click();page.mouse.click(point['x']+point['dx']/2,point['y'])
                page.evaluate('persist()');r=page.evaluate('E.regions[0]');assert len(r['mask']['strokes'])==2
                # Compare displayed native-resolution raster against backend byte for byte.
                alpha=page.evaluate('()=>Array.from(strokeCanvas(E.regions[0]).getContext("2d").getImageData(0,0,E.regions[0].w,E.regions[0].h).data).filter((v,i)=>i%4===3)')
                assert np.array_equal(np.array(alpha,dtype=np.uint8).reshape(r['h'],r['w']),np.asarray(core.region_mask(r)))
                report['checks']['brush_erase_preview_equals_backend_native_pixels']=True
                # Reload persisted mask/notes/reference; version is bound by draft.
                page.reload();page.wait_for_function('()=>E.regions.length===1&&E.bitmap!==null');assert page.evaluate('E.regions[0]')==r
                report['checks']['draft_reload_mask_reference_and_text']=True
                page.locator('#submit').click();page.wait_for_function('()=>!E.pending');t=core.state()['tasks'][0];assert t['regions'][0]==dict(r,number=1)
                t=core.claim(t['id']);refs=core.prepare(t['id'],t['lease']);ref=refs[0];assert ref['reference']['w']==max(1024,((r['w']+192+15)//16)*16)
                edit=Image.open(ref['path']).convert('RGBA');rr=ref['reference'];ImageDraw.Draw(edit).rectangle((r['x']-rr['x'],r['y']-rr['y'],r['x']-rr['x']+r['w']-1,r['y']-rr['y']+r['h']-1),fill='#2222ff')
                patch=Path(t['folder'])/'synthetic.png';core.png(patch,edit);manifest=Path(t['folder'])/'patches.json';core.dump(manifest,{'task_id':t['id'],'lease':t['lease'],'patches':[{'number':1,'path':str(patch),'reviewed':True,'review_note':'Synthetic acceptance only, no model'}]})
                out=core.compose(t['id'],t['lease'],manifest);assert out['status']=='completed',out
                before=np.asarray(Image.open(t['base_path']).convert('RGBA'));after=np.asarray(Image.open(core.read('version',out['output_version_id'])['path']).convert('RGBA'));mask=np.zeros(before.shape[:2],bool);mask[r['y']:r['y']+r['h'],r['x']:r['x']+r['w']]=np.asarray(core.region_mask(r))>0
                assert np.count_nonzero(np.any(before!=after,axis=2)&~mask)==0;report['validation']=out['validation'];report['checks']['compose_mask_outside_zero']=True
                page.wait_for_function('(id)=>E.view===id',arg=out['output_version_id']);assert page.evaluate('E.regions.length')==0
                report['checks']['new_version_clears_old_annotations']=True
                # Explicit OCR failure and empty result states.
                page.route('**/api/ocr',lambda route:route.fulfill(status=400,json={'error':'isolated OCR error'}));page.locator('#ocr').click();page.wait_for_function("()=>document.querySelector('#ocr-status').textContent.includes('识别失败')")
                page.unroute('**/api/ocr');page.route('**/api/ocr',lambda route:route.fulfill(json={'rows':[]}));page.locator('#ocr').click();page.wait_for_function("()=>document.querySelector('#ocr-status').textContent.includes('未识别到文字')")
                report['checks']['ocr_error_and_empty_states']=True
                # A delayed reply cannot populate a newly selected image.
                page.unroute('**/api/ocr');page.route('**/api/ocr',lambda route:route.fulfill(json={'rows':rows}));page.evaluate('()=>{document.querySelector("#ocr").click();selectImage("'+other['id']+'");}')
                page.wait_for_function('(id)=>E.image.id===id&&!E.loadingVersion',arg=other['id']);page.wait_for_timeout(500);assert page.locator('#ocr-rows').text_content()==''
                report['checks']['ocr_late_result_does_not_cross_images']=True
                assert not errors,errors;report['page_errors']=errors;browser.close()
        finally:http.shutdown();http.server_close();thread.join();core.DATA=old
    core.dump(core.ROOT/'evidence/ocr-mask-acceptance.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':run()
