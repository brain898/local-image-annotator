"""Geometric registration with measured anchors; optional flat text extraction.
Never draws/retypes text. Only uses glyph pixels from the actual model output.
"""
from pathlib import Path
import json, math
import numpy as np
from PIL import Image
import core

def flat_text(base, generated):
    import cv2
    colors,counts=np.unique(base.reshape(-1,3),axis=0,return_counts=True);order=np.argsort(counts)[::-1]
    if len(order)<2:raise ValueError('选区没有可提取的文字前景')
    bg=colors[order[0]].astype(np.float32);fg=colors[order[1]].astype(np.float32)
    fraction=float(counts[order[0]]/base.shape[0]/base.shape[1])
    if fraction<.70 or np.linalg.norm(bg-fg)<100: raise ValueError('不是高置信度平面底色文字区域，禁止自动提取')
    edge=np.concatenate([base[:3].reshape(-1,3),base[-3:].reshape(-1,3),base[:,:3].reshape(-1,3),base[:,-3:].reshape(-1,3)])
    if np.any(np.abs(edge.astype(float)-bg).max(axis=1)>2): raise ValueError('原文字或非平面内容触碰边界，请扩大选区')
    # Estimate background from the verified empty border only. A local filter
    # mistakes the interior of a thick stroke for background and erases it.
    height,width=base.shape[:2]
    yy,xx=np.mgrid[:height,:width]
    design=np.stack([np.ones_like(xx),xx/max(width-1,1),yy/max(height-1,1)],axis=-1).astype(float)
    border=(xx<3)|(xx>=width-3)|(yy<3)|(yy>=height-3)
    coefficients=np.linalg.lstsq(design[border],generated[border].astype(float),rcond=None)[0]
    smooth=design@coefficients
    if np.max(np.abs(smooth[border]-generated[border]))>2 or np.any(smooth<0) or np.any(smooth>255.01):
        raise ValueError('模型背景不能由空白边界可靠恢复，禁止自动提取')
    vec=smooth-fg
    raw_alpha=np.sum((smooth-generated)*vec,axis=2)/np.maximum(np.sum(vec*vec,axis=2),1)
    alpha=np.clip(raw_alpha,0,1)
    reconstruction=smooth*(1-alpha[...,None])+fg*alpha[...,None]
    # Every generated pixel must agree with a background/foreground mixture.
    # This prevents silently discarding texture, new colors or uncertain glyphs.
    residual=float(np.max(np.abs(reconstruction-generated)))
    if residual>16 or np.any(raw_alpha<-.04) or np.any(raw_alpha>1.10):
        raise ValueError('模型前景或背景不满足可靠文字提取条件')
    base_vec=bg-fg
    base_alpha=np.clip(np.sum((bg-base)*base_vec,axis=2)/np.sum(base_vec*base_vec),0,1)
    if np.max(np.abs(bg*(1-base_alpha[...,None])+fg*base_alpha[...,None]-base))>2:
        raise ValueError('原图包含非文字颜色，禁止自动提取')
    alpha[alpha<.04]=0
    original_dark=np.linalg.norm(base.astype(float)-bg,axis=2)>80
    original_box=cv2.boundingRect(original_dark.astype(np.uint8));new_box=cv2.boundingRect((alpha>.08).astype(np.uint8))
    x,y,w,h=new_box;ox,oy,ow,oh=original_box
    if w<3 or h<3 or x<4 or y<4 or x+w>base.shape[1]-4 or y+h>base.shape[0]-4: raise ValueError('模型文字过小、缺失或超出安全边界')
    if abs(x-ox)>max(4,base.shape[1]*.02) or abs(y-oy)>max(4,base.shape[0]*.03) or not .8<=h/max(oh,1)<=1.2: raise ValueError('模型文字位置或字号发生变化，需要重试')
    repaired=np.rint(bg*(1-alpha[...,None])+fg*alpha[...,None]).astype(np.uint8)
    return repaired,{'method':'extract_actual_model_glyph_alpha_on_verified_flat_background','background_rgb':bg.tolist(),'foreground_rgb':fg.tolist(),'flat_fraction':fraction,'old_text_bbox':list(original_box),'model_text_bbox':list(new_box),'generated_glyphs_retyped':False,'background_model':'verified_border_affine_plane','max_reconstruction_error':residual}

def normalize(tid,lease,number,source,flat=False):
    import cv2
    with core.tx() as c: t=core.checked(c,tid,lease)
    refs=json.loads((Path(t['folder'])/'references.json').read_text(encoding='utf-8'))
    if refs['lease']!=lease: raise ValueError('参考图执行令牌不匹配')
    ref=next(a for a in refs['references'] if a['number']==number);r=ref['allowed'];rr=ref['reference']
    a4=np.array(Image.open(ref['path']).convert('RGBA'));b4=np.array(Image.open(source).convert('RGBA'))
    a=a4[:,:,:3];b=b4[:,:,:3];mask=np.full(a.shape[:2],255,np.uint8)
    x=r['x']-rr['x'];y=r['y']-rr['y'];mask[y:y+r['h'],x:x+r['w']]=0
    sift=cv2.SIFT_create(nfeatures=5000);k1,d1=sift.detectAndCompute(cv2.cvtColor(a,cv2.COLOR_RGB2GRAY),mask);k2,d2=sift.detectAndCompute(cv2.cvtColor(b,cv2.COLOR_RGB2GRAY),None)
    if d1 is None or d2 is None: raise ValueError('缺少可验证的参考锚点，不允许缩放回贴')
    matches=cv2.BFMatcher().knnMatch(d1,d2,k=2);good=[pair[0] for pair in matches if len(pair)==2 and pair[0].distance<.65*pair[1].distance]
    if len(good)<16: raise ValueError('可靠锚点少于 16 个，不能证明对齐')
    src=np.float32([k1[m.queryIdx].pt for m in good]);dst=np.float32([k2[m.trainIdx].pt for m in good]);M,inliers=cv2.estimateAffinePartial2D(src,dst,method=cv2.RANSAC,ransacReprojThreshold=2,maxIters=5000,confidence=.999)
    if M is None: raise ValueError('无法求解稳定几何变换')
    ok=inliers.ravel().astype(bool);scale=math.sqrt(float(np.linalg.det(M[:,:2])));angle=math.degrees(math.atan2(M[1,0],M[0,0]));errors=np.linalg.norm(src@M[:,:2].T+M[:,2]-dst,axis=1)/scale
    coords=src[ok];spread=np.ptp(coords,axis=0);bins=len(set((min(3,int(xx/a.shape[1]*4)),min(3,int(yy/a.shape[0]*4))) for xx,yy in coords))
    p95=float(np.quantile(errors[ok],.95));median=float(np.median(errors[ok]));expected=b.shape[1]/a.shape[1]
    if int(ok.sum())<16 or bins<4 or spread[0]<a.shape[1]*.5 or spread[1]<a.shape[0]*.5 or p95>1.5 or median>.75 or abs(angle)>.5 or abs(scale/expected-1)>.03: raise ValueError('锚点分布、残差或布局不满足对齐阈值，需要重新生成')
    warped=cv2.warpAffine(b4,cv2.invertAffineTransform(M),(a.shape[1],a.shape[0]),flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255,0))
    treatment=None
    if flat:
        if np.any(a4[y:y+r['h'],x:x+r['w'],3]!=255) or np.any(warped[y:y+r['h'],x:x+r['w'],3]!=255):raise ValueError('透明背景不能启用平面底色文字恢复')
        patch,treatment=flat_text(a[y:y+r['h'],x:x+r['w']],warped[y:y+r['h'],x:x+r['w'],:3]);warped=a4.copy();warped[y:y+r['h'],x:x+r['w'],:3]=patch
    out=Path(t['folder'])/f'normalized-{number}-attempt-{t["attempt"]}.png';core.png(out,Image.fromarray(warped).convert('RGBA'))
    proof={'task_id':tid,'lease':lease,'number':number,'source_path':str(Path(source).resolve()),'source_sha256':core.sha(source),'reference_sha256':core.sha(ref['path']),'output_path':str(out),'output_sha256':core.sha(out),'matrix_reference_to_model':M.tolist(),'model_size':[b.shape[1],b.shape[0]],'reference_size':[a.shape[1],a.shape[0]],'inlier_count':int(ok.sum()),'spatial_bins':bins,'spread':spread.tolist(),'median_error_original_px':median,'p95_error_original_px':p95,'rotation_degrees':angle,'anchors':[{'reference':s.tolist(),'model':d.tolist(),'error_original_px':float(e)} for s,d,e in zip(src[ok],dst[ok],errors[ok])],'flat_text':treatment,'accepted':True,'at':core.now()}
    proof_path=out.with_suffix('.alignment.json');core.dump(proof_path,proof)
    return {'number':number,'path':str(out),'alignment':str(proof_path),'reviewed':False,'review_note':'必须查看规范化后的模型文字及最终合成后填写审核记录','inliers':int(ok.sum()),'p95_error_original_px':p95}
