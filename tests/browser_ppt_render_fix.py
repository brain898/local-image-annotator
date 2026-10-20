"""Isolated genuine preview, injected failure; no image model or desktop send."""
from pathlib import Path
import sys,tempfile,threading,json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import core,server,ppt_workflow as ppt
from ppt_fixtures import make_deck, submit_mock_plan

def run():
    old=core.DATA;report={'at':core.now(),'model_called':False,'failure_injected':True,'checks':{}}
    with tempfile.TemporaryDirectory(prefix='ppt-render-browser-') as tmp:
        core.DATA=Path(tmp)/'data';core.init();fixture=make_deck(Path(tmp)/'fixture.pptx')
        with patch('ppt_workflow.schedule'):d=ppt.upload(fixture.read_bytes(),'隔离失败提示测试.pptx')
        ppt.import_deck(d['id']);assert core.read('ppt_deck',d['id'])['status']=='ready'
        j=ppt.create_analysis(dict(deck_id=d['id'],pages='1',proposal='把年份更新为2026'));j=ppt.claim(j['id'])
        item=dict(page=1,object_id='2',method='native',old='2025',new='2026',note='更新年份',evidence='隔离测试唯一原生对象')
        manifest=Path(j['folder'])/'plan.json';core.dump(manifest,dict(job_id=j['id'],lease=j['lease'],base_sha256=j['base_sha256'],request_hash=j['request_hash'],items=[item]))
        plan=submit_mock_plan(j['id'],j['lease'],manifest);body=dict(job_id=plan['id'],plan_revision=plan['plan_revision'])
        ppt.confirm(dict(body,item_ids=[plan['items'][0]['id']]));job=ppt.claim(ppt.create_execution(body)['id'])
        with patch('ppt_render.render_pair',side_effect=ValueError('隔离模拟：PowerPoint 基准重复渲染不稳定')):ppt.next_item(job['id'],job['lease'])
        version=ppt.publish(job['id'],job['lease']);assert core.read('ppt_job',job['id'])['status']=='failed'
        http=server.LocalServer(('127.0.0.1',0),server.Handler);threading.Thread(target=http.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='chrome',headless=True);page=browser.new_page(viewport={'width':1480,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{http.server_port}/ppt.html');page.wait_for_function('() => P.data?.versions.some(v=>v.report)')
                assert '没有修改项通过验收' in page.locator('#result-summary').inner_text()
                assert page.locator('#versions option:checked').inner_text()=='未完成（0 项成功）'
                assert '批量执行 · 未完成' in page.locator('#jobs').inner_text();report['checks']['zero_success_is_failed_not_partial']=True
                assert page.get_by_role('button',name='继续未完成项').first.is_visible();report['checks']['explicit_recovery_remains_available']=True
                response=page.request.get(f'http://127.0.0.1:{http.server_port}'+version['report_url'])
                assert response.status==200 and response.json()['successful']==[]
                assert response.body()==Path(version['path']).with_name('changes.json').read_bytes();report['checks']['json_report_download_exact_bytes']=True
                with page.expect_download() as result:page.locator('#export').click()
                assert '-未完成.pptx' in result.value.suggested_filename
                target=Path(tmp)/'download.pptx';result.value.save_as(str(target));assert core.sha(target)==version['sha256'];report['checks']['unchanged_download_label_and_hash']=True
                for width in (1180,760):
                    page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors;report['checks']['no_script_errors_or_horizontal_overflow']=True
                page.screenshot(path=str(core.ROOT/'evidence/ppt-render-fix-browser.png'),full_page=True);browser.close()
        finally:http.shutdown();http.server_close();core.DATA=old
    core.dump(core.ROOT/'evidence/ppt-render-fix-browser.json',report);print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
