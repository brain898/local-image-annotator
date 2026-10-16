"""Isolated real Chrome regression for failed submissions. Never opens Codex."""
from pathlib import Path
import sys,tempfile,threading,json,io
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import core,server
from PIL import Image
from playwright.sync_api import sync_playwright

def run():
    previous=core.DATA
    report={'synthetic_test':True,'at':core.now(),'observations':{}}
    with tempfile.TemporaryDirectory(prefix='annotator-request-') as folder:
        core.DATA=Path(folder);core.init()
        images=[]
        for name in ['A','B']:
            b=io.BytesIO();Image.new('RGB',(600,400),'white').save(b,format='PNG')
            im=core.upload(b.getvalue(),name+'.png');images.append(im)
            core.save_draft(dict(image_id=im['id'],base_version_id=im['current_version_id'],regions=[dict(x=100,y=80,w=130,h=100,note=name+' draft')],overall=name))
        http=server.LocalServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome',headless=True)
                page=browser.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                # Hide task polling to model an unknown server outcome after a
                # lost response; real task POSTs and SQLite idempotency still run.
                def state(route):
                    response=route.fetch();data=response.json();data['tasks']=[]
                    route.fulfill(response=response,json=data)
                page.route('**/api/state',state)
                handoffs=[]
                def handoff(route):
                    handoffs.append(route.request.post_data_json['task_id']);route.fulfill(json={'requested_open':False,'synthetic_test':True})
                page.route('**/api/handoff',handoff)
                requests=[];mode={'value':'abort'}
                def tasks(route):
                    requests.append(route.request.post_data_json)
                    if mode['value']=='abort':route.abort('failed')
                    elif mode['value']=='lost':route.fetch();route.abort('failed')
                    else:route.continue_()
                page.route('**/api/tasks',tasks)
                page.goto(f'http://127.0.0.1:{http.server_port}');page.wait_for_function('() => E.bitmap !== null')
                def select(im):
                    page.locator('.image-card').filter(has=page.get_by_text(im['name'],exact=True)).click()
                    page.wait_for_function('(id)=>E.image.id===id && E.view===E.image.current_version_id && !E.loadingVersion',arg=im['id'])
                def submit():
                    page.locator('#submit').click();page.wait_for_function('() => !E.pending')
                select(images[0]);submit();a=requests[-1]
                assert a['image_id']==images[0]['id']
                select(images[1]);mode['value']='lost';submit();b=requests[-1]
                assert b['image_id']==images[1]['id'] and b['regions'][0]['note']=='B draft' and b['request_key']!=a['request_key']
                committed=core.state()['tasks'];assert len(committed)==1 and committed[0]['image_id']==images[1]['id']
                report['observations']['A_failure_then_B_creates_only_B']=True
                mode['value']='success';submit()
                assert requests[-1]==b and len(core.state()['tasks'])==1
                assert handoffs==[committed[0]['id']]
                report['observations']['lost_response_retry_same_key_and_one_task']=True
                core.cancel(committed[0]['id'])
                select(images[0]);mode['value']='abort';submit();assert requests[-1]==a
                report['observations']['switch_back_keeps_A_retry_key']=True
                page.locator('#overall').fill('A changed');submit();changed=requests[-1]
                assert changed['request_key']!=a['request_key'] and changed['overall']=='A changed'
                report['observations']['changed_draft_gets_new_key']=True
                original=core.read('version',images[0]['current_version_id']);new={**original,'id':core.ident(),'label':'Synthetic second base'}
                with core.tx() as c:core.put(c,'version',new)
                core.restore(images[0]['id'],new['id'])
                page.wait_for_function('(id) => E.view===id && !E.loadingVersion',arg=new['id'])
                select(images[1])
                core.save_draft(dict(image_id=images[0]['id'],base_version_id=new['id'],regions=a['regions'],overall=a['overall']))
                page.wait_for_function('(id) => E.data.drafts.some(d=>d.id===id && d.regions.length)',arg=images[0]['id']);select(images[0])
                page.locator('#regions textarea').fill('A draft');page.locator('#overall').fill('A');submit()
                assert requests[-1]['base_version_id']==new['id'] and requests[-1]['request_key']!=a['request_key']
                report['observations']['new_base_never_reuses_old_request']=True
                assert not errors;report['page_errors']=errors;report['observations']['desktop_handoff_intercepted']=True
                browser.close()
        finally:
            http.shutdown();http.server_close();thread.join();core.DATA=previous
    target=core.ROOT/'evidence/review-fixes-browser.json';core.dump(target,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':run()

