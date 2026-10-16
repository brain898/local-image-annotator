"""Offline OCR using installed RapidOCR and already-present local ONNX models only."""
from pathlib import Path
import json, sys, math

def recognize(path):
    import rapidocr
    from rapidocr import RapidOCR
    root=Path(rapidocr.__file__).parent/'models'
    names=['ch_PP-OCRv4_det_infer.onnx','ch_ppocr_mobile_v2.0_cls_infer.onnx','ch_PP-OCRv4_rec_infer.onnx']
    paths=[root/n for n in names]
    if not all(p.is_file() for p in paths):raise ValueError('本地 OCR 模型缺失；不会联网下载，请安装本地模型后重试')
    engine=RapidOCR(params={k+'.model_path':str(p) for k,p in zip(['Det','Cls','Rec'],paths)})
    result=engine(path)
    from PIL import Image
    w,h=Image.open(path).size
    rows=[]
    if result.boxes is not None:
        for box,text,score in zip(result.boxes,result.txts,result.scores):
            polygon=[[float(a),float(b)] for a,b in box]
            x=max(0,math.floor(min(a for a,b in polygon)));y=max(0,math.floor(min(b for a,b in polygon)))
            right=min(w,math.ceil(max(a for a,b in polygon)));bottom=min(h,math.ceil(max(b for a,b in polygon)))
            if right-x>=2 and bottom-y>=2:rows.append({'x':x,'y':y,'w':right-x,'h':bottom-y,'polygon':polygon,'text':text,'score':float(score)})
    return {'engine':'RapidOCR local ONNX','width':w,'height':h,'rows':rows}

if __name__=='__main__':
    try:Path(sys.argv[2]).write_text(json.dumps(recognize(sys.argv[1]),ensure_ascii=False),encoding='utf-8')
    except Exception as e:Path(sys.argv[2]).write_text(json.dumps({'error':str(e)},ensure_ascii=False),encoding='utf-8');sys.exit(1)
