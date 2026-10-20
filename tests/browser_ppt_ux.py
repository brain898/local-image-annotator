"""Isolated Chrome regression. Genuine PowerPoint preview/native edit, stubbed handoff.
No real desktop handoff, no image model, no user data.
"""
from pathlib import Path
import sys, tempfile, threading, json, io
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from PIL import Image
import core, server, ppt_workflow as ppt
from ppt_fixtures import make_deck, submit_mock_plan
from test_list_remove import payload_hashes


def run():
    old=core.DATA
    report={'isolated':True,'model_called':False,'handoff_stubbed':True,'checks':{},'screenshots':[]}
    evidence=core.ROOT/'evidence/ppt-ux';evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ppt-ux-browser-') as tmp:
        core.DATA=Path(tmp)/'data';core.init();sample=make_deck(Path(tmp)/'fixture.pptx')
        http=server.LocalServer(('127.0.0.1',0),server.Handler)
        threading.Thread(target=http.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page(viewport={'width':1480,'height':1000});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                accept_dialog=lambda dialog:dialog.accept();page.on('dialog',accept_dialog)
                page.route('**/api/ppt/handoff',lambda route:route.fulfill(status=200,content_type='application/json',body=json.dumps({'sent':False,'message':'隔离交接桩，仍需用户在 Codex 点击发送。'})))
                base=f'http://127.0.0.1:{http.server_port}'
                def capture(state,title,button):
                    page.wait_for_function('(state)=>document.querySelector("#next-step").dataset.stage===state',arg=state)
                    assert title in page.locator('#next-step h2').inner_text()
                    assert page.locator('#next-step .primary').inner_text()==button
                    for width in (1480,1000,760):
                        page.set_viewport_size({'width':width,'height':1000});page.evaluate('scrollTo(0,0)')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(state,width)
                        if width!=760:
                            target=evidence/f'{state}-{width}.png';page.screenshot(path=str(target))
                            report['screenshots'].append({'path':str(target),'state':state,'width':width,'next_button':button})
                    page.set_viewport_size({'width':1480,'height':1000})
                page.goto(base+'/ppt.html');page.wait_for_function('()=>!P.polling')
                capture('empty','从一份 PPT 开始','导入 PPTX')
                page.locator('#dismiss-guide').click();page.reload();assert page.locator('#onboarding').is_hidden()
                # Failed upload must release same-file input.
                page.route('**/api/ppt/upload-file',lambda r:r.fulfill(status=400,content_type='application/json',body='{"error":"隔离上传失败"}'))
                page.locator('#file').set_input_files(str(sample));page.wait_for_function('()=>!P.busy&&document.querySelector("#file").value===""')
                assert '隔离上传失败' in page.locator('#notice').inner_text()
                page.unroute('**/api/ppt/upload-file')
                page.locator('#file').set_input_files(str(sample))
                page.wait_for_function('()=>deck()?.status==="ready"',timeout=120000)
                capture('draft','写下要改什么','让 Codex 分析要求')
                report['checks']['same_file_retry_and_onboarding_persistence']=True
                assert page.locator('#pages').input_value()=='全部 7 页'
                page.locator('.selection').evaluate('(e)=>e.open=true')
                page.locator('#select-all').click();page.wait_for_function('()=>selected().length===0&&!P.busy')
                assert page.locator('.thumb input:checked').count()==0
                assert page.locator('#next-analyze').is_disabled()
                assert '至少选择 1 页' in page.locator('#next-step').inner_text()
                page.locator('#select-all').click();page.wait_for_function('()=>selected().length===7&&!P.busy')
                assert page.locator('#select-all').inner_text()=='取消全选'
                page.locator('#pages').fill('1,3-4');page.locator('#pages').press('Enter')
                page.wait_for_function('()=>selected().length===3&&!P.busy')
                assert page.locator('.thumb input:checked').count()==3
                page.get_by_label('选择第 2 页',exact=True).check();page.wait_for_function('()=>selected().length===4')
                assert page.locator('#pages').input_value()=='1,2,3,4'
                page.locator('#pages').fill('0,99');page.locator('#pages').press('Enter')
                page.wait_for_function('()=>!P.busy');assert '页码越界' in page.locator('#notice').inner_text()
                page.locator('#select-all').click();page.wait_for_function('()=>selected().length===7&&!P.busy')
                report['checks']['zero_selection_toggle_blur_enter_bidirectional_range']=True
                page.locator('[data-example]').first.click();assert '2026' in page.locator('#proposal').input_value()
                # Drawing, moving and resizing use the same corner/anchor geometry as image editor.
                def drag(fr,to):
                    page.locator('#page-image').scroll_into_view_if_needed();b=page.locator('#page-image').bounding_box()
                    page.mouse.move(b['x']+fr[0]*b['width'],b['y']+fr[1]*b['height']);page.mouse.down()
                    page.mouse.move(b['x']+to[0]*b['width'],b['y']+to[1]*b['height'],steps=8);page.mouse.up()
                    page.wait_for_function('()=>!P.busy&&!P.pageDrag')
                page.locator('#draw-box').click();drag((.1,.15),(.4,.3));page.wait_for_function('()=>P.hints.length===1')
                before=page.evaluate('P.hints[0].box');drag((.25,.22),(.35,.32));moved=page.evaluate('P.hints[0].box')
                assert moved['x']>before['x'] and moved['w']==before['w']
                drag((.5,.4),(.6,.5));resized=page.evaluate('P.hints[0].box');assert resized['w']>moved['w']
                page.get_by_label('第 1 页框 1 修改要求',exact=True).fill('更新年份')
                page.get_by_label('第 1 页框 1 修改要求',exact=True).press('Tab')
                page.locator('#next-page').click();assert page.evaluate('P.page')==2
                page.locator('#next-page').press('ArrowRight');assert page.evaluate('P.page')==3
                assert page.locator('#page-name').inner_text()=='第 3 / 7 页'
                report['checks']['page_box_draw_move_resize_pager_keyboard']=True
                page.evaluate('window.thumbNode=document.querySelector(".thumb")')
                page.wait_for_timeout(3500);assert page.evaluate('thumbNode===document.querySelector(".thumb")')
                page.locator('#next-analyze').click();page.wait_for_function('()=>activeJobs().length===1&&!P.busy')
                capture('waiting','已在 Codex 打开草稿','重新打开 Codex')
                assert page.locator('#editing-area').is_hidden()
                j=ppt.state()['jobs'][0]
                before=payload_hashes()
                try:core.remove_from_list('ppt_deck',j['deck_id']);raise AssertionError('active deck removed')
                except ValueError:pass
                assert before==payload_hashes()
                page.evaluate('window.jobNode=document.querySelector(".job");window.thumbNode=document.querySelector(".thumb")')
                page.wait_for_timeout(3500)
                assert page.evaluate('jobNode===document.querySelector(".job")&&thumbNode===document.querySelector(".thumb")')
                report['checks']['waiting_hides_editor_and_polling_preserves_nodes']=True
                poll_requests=[];watch=lambda request:poll_requests.append(request.url) if request.url.endswith('/api/ppt/state') else None
                page.on('request',watch);page.wait_for_function('()=>!P.polling')
                page.evaluate('Object.defineProperty(document,"hidden",{configurable:true,value:true})')
                page.wait_for_timeout(3500);assert not poll_requests
                page.evaluate('delete document.hidden;document.dispatchEvent(new Event("visibilitychange"))')
                page.wait_for_function('()=>!P.polling');assert poll_requests
                page.remove_listener('request',watch)
                report['checks']['hidden_page_pauses_polling_and_visibility_resumes']=True
                claim=ppt.claim(j['id']);manifest=Path(j['folder'])/'ux-plan.json'
                core.dump(manifest,dict(job_id=j['id'],lease=claim['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=[dict(page=1,object_id='2',method='native',old='2025',new='2026',note='更新年份',evidence='模拟分析。preview_path / source_path / 1e-6 仅用于技术依据')],unchanged_pages=[dict(page=n,reason='本次不涉及') for n in range(2,8)]))
                plan=submit_mock_plan(j['id'],claim['lease'],manifest)
                page.wait_for_function('()=>P.plan?.items.length===1')
                capture('plan','核对右侧 1 项','核对并确认清单')
                assert page.get_by_text('技术依据',exact=True).is_visible()
                page.locator('#confirm-all').click();page.wait_for_function('()=>P.plan.items[0].confirmed&&!P.busy')
                assert page.locator('.plan-card.confirmed .confirmed-badge').inner_text()=='已确认'
                # Scope cancellation preserves confirmations, accepted change invalidates them.
                def dismiss(dialog):dialog.dismiss()
                page.remove_listener('dialog',accept_dialog);page.on('dialog',dismiss)
                page.get_by_label('选择第 1 页',exact=True).click()
                assert page.evaluate('selected().length')==7
                assert page.evaluate('!!P.plan.items[0].confirmed')
                page.remove_listener('dialog',dismiss);page.on('dialog',accept_dialog)
                report['checks']['confirmed_scope_change_has_cancelable_guard']=True
                page.locator('#next-step .primary').click();page.wait_for_function('()=>activeJobs()[0]?.kind==="execution"&&!P.busy')
                execution=next(j for j in ppt.state()['jobs'] if j['kind']=='execution');job=ppt.claim(execution['id'])
                ppt.next_item(job['id'],job['lease']);out=ppt.publish(job['id'],job['lease'])
                page.wait_for_function('()=>stage()==="complete"',timeout=120000)
                capture('complete','成功 1 项，未完成 0 项','导出 PPTX')
                assert '这份清单基于旧版本，已失效' in page.locator('#plan-status').inner_text()
                assert page.locator('#execute').is_disabled()
                assert '上一轮要求已归档' in page.locator('#notice').inner_text()
                assert page.get_by_role('button',name='带回上一轮要求',exact=True).is_visible()
                page.get_by_role('button',name='查看对比',exact=True).click()
                assert page.locator('#comparisons>.compare-page').count()==1
                assert page.locator('.unchanged-pages summary').inner_text()=='未修改 6 页'
                page.locator('#comparisons>.compare-page img').first.click();assert page.locator('#zoom-dialog').is_visible();page.locator('#close-zoom').click()
                with page.expect_download() as result:page.locator('#next-step .primary').click()
                target=Path(tmp)/'download.pptx';result.value.save_as(str(target));assert core.sha(target)==out['sha256']
                page.evaluate('scrollTo(0,document.body.scrollHeight);notice("隔离持久错误",true)')
                page.wait_for_timeout(7100);assert page.locator('#notice').is_visible()
                box=page.locator('#notice').bounding_box();assert box['y']>=0 and box['y']+box['height']<=1000
                report['checks']['stale_explanation_archive_changed_page_comparison_zoom_export_and_sticky_error']=True
                # The previous request survives reload, and bringing it back invalidates old confirmations explicitly.
                page.reload();page.wait_for_function('()=>stage()==="complete"')
                assert page.locator('#proposal').input_value()==''
                assert page.get_by_role('button',name='带回上一轮要求',exact=True).is_visible()
                page.get_by_role('button',name='带回上一轮要求',exact=True).click()
                page.wait_for_function('()=>P.plan===null&&stage()==="draft"')
                assert '2026' in page.locator('#proposal').input_value()
                assert page.evaluate('P.hints.length')==1
                assert all(not i.get('confirmed') for i in core.read('ppt_job',j['id'])['items'])
                page.locator('.selection').evaluate('(e)=>e.open=true')
                page.locator('#select-all').click();page.wait_for_function('()=>selected().length===0&&!P.busy')
                page.reload();page.wait_for_function('()=>deck()?.status==="ready"')
                assert page.evaluate('selected().length')==0
                report['checks']['archive_restore_and_zero_selection_survive_reload']=True
                # Same topbar geometry/typography on both pages, at all requested widths.
                other=browser.new_page();other.on('pageerror',lambda e:errors.append('image page: '+str(e)));other.goto(base+'/')
                def header_info(pg):return pg.locator('.topbar').evaluate('(e)=>{const s=getComputedStyle(e);return [e.getBoundingClientRect().height,s.backgroundColor,s.fontFamily,getComputedStyle(e.querySelector("#upload")).backgroundColor]}')
                for width in (1480,1180,1000,760):
                    page.set_viewport_size({'width':width,'height':1000});other.set_viewport_size({'width':width,'height':1000})
                    assert header_info(page)==header_info(other)
                    assert other.evaluate('document.documentElement.scrollWidth<=innerWidth')
                report['checks']['shared_topbar_and_responsive_1480_1000_760']=True
                # Image list removal, only synthetic PNGs.
                raw=io.BytesIO();Image.new('RGB',(70,40),'white').save(raw,format='PNG')
                first=core.upload(raw.getvalue(),'P18.png');second=core.upload(raw.getvalue(),'P18.png')
                other.reload();other.wait_for_function('()=>E.data.images.length===2');other.on('dialog',lambda dialog:dialog.accept())
                with core.tx() as c:core.put(c,'task',dict(id='synthetic-list-guard',image_id=second['id'],status='waiting',mode='probe',regions=[],base_version_id=second['current_version_id']))
                other.wait_for_function('()=>E.data.tasks.some(t=>t.id==="synthetic-list-guard")')
                other.wait_for_function('(id)=>document.querySelector(`[data-remove-image="${id}"]`)?.textContent.includes("无法移除")',arg=second['id'])
                other.locator('.entry-menu summary').first.click()
                assert other.locator('[data-remove-image="'+second['id']+'"]').is_disabled()
                assert '无法移除' in other.locator('[data-remove-image="'+second['id']+'"]').inner_text()
                with core.tx() as c:
                    task=core.get(c,'task','synthetic-list-guard');task['status']='completed';core.put(c,'task',task)
                other.wait_for_function('()=>E.data.tasks.find(t=>t.id==="synthetic-list-guard")?.status==="completed"')
                assert other.locator('[data-remove-image="'+second['id']+'"]').is_enabled()
                before=payload_hashes();other.get_by_role('button',name='从列表移除',exact=True).first.click()
                other.wait_for_function('()=>E.data.images.length===1');assert before==payload_hashes()
                assert len(core.state()['images'])==1
                other.wait_for_function('()=>E.image?.id===E.data.images[0].id&&E.bitmap!==null')
                assert other.locator('.version-card').count()==1
                other.locator('.entry-menu summary').click();other.get_by_role('button',name='从列表移除',exact=True).click()
                other.wait_for_function('()=>E.data.images.length===0&&E.image===null')
                assert other.locator('#empty').is_visible() and other.locator('.version-card').count()==0
                assert before==payload_hashes()
                # Deck removal cleans only its draft keys, retains job/version records and every payload byte.
                page.evaluate('localStorage.setItem("local-ppt-"+P.deck+"-probe","saved");localStorage.setItem("other-draft","keep")')
                # A second ready fixture record exercises automatic selection without another render/model call.
                did=j['deck_id'];clone=core.read('ppt_deck',did).copy();clone.update(id=core.ident(),name='下一份隔离 PPT.pptx',active_plan=None)
                clone_version=core.read('ppt_version',clone['original_version_id']).copy();clone_version.update(id=core.ident(),deck_id=clone['id'])
                clone.update(current_version_id=clone_version['id'],original_version_id=clone_version['id'])
                with core.tx() as c:core.put(c,'ppt_deck',clone);core.put(c,'ppt_version',clone_version)
                page.evaluate('sync()');page.wait_for_function('()=>P.data.decks.length===2')
                before=payload_hashes();jobs=ppt.state()['jobs'];versions=ppt.state()['versions']
                page.locator('#decks .library-entry').filter(has=page.locator('.active')).locator('summary').click();page.get_by_role('button',name='从列表移除',exact=True).click()
                page.wait_for_function('(id)=>P.data.decks.length===1&&P.deck===id&&!P.busy',arg=clone['id'])
                assert before==payload_hashes()
                page.locator('#decks .entry-menu summary').click();page.get_by_role('button',name='从列表移除',exact=True).click()
                page.wait_for_function('()=>P.data.decks.length===0&&!P.busy')
                assert before==payload_hashes();assert ppt.state()['jobs']==jobs;assert ppt.state()['versions']==versions
                assert page.evaluate('(id)=>!Object.keys(localStorage).some(k=>k.startsWith("local-ppt-"+id+"-"))',did)
                assert page.evaluate('localStorage.getItem("other-draft")')=='keep'
                assert page.locator('#next-step').get_attribute('data-stage')=='empty'
                report['checks']['image_and_ppt_remove_keep_all_payload_hashes_and_db_history']=True
                report['payload_hashes_after_removal']=before;report['database_hash_exclusion']='Only store.sqlite and its WAL/SHM are excluded: database visibility flags necessarily change. All payload paths and hashes match.'
                report['page_errors']=errors;assert not errors
                browser.close()
        finally:http.shutdown();http.server_close();core.DATA=old
    core.dump(evidence/'report.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
