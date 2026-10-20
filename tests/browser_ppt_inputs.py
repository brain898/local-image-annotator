"""Page drawing and free proposal UX. Real PPT render, handoff/model simulated."""
from pathlib import Path
import sys,tempfile,threading,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from ppt_fixtures import make_deck, submit_mock_plan
import core,server,ppt_workflow as ppt

def page_requirements_checks(page,tmp,report,accept_dialog):
    page.set_viewport_size({'width':1480,'height':1000})
    # Prefer structured drafts, including multiline text, over the legacy carrier.
    page.evaluate("localStorage.setItem(draftKey(),JSON.stringify({selectedPages:[2],proposal:'',page_reqs:{2:'结构化要求\\n保留配色'},per_page:'第 1 页：忽略旧载体',hints:[]}))")
    page.reload();page.wait_for_function("()=>P.pageReqs[2]?.includes('保留配色')")
    assert page.evaluate('Object.keys(P.pageReqs)')==['2']
    assert page.locator('#per-page').input_value()=='第 2 页：结构化要求；保留配色'
    report['checks']['structured_draft_precedes_legacy_and_flattens_newlines']=True
    # Start a clean draft in the same isolated deck, with all pages selected.
    page.evaluate("localStorage.setItem(draftKey(),JSON.stringify({selectedPages:[1,2,3,4,5,6,7],proposal:'',page_reqs:{},hints:[]}))")
    page.reload();page.wait_for_function("()=>deck()?.status==='ready'&&!P.busy")
    page.locator('#select-all').click();page.wait_for_function('()=>selected().length===0&&!P.busy')
    req=page.locator('#page-req')
    req.fill('  标题年份改为 2026  ');assert page.evaluate('selected()')==[1]
    page.locator('#next-page').click();assert page.locator('#page-req-label').inner_text()=='第 2 页要求';assert req.input_value()==''
    req.fill('日期更新为 10 月 20 日');assert page.evaluate('selected()')==[1,2]
    page.get_by_alt_text('第 5 页',exact=True).click();req.fill('保留倾斜效果')
    req.evaluate("e=>{const d=new DataTransfer();d.setData('text','\\n年份改为 2026');e.setSelectionRange(e.value.length,e.value.length);e.dispatchEvent(new ClipboardEvent('paste',{clipboardData:d,bubbles:true,cancelable:true}));}")
    assert req.input_value()=='保留倾斜效果；年份改为 2026'
    page.locator('#next-page').press('ArrowLeft');assert page.evaluate('P.page')==4;assert req.input_value()==''
    page.locator('#next-page').press('ArrowRight');assert page.evaluate('P.page')==5;assert req.input_value()=='保留倾斜效果；年份改为 2026'
    assert page.locator('.page-req-dot').count()==3
    # Selected pages without requirements stay selected but are excluded from page-only analysis.
    page.get_by_label('选择第 7 页',exact=True).check()
    assert page.evaluate('selected()')==[1,2,5,7]
    assert page.locator('#next-step h2').inner_text()=='将分析 3 页';assert page.locator('#analyze').is_enabled()
    page.locator('#page-req-summary summary').click()
    assert page.locator('.page-req-row').count()==3
    page.locator('.page-req-jump').nth(1).click();assert page.evaluate('P.page')==2;assert req.input_value()=='日期更新为 10 月 20 日'
    page.get_by_role('button',name='删除第 2 页要求',exact=True).click();assert page.locator('.page-req-dot').count()==2;assert req.input_value()==''
    req.fill('日期更新为 10 月 20 日')
    # Cancelling a deselection keeps both the page requirement and selection.
    page.remove_listener('dialog',accept_dialog)
    dismiss=lambda dialog:dialog.dismiss();page.on('dialog',dismiss)
    page.get_by_label('选择第 2 页',exact=True).click()
    assert page.evaluate('selected().includes(2)&&!!P.pageReqs[2]')
    page.remove_listener('dialog',dismiss);page.on('dialog',accept_dialog)
    page.get_by_label('选择第 2 页',exact=True).click();page.wait_for_function('()=>!P.busy&&!P.pageReqs[2]')
    assert not page.evaluate('selected().includes(2)');req.fill('日期更新为 10 月 20 日');assert page.evaluate('selected().includes(2)')
    report['checks']['cross_page_input_pager_thumbnail_keyboard_dots_summary_delete_and_scope_guard']=True
    expected='第 1 页：标题年份改为 2026\n第 2 页：日期更新为 10 月 20 日\n第 5 页：保留倾斜效果；年份改为 2026'
    assert page.locator('#per-page').input_value()==expected
    saved=page.evaluate('JSON.parse(localStorage.getItem(draftKey()))');assert len(saved['page_reqs'])==3 and saved['per_page']==expected
    page.reload();page.wait_for_function('()=>Object.keys(P.pageReqs).length===3')
    page.locator('#page-req-summary').evaluate('e=>e.open=true')
    for width in (1480,1000,760):
        page.set_viewport_size({'width':width,'height':1000});page.evaluate("scrollTo(0,0);document.querySelector('#notice').hidden=true")
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        if width!=1000:page.screenshot(path=str(core.ROOT/f'evidence/ui-slim/page-requirements-{width}.png'))
    page.set_viewport_size({'width':1480,'height':1000})
    payloads=[]
    def capture(request):
        if request.url.endswith('/api/ppt/analyze'):payloads.append(request.post_data_json)
    page.on('request',capture)
    def analyze_and_plan():
        page.locator('#analyze').click();page.wait_for_function('()=>activeJobs()[0]?.kind==="analysis"&&!P.busy')
        job=core.read('ppt_job',page.evaluate('activeJobs()[0].id'))
        assert page.locator('#page-req').is_disabled()
        claim=ppt.claim(job['id']);page.evaluate('sync()');page.wait_for_function('()=>stage()==="analyzing"');assert req.is_disabled()
        manifest=Path(job['folder'])/'page-requirements-plan.json'
        core.dump(manifest,dict(job_id=job['id'],lease=claim['lease'],base_sha256=job['base_sha256'],request_hash=job['request_hash'],items=[dict(page=1,object_id='2',method='native',old='2025',new='2026',note='更新年份',evidence='隔离原生修改测试桩')],unchanged_pages=[dict(page=n,reason='隔离测试保留') for n in job['pages'] if n!=1]))
        submit_mock_plan(job['id'],claim['lease'],manifest);page.wait_for_function('()=>P.plan?.items.length===1&&stage()==="plan"')
        return job
    first=analyze_and_plan();assert payloads[-1]['pages']=='1,2,5' and payloads[-1]['per_page']==expected and payloads[-1]['proposal']==''
    assert first['pages']==[1,2,5] and set(first['requirements'])=={'1','2','5'}
    report['checks']['page_only_real_backend_accepts_sorted_per_page_and_intersection_scope']=True
    page.locator('#confirm-all').click();page.wait_for_function('()=>P.plan.items[0].confirmed&&!P.busy')
    page.get_by_alt_text('第 1 页',exact=True).click();before=req.input_value()
    page.remove_listener('dialog',accept_dialog);page.on('dialog',dismiss)
    req.fill('取消此次修改');assert req.input_value()==before;assert page.evaluate('!!P.plan.items[0].confirmed')
    page.remove_listener('dialog',dismiss);page.on('dialog',accept_dialog)
    req.fill('标题年份改为 2026，保留字号')
    page.wait_for_function('()=>P.plan===null&&!P.pageReqInvalidating')
    assert not core.read('ppt_job',first['id'])['items'][0]['confirmed'];assert page.locator('#execute').is_disabled()
    report['checks']['page_requirement_change_invalidates_confirmations']=True
    analyze_and_plan();page.locator('#confirm-all').click();page.wait_for_function('()=>P.plan.items[0].confirmed&&!P.busy')
    page.locator('#execute').click();page.wait_for_function('()=>activeJobs()[0]?.kind==="execution"&&!P.busy');assert req.is_disabled()
    execution=ppt.claim(page.evaluate('activeJobs()[0].id'));page.evaluate('sync()');page.wait_for_function('()=>stage()==="executing"');assert req.is_disabled()
    ppt.next_item(execution['id'],execution['lease']);ppt.publish(execution['id'],execution['lease'])
    page.wait_for_function('()=>stage()==="complete"',timeout=120000)
    assert page.evaluate('Object.keys(P.pageReqs).length')==0
    assert page.evaluate('Object.keys(P.archive.page_reqs).length')==3
    page.reload();page.wait_for_function('()=>stage()==="complete"')
    page.locator('#restore-request').click();page.wait_for_function('()=>stage()==="draft"&&P.plan===null')
    assert page.locator('#proposal').input_value()=='' and page.evaluate('Object.keys(P.pageReqs).length')==3
    assert '保留字号' in page.locator('#per-page').input_value()
    report['checks']['version_archive_reload_and_restore_keep_structured_page_requirements']=True
    # With an overall proposal, selected pages continue to be submitted together.
    page.get_by_label('选择第 7 页',exact=True).check()
    page.locator('#proposal').fill('全篇保留现有布局')
    page.locator('#analyze').click();page.wait_for_function('()=>activeJobs().length===1&&!P.busy')
    assert payloads[-1]['pages']=='1,2,5,7' and payloads[-1]['proposal']=='全篇保留现有布局'
    page.remove_listener('request',capture)
    report['checks']['overall_proposal_preserves_selected_scope']=True

def run():
    old=core.DATA;report={'at':core.now(),'isolated':True,'model_called':False,'handoff_stubbed':True,'checks':{}}
    with tempfile.TemporaryDirectory(prefix='ppt-input-browser-') as tmp:
        tmp=Path(tmp);core.DATA=tmp/'data';core.init();sample=make_deck(tmp/'sample.pptx')
        instance=server.LocalServer(('127.0.0.1',0),server.Handler);threading.Thread(target=instance.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1480,'height':1000});errors=[];accept_dialog=lambda dialog:dialog.accept();page.on('dialog',accept_dialog);page.on('pageerror',lambda e:errors.append(str(e)))
                page.route('**/api/ppt/handoff',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'sent':False,'message':'浏览器交接桩，仍需用户在 Codex 发送'})))
                page.goto(f'http://127.0.0.1:{instance.server_port}/ppt.html');page.locator('#file').set_input_files(str(sample));page.wait_for_function("() => P.data.decks[0]?.status==='ready'",timeout=90000)
                assert page.locator('#draw-box').is_visible() and page.locator('#draw-box').is_enabled();assert page.locator('.thumb input:checked').count()==7
                page.evaluate("localStorage.setItem(draftKey(),JSON.stringify({pages:'',overall:'保留字体',per_page:'第1页：旧草稿要求\\n无法解析的旧要求'}))");page.reload();page.wait_for_function("()=>P.pageReqs[1]==='旧草稿要求'")
                assert page.locator('#proposal').input_value()=='保留字体\n无法解析的旧要求';assert page.locator('#overall').input_value()=='';assert page.locator('#per-page').input_value()=='第 1 页：旧草稿要求';report['checks']['legacy_per_page_parsed_and_unparsed_lines_preserved']=True
                page.evaluate("localStorage.setItem(draftKey(),JSON.stringify({proposal:'现有要求',overall:'保留字体',per_page:'第1页：旧草稿要求'}))");page.reload();page.wait_for_function("()=>P.pageReqs[1]==='旧草稿要求'");page.reload();page.wait_for_function("()=>P.pageReqs[1]==='旧草稿要求'");assert page.locator('#proposal').input_value()=='现有要求\n保留字体';assert page.locator('#per-page').is_hidden()
                report['checks']['mixed_legacy_draft_parses_once_and_survives_reload']=True
                page.evaluate("P.pageReqs={};syncPageReqCarrier();saveDraft();renderThumbs();renderPageReqs()")
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
                page.locator('#analyze').click();page.wait_for_function('() => P.data.jobs.length===1');j=ppt.state()['jobs'][0];assert j['proposal']==proposal;assert len(j['hints'])==2 and j['pages']==list(range(1,8));assert j['hints'][0]['box']==first['box'];assert not list(core.DATA.glob('tasks/*'))
                assert page.locator('#draw-box').is_disabled();report['checks']['one_submission_contains_all_text_boxes_scope_and_no_generation']=True
                claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'mock-plan.json'
                items=[dict(page=1,object_id='2',method='native',old='2025',new='2026',note='更新年份',evidence='模拟分析，按真实原生对象唯一匹配'),dict(page=5,object_id='2',method='image',old='2025',new='2026',note='仅更新年份',evidence='模拟分析，按裁剪旋转校准后的图片源像素候选',source_rect=dict(x=440,y=295,w=230,h=100))]
                core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=items,unchanged_pages=[dict(page=n,reason='本方案不涉及此页，保留') for n in [2,3,4,6,7]]));submit_mock_plan(j['id'],claim['lease'],manifest)
                page.wait_for_function('() => P.plan?.items.length===2');assert page.locator('#kept-pages summary').inner_text()=='保留 5 页，无需修改';page.locator('#confirm-all').click();page.wait_for_function('() => P.plan.items.every(i=>i.confirmed)');assert page.locator('#execute').is_enabled();assert page.locator('#confirm-all').is_disabled()
                page.locator('#box-notes').evaluate('(e)=>e.scrollIntoView()');page.screenshot(path=str(core.ROOT/'evidence/ppt-plan-inputs.png'),full_page=True)
                page.get_by_alt_text('第 1 页',exact=True).click();draw(.08,.58,.35,.12);page.wait_for_function('() => P.plan===null');assert page.locator('#execute').is_disabled();assert all(i['confirmed'] is None for i in core.read('ppt_job',j['id'])['items']);report['checks']['new_box_invalidates_entire_old_confirmation']=True
                for width in [1180,880,760]:page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                report['checks']['responsive_no_overflow']=True
                page_requirements_checks(page,tmp,report,accept_dialog)
                report['page_errors']=errors;assert not errors;browser.close()
        finally:instance.shutdown();instance.server_close();core.DATA=old
    core.dump(core.ROOT/'evidence/ppt-plan-input-browser.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':run()
