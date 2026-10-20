"""Isolated Chrome + real PowerPoint. Handoff stubbed; image patches synthetic."""
from pathlib import Path
import sys, tempfile, threading, json, time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core, server, ppt_workflow as ppt
from ppt_package import Package
from ppt_fixtures import make_deck, submit_mock_plan
from playwright.sync_api import sync_playwright

def run():
    old=core.DATA;report={'kind':'PPT isolated Chrome + real PowerPoint','model_edit':False,'handoff_stubbed':True,'at':core.now(),'checks':{}}
    with tempfile.TemporaryDirectory(prefix='ppt-browser-') as tmp:
        core.DATA=Path(tmp)/'data';core.init();sample=make_deck(Path(tmp)/'fixture.pptx')
        http=server.LocalServer(('127.0.0.1',0),server.Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1480,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/ppt/handoff',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'sent':False,'message':'隔离测试：未打开 Codex，发送仍需用户完成'})))
                page.goto(f'http://127.0.0.1:{http.server_port}/ppt.html');page.locator('#file').set_input_files(str(sample));page.wait_for_function("() => P.data.decks[0]?.status === 'ready'",timeout=60000)
                assert page.locator('.thumb img').count()==7;assert page.locator('#page-image').evaluate('(e)=>e.naturalWidth')==1600;report['checks']['background_import_real_previews_and_numbered_thumbnails']=True
                page.locator('.selection').evaluate('(e)=>e.open=true');page.locator('#pages').fill('0,9');page.locator('#pages').press('Enter');page.get_by_role('status').filter(has_text='页码越界').wait_for();report['checks']['invalid_page_ranges_visible']=True
                page.locator('#select-all').click();page.locator('#select-all').click();page.wait_for_function("() => document.querySelectorAll('.thumb input:checked').length===7")
                page.locator('#pages').fill('1-2,5');page.locator('#pages').press('Enter');page.wait_for_function("() => document.querySelectorAll('.thumb input:checked').length===3&&!P.busy")
                page.locator('#proposal').fill('把 2025 改成 2026');page.locator('#next-analyze').click();page.wait_for_function("() => P.data.jobs.length===1");j=ppt.state()['jobs'][0];assert j['pages']==[1,2,5];assert page.locator('#execute').is_disabled();report['checks']['analysis_before_confirmation_and_no_model']=True
                claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'browser-plan.json';items=[dict(page=1,object_id='2',method='native',old='2025',new='2026',evidence='真实原生文字跨两个格式片段，唯一匹配',note='修改年份'),dict(page=2,object_id='2',method='native',old='2025',new='2026',cell=[1,0],evidence='表格第二行第一列唯一匹配',note='修改年份'),dict(page=5,object_id='2',method='image',old='2025',new='2026',source_rect={'x':450,'y':300,'w':180,'h':100},evidence='程序测试候选，旋转裁剪图片内可见区域，非真实模型定位',note='仅改年份')]
                core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=items));submit_mock_plan(j['id'],claim['lease'],manifest);page.wait_for_function("() => document.querySelectorAll('.plan-card').length===3")
                page.locator('.plan-card').nth(2).get_by_role('button',name='查看范围').click();page.locator('#source-canvas').wait_for();page.wait_for_function('() => P.source !== null');assert page.locator('#boxes polygon').count()==1
                page.locator('#confirm-all').click();page.wait_for_function("() => P.plan.items.every(i=>i.confirmed)");assert page.locator('#execute').is_enabled()
                card=page.locator('.plan-card').first;card.get_by_label('目标内容',exact=True).fill('2027');assert page.locator('#execute').is_disabled();card.get_by_role('button',name='保存修改').click();page.wait_for_function("() => P.plan.items[0].new==='2027' && P.plan.items.every(i=>!i.confirmed)");report['checks']['item_change_invalidates_entire_approval_batch']=True
                card.get_by_label('目标内容',exact=True).fill('2026');card.get_by_role('button',name='保存修改').click();page.wait_for_function("() => P.plan.items[0].new==='2026'")
                # Drag a source rectangle; PowerPoint preview uses calibrated polygon.
                page.locator('#source-canvas').scroll_into_view_if_needed();box=page.locator('#source-canvas').bounding_box();scale=box['width']/1280
                page.mouse.move(box['x']+460*scale,box['y']+310*scale);page.mouse.down();page.mouse.move(box['x']+620*scale,box['y']+390*scale,steps=5);page.mouse.up();page.get_by_role('status').filter(has_text='重新分析').wait_for();assert ppt.state()['jobs'][0]['items'][2]['source_rect']['w']==180;page.reload();page.wait_for_function("() => P.plan?.items.length===3&&!P.busy");report['checks']['source_box_change_requires_new_visual_analysis']=True
                page.locator('#confirm-all').click();page.wait_for_function('() => P.plan.items.every(i=>i.confirmed)')
                page.locator('#plan').evaluate('(e)=>e.scrollTop=0');assert page.locator('.plan-card h3').first.bounding_box()['y']>=page.locator('#plan').bounding_box()['y']
                report['checks']['first_plan_card_fully_accessible_when_scrolled_to_top']=True
                page.screenshot(path=str(core.ROOT/'evidence/ppt-browser-plan.png'),full_page=True)
                page.locator('#execute').click();page.wait_for_function("() => P.data.jobs.some(j=>j.kind==='execution')");execution=next(j for j in ppt.state()['jobs'] if j['kind']=='execution');job=ppt.claim(execution['id']);ppt.next_item(job['id'],job['lease']);ppt.next_item(job['id'],job['lease']);image=ppt.next_item(job['id'],job['lease']);ppt.fail_item(job['id'],job['lease'],image['item']['id'],'隔离验收：没有调用模型，图片项模拟失败');out=ppt.publish(job['id'],job['lease']);page.wait_for_function("() => P.data.versions.some(v=>v.report?.partial)");page.locator('#result-summary.partial').wait_for();assert '1 项未完成' in page.locator('#result-summary').inner_text();report['checks']['partial_success_export_and_per_item_error']=True
                with page.expect_download() as download:page.locator('#export').click()
                target=Path(tmp)/'download.pptx';download.value.save_as(str(target));assert core.sha(target)==out['sha256'];pack=Package(target);assert pack.object(1,'2')['text']=='Plan 2026';assert pack.object(2,'2')['cells'][2]['text']=='2026';assert page.locator('.compare-page').count()==7;report['checks']['export_reparse_editable_text_and_all_page_comparisons']=True
                page.reload();page.wait_for_function("() => P.plan?.items.length===3");assert page.locator('#result-summary.partial').is_visible();assert page.locator('#execute').is_disabled();report['checks']['reload_preserves_progress_and_stale_baseline_not_executable']=True
                for width in (1180,880,760):
                    page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                report['checks']['responsive_1180_880_760_no_horizontal_overflow']=True
                page.screenshot(path=str(core.ROOT/'evidence/ppt-browser-result.png'),full_page=True);report['page_errors']=errors;assert not errors;browser.close()
        finally:
            http.shutdown();http.server_close();core.DATA=old
    core.dump(core.ROOT/'evidence/ppt-browser.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':run()
