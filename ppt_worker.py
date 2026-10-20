"""Codex-facing commands, with explicit leases and no model API client."""
import argparse, json
from pathlib import Path
import core, ppt_workflow as ppt

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['claim','context','locate','plan','next','advance','accept-image','record-model','retry-image','fail-item','fail','publish'])
    p.add_argument('job'); p.add_argument('--lease'); p.add_argument('--manifest'); p.add_argument('--item'); p.add_argument('--error')
    p.add_argument('--input')
    a=p.parse_args()
    try:
        if a.action=='claim': result=ppt.claim(a.job)
        elif not a.lease: raise ValueError('需要 claim 返回的 lease')
        elif a.action=='context': result=ppt.context(a.job,a.lease)
        elif a.action=='locate': result=ppt.locate(a.job,a.lease,a.manifest)
        elif a.action=='plan': result=ppt.write_plan(a.job,a.lease,a.manifest)
        elif a.action=='next': result=ppt.next_item(a.job,a.lease)
        elif a.action=='advance':
            while True:
                result=ppt.next_item(a.job,a.lease)
                if result.get('finished') or result.get('image_task'): break
                # Each native item is durable before moving to the next one.
        elif a.action=='accept-image': result=ppt.accept_image(a.job,a.lease,a.item)
        elif a.action=='record-model': result=ppt.record_model(a.job,a.lease,a.item,a.input)
        elif a.action=='retry-image': result=ppt.retry_image(a.job,a.lease,a.item)
        elif a.action=='fail-item': result=ppt.fail_item(a.job,a.lease,a.item,a.error or '执行失败，请检查具体要求')
        elif a.action=='fail': result=ppt.job_update(a.job,a.lease,status='failed',lease=None,error=a.error or '任务执行失败')
        else: result=ppt.publish(a.job,a.lease)
        print(json.dumps(result,ensure_ascii=False,indent=2)); return 0
    except Exception as e:
        print(json.dumps({'error':str(e),'job_id':a.job,'note':'没有自动重试；进度保留，可在网页恢复。'},ensure_ascii=False)); return 1

if __name__=='__main__': raise SystemExit(main())
