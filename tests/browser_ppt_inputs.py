"""Page drawing and free proposal UX. Real PPT render, handoff/model simulated."""
from pathlib import Path
import sys,tempfile,threading,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from ppt_fixtures import make_deck, submit_mock_plan
import core,server,ppt_workflow as ppt

def run():
    old=core.DATA;report={'at':core.now(),'isolated':True,'model_called':False,'handoff_stubbed':True,'checks':{}}
    with tempfile.TemporaryDirectory(prefix='ppt-input-browser-') as tmp:
        tmp=Path(tmp);core.DATA=tmp/'data';core.init();sample=make_deck(tmp/'sample.pptx')
        instance=server.LocalServer(('127.0.0.1',0),server.Handler);threading.Thread(target=instance.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1480,'height':1000});errors=[];page.on('dialog',lambda dialog:dialog.accept());page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/ppt/handoff',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'sent':False,'message':'浏览器交接桩，仍需用户在 Codex 发送'})))
                page.goto(f'http://127.0.0.1:{instance.server_port}/ppt.html');page.locator('#file').set_input_files(str(sample));page.wait_for_function("() => P.data.decks[0]?.status==='ready'",timeout=90000)
                assert page.locator('#draw-box').is_visible() and page.locator('#draw-box').is_enabled();assert page.locator('.thumb input:checked').count()==7
                page.evaluate("localStorage.setItem(draftKey(),JSON.stringify({pages:'',overall:'保留字体',per_page:'第1页：旧草稿要求'}))");page.reload();page.wait_for_function("() => document.querySelector('#proposal').value.includes('旧草稿要求')")
                assert page.locator('#proposal').input_value()=='保留字体\n第1页：旧草稿要求';assert page.locator('#overall').input_value()=='';assert page.locator('#per-page').input_value()=='';report['checks']['legacy_draft_migrates_visibly_without_hidden_duplicate_rules']=True
                proposal='第1页年份改为2026；第5页框内的2025也改成2026。\n保留倾斜效果和字体，其他页不要变。不必逐页拆开处理。'
                page.locator('#proposal').fill(proposal)
                def draw(fx,fy,fw,fh):
                    if page.locator('#draw-box').get_attribute('aria-pressed')!='true':page.locator('#draw-box').click()
                    page.locator('#page-image').scroll_into_view_if_needed();b=page.locator('#page-image').bounding_box();before=page.evaluate('P.hints.length')
                    page.mouse.move(b['x']+fx*b['width'],b['y']+fy*b['height']);page.mouse.down();page.mouse.move(b['x']+(fx+fw)*b['width'],b['y']+(fy+fh)*b['height'],steps=12);page.mouse.up()
                    page.wait_for_function(f'() => P.hints.length === {before+1}')
                draw(.08,.13,.45,.12);page.get_by_label('第 1 页框 1 修改要求',exact=True).fill('这里的2025改为2026');page.get_by_label('第 1 页框 1 修改要求',exact=True).press('Tab')
                page.get_by_alt_text('第 5 页',exact=True).click();page.wait_for_function('() => P.page===5');draw(.38,.42,.20,.18)
                page.get_by_label('第 5 页框 2 修改要求',exact=True).fill('保留倾斜效果，更新框内年份');page.get_by_label('第 5 页框 2 修改要求',exact=True).press('Tab')
                assert page.locator('#boxes .draft-box').count()==1;first=page.evaluate('P.hints[0]');s=page.evaluate('deck().structure')
                assert abs(first['box']['x']/s['width']-.08)<.002 and abs(first['box']['w']/s['width']-.45)<.002
                report['checks']['draw_before_analysis_on_native_and_rotated_pages']=True
                page.reload();page.wait_for_function('() => P.hints?.length===2');assert page.locator('#proposal').input_value()==proposal;assert page.locator('.hint-card').count()==2;assert page.get_by_label('第 5 页框 2 修改要求',exact=True).input_value()=='保留倾斜效果，更新框内年份';report['checks']['proposal_and_cross_page_boxes_survive_reload']=True
                page.locator('#show-before').check();assert page.locator('#draw-box').is_disabled();page.locator('#show-before').uncheck()
                page.locator('#next-analyze').click();page.wait_for_function('() => P.data.jobs.length===1');j=ppt.state()['jobs'][0];assert j['proposal']==proposal;assert len(j['hints'])==2 and j['pages']==list(range(1,8));assert j['hints'][0]['box']==first['box'];assert not list(core.DATA.glob('tasks/*'))
                assert page.locator('#draw-box').is_disabled();report['checks']['one_submission_contains_all_text_boxes_scope_and_no_generation']=True
                claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'mock-plan.json'
                items=[dict(page=1,object_id='2',method='native',old='2025',new='2026',note='更新年份',evidence='模拟分析，按真实原生对象唯一匹配'),dict(page=5,object_id='2',method='image',old='2025',new='2026',note='仅更新年份',evidence='模拟分析，按裁剪旋转校准后的图片源像素候选',source_rect=dict(x=440,y=295,w=230,h=100))]
                core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=items,unchanged_pages=[dict(page=n,reason='本方案不涉及此页，保留') for n in [2,3,4,6,7]]));submit_mock_plan(j['id'],claim['lease'],manifest)
                page.wait_for_function('() => P.plan?.items.length===2');assert page.locator('#kept-pages summary').inner_text()=='保留 5 页，无需修改';page.locator('#confirm-all').click();page.wait_for_function('() => P.plan.items.every(i=>i.confirmed)');assert page.locator('#execute').is_enabled();assert page.locator('#confirm-all').is_disabled()
                page.locator('#box-notes').evaluate('(e)=>e.scrollIntoView()');page.screenshot(path=str(core.ROOT/'evidence/ppt-plan-inputs.png'),full_page=True)
                page.get_by_alt_text('第 1 页',exact=True).click();draw(.08,.58,.35,.12);page.wait_for_function('() => P.plan===null');assert page.locator('#execute').is_disabled();assert all(i['confirmed'] is None for i in core.read('ppt_job',j['id'])['items']);report['checks']['new_box_invalidates_entire_old_confirmation']=True
                for width in [1180,880,760]:page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                report['checks']['responsive_no_overflow']=True;report['page_errors']=errors;assert not errors;browser.close()
        finally:instance.shutdown();instance.server_close();core.DATA=old
    core.dump(core.ROOT/'evidence/ppt-plan-input-browser.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':run()
