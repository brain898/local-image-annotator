"""Codex-facing commands; never invokes an external image API."""
import argparse, json
import core
p=argparse.ArgumentParser(); p.add_argument('action',choices=['claim','prepare','receipt','compose','fail','normalize']); p.add_argument('task'); p.add_argument('--lease'); p.add_argument('--manifest'); p.add_argument('--error');p.add_argument('--number',type=int);p.add_argument('--input');p.add_argument('--flat-text',action='store_true'); a=p.parse_args()
try:
    if a.action=='claim': result=core.claim(a.task)
    elif not a.lease: raise ValueError('需要 claim 返回的 lease')
    elif a.action=='prepare': result=core.prepare(a.task,a.lease)
    elif a.action=='receipt': result=core.receipt(a.task,a.lease)
    elif a.action=='compose': result=core.compose(a.task,a.lease,a.manifest)
    elif a.action=='normalize':
        from alignment import normalize
        result=normalize(a.task,a.lease,a.number,a.input,a.flat_text)
    else: result=core.fail(a.task,a.lease,a.error or '图片编辑工具调用失败')
    print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception as e:
    if a.lease and a.action not in ('claim','fail'):
        try: core.fail(a.task,a.lease,str(e))
        except Exception: pass
    print(json.dumps({'error':str(e)},ensure_ascii=False)); raise SystemExit(1)
