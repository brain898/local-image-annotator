"""Unsent handoff recovery across service/browser restarts, with isolated data."""
from pathlib import Path
import io, json, sys, tempfile, threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core, server
from PIL import Image
from playwright.sync_api import sync_playwright


def run():
    previous = core.DATA
    report = {'kind': 'isolated_task_recovery', 'model_edit': False,
              'handoff_stubbed': True, 'at': core.now(), 'checks': {}}
    with tempfile.TemporaryDirectory(prefix='annotator-recovery-') as folder:
        core.DATA = Path(folder)
        images = []
        for name in ('A.png', 'B.png'):
            raw = io.BytesIO()
            Image.new('RGB', (600, 400), 'white').save(raw, format='PNG')
            image = core.upload(raw.getvalue(), name)
            core.save_draft(dict(image_id=image['id'], base_version_id=image['current_version_id'],
                                 regions=[dict(x=50, y=60, w=100, h=80, note='保留的批注')], overall='保留的共同要求'))
            images.append(image)
        drafts = core.state()['drafts']
        hashes = {v['id']: core.sha(v['path']) for v in core.state()['versions']}
        http = None

        def start():
            instance = server.LocalServer(('127.0.0.1', 0), server.Handler)
            threading.Thread(target=instance.serve_forever, daemon=True).start()
            return instance

        try:
            http = start()
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='chrome', headless=True)
                errors, handoffs = [], []

                def new_page():
                    context = browser.new_context(viewport={'width': 1450, 'height': 900})
                    page = context.new_page()
                    page.on('pageerror', lambda e: errors.append(str(e)))

                    def handoff(route):
                        handoffs.append(route.request.post_data_json['task_id'])
                        route.fulfill(json={'requested_open': False, 'sent': False})

                    page.route('**/api/handoff', handoff)
                    page.goto(f'http://127.0.0.1:{http.server_port}')
                    page.wait_for_function('() => E.bitmap && !E.loadingVersion')
                    return context, page

                def select(page, image):
                    page.locator('.image-card').filter(has=page.get_by_text(image['name'], exact=True)).click()
                    page.wait_for_function('(id) => E.image.id === id && !E.loadingVersion', arg=image['id'])

                context, page = new_page()
                select(page, images[0])
                page.locator('#submit').click()
                page.wait_for_function('() => !E.pending && E.data.tasks.length === 1')
                task = core.state()['tasks'][0]
                assert task['status'] == 'waiting' and task['attempt'] == 0
                context.close()
                http.shutdown()
                http.server_close()
                http = start()
                context, page = new_page()
                assert page.evaluate('E.image.name') == 'B.png'
                assert '另一张图片「A.png」' in page.locator('#active-task-text').inner_text()
                assert page.locator('#submit').is_hidden()
                for selector in ('#reopen-task', '#cancel-active-task'):
                    bounds = page.locator(selector).bounding_box()
                    assert bounds and bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 900
                report['checks']['restart_and_other_image_recovery_visible_without_scroll'] = True
                page.locator('#reopen-task').click()
                page.wait_for_function('() => !E.pending')
                assert handoffs == [task['id'], task['id']]
                assert len(core.state()['tasks']) == 1
                assert core.read('task', task['id'])['attempt'] == 0
                report['checks']['reopen_reuses_waiting_task_without_sending'] = True

                page.route('**/api/cancel', lambda route: route.fulfill(status=400, json={'error': '模拟取消失败'}))
                page.locator('#cancel-active-task').click()
                page.wait_for_function('() => !E.pending')
                assert page.locator('#active-task').is_visible()
                assert core.read('task', task['id'])['status'] == 'waiting'
                page.unroute('**/api/cancel')
                report['checks']['cancel_failure_keeps_task_recoverable'] = True

                page.locator('#cancel-active-task').click()
                page.wait_for_function('() => !E.pending && !busy()')
                assert core.read('task', task['id'])['status'] == 'cancelled'
                assert page.locator('#submit').is_enabled()
                select(page, images[0])
                assert page.get_by_role('textbox', name='选区 1 修改要求', exact=True).input_value() == '保留的批注'
                assert page.get_by_role('textbox', name='选区 1 修改要求', exact=True).is_enabled()
                assert {d['id']: (d['regions'], d['overall']) for d in core.state()['drafts']} == {
                    d['id']: (d['regions'], d['overall']) for d in drafts}
                assert hashes == {v['id']: core.sha(v['path']) for v in core.state()['versions']}
                report['checks']['cancel_unlocks_both_images_preserves_drafts_and_versions'] = True

                page.locator('#submit').click()
                page.wait_for_function('() => !E.pending && E.data.tasks.length === 2')
                running = core.claim(core.state()['tasks'][0]['id'])
                page.wait_for_function('() => activeTask()?.status === "running"')
                assert page.locator('#reopen-task').is_hidden()
                page.locator('#cancel-active-task').click()
                page.wait_for_function('() => !E.pending && !busy()')
                try:
                    core.fail(running['id'], running['lease'], '旧聊天迟到的结果')
                except ValueError:
                    pass
                else:
                    raise AssertionError('Cancelled worker was allowed to write')
                assert page.locator('#submit').is_enabled()
                report['checks']['resubmit_and_cancel_interrupted_worker_rejects_old_lease'] = True
                assert not errors, errors
                report['page_errors'] = errors
                browser.close()
        finally:
            if http:
                http.shutdown()
                http.server_close()
            core.DATA = previous
    core.dump(core.ROOT / 'evidence/task-recovery.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    run()
