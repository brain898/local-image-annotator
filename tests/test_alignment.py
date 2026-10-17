import unittest,tempfile,json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
import core
try:import cv2
except ImportError:cv2=None
from alignment import flat_text,normalize,reference_quality

@unittest.skipIf(cv2 is None,'optional OpenCV not installed')
class AlignmentTests(unittest.TestCase):
    def test_preflight_does_not_treat_blank_or_edited_pixels_as_anchors(self):
        im=Image.new('RGB',(300,300),'white');d=ImageDraw.Draw(im)
        for y in range(90,200,12):
            for x in range(90,200,12):d.rectangle((x,y,x+5,y+5),fill='black')
        result=reference_quality(im,dict(x=80,y=80,w=140,h=140))
        self.assertFalse(result['sufficient_for_attempt'])
        self.assertFalse(result['registration_proven'])
        self.assertEqual(result['candidate_count'],0)

    def test_flat_glyph_extraction_restores_original_background(self):
        base=np.full((100,180,3),(203,220,200),np.uint8);base[35:55,35:45]=(40,78,62);base[35:55,55:65]=(40,78,62)
        model=base.copy();gradient=np.linspace(-10,10,180)
        model=np.clip(model.astype(float)+gradient[None,:,None],0,255).astype(np.uint8)
        result,report=flat_text(base,model)
        self.assertTrue(np.all(result[:4]==(203,220,200)));self.assertFalse(report['generated_glyphs_retyped']);self.assertLess(result[40,40,0],80)
    def test_thick_and_thin_actual_glyphs_preserved(self):
        from PIL import ImageFont
        for background,foreground in [('white','black'),('#18232c','#eeeeee')]:
            for fontname in ['arial.ttf','arialbd.ttf']:
                for text in ['8','88']:
                    with self.subTest(background=background,font=fontname,text=text):
                        font=ImageFont.truetype(str(Path('C:/Windows/Fonts')/fontname),240)
                        original=Image.new('RGB',(600,400),background)
                        ImageDraw.Draw(original).text((100,65),'8',font=font,fill=foreground)
                        generated=Image.new('RGB',(600,400),background)
                        ImageDraw.Draw(generated).text((100,65),text,font=font,fill=foreground)
                        result,report=flat_text(np.array(original),np.array(generated))
                        target=np.array(generated);solid=np.all(target==np.array(Image.new('RGB',(1,1),foreground))[0,0],axis=2)
                        self.assertTrue(np.array_equal(result[solid],target[solid]))
                        self.assertLessEqual(int(np.abs(result.astype(int)-target.astype(int)).max()),11)
                        self.assertFalse(report['generated_glyphs_retyped'])
    def test_uncertain_generated_texture_rejected(self):
        base=np.full((100,180,3),240,np.uint8);base[35:55,35:45]=20
        generated=base.copy();generated[45:60,100:120]=(255,0,0)
        with self.assertRaises(ValueError):flat_text(base,generated)
    def test_nonplanar_generated_background_rejected(self):
        base=np.full((100,180,3),240,np.uint8);base[35:55,35:45]=20
        generated=base.copy();generated[:3,80:90]=210
        with self.assertRaises(ValueError):flat_text(base,generated)
    def test_textured_background_rejected(self):
        base=np.random.default_rng(1).integers(0,255,(100,180,3),dtype=np.uint8)
        with self.assertRaises(ValueError):flat_text(base,base)
    def test_glyph_on_selection_edge_rejected(self):
        base=np.full((100,180,3),220,np.uint8);base[:35,35:50]=20
        with self.assertRaises(ValueError):flat_text(base,base)
    def test_featureless_reference_cannot_be_scaled_by_guess(self):
        previous=core.DATA
        with tempfile.TemporaryDirectory() as folder:
            core.DATA=Path(folder);core.init();import io
            im=Image.new('RGB',(1024,1024),'white');b=io.BytesIO();im.save(b,format='PNG');image=core.upload(b.getvalue(),'flat.png')
            t=core.create_task(dict(image_id=image['id'],base_version_id=image['current_version_id'],request_key=core.ident(),regions=[dict(x=100,y=100,w=100,h=100,note='test')]))
            t=core.claim(t['id']);core.prepare(t['id'],t['lease']);raw=Path(t['folder'])/'raw.png';core.png(raw,im.resize((1254,1254)))
            with self.assertRaises(ValueError):normalize(t['id'],t['lease'],1,raw)
        core.DATA=previous
    def test_measured_registration_has_distributed_low_error_anchors(self):
        previous=core.DATA
        with tempfile.TemporaryDirectory() as folder:
            core.DATA=Path(folder);core.init();import io
            im=Image.new('RGB',(1024,1024),'white');d=ImageDraw.Draw(im);rng=np.random.default_rng(5)
            for _ in range(200):
                x,y=rng.integers(20,980,2);rad=int(rng.integers(4,15));color=tuple(int(a) for a in rng.integers(0,170,3));d.ellipse((x-rad,y-rad,x+rad,y+rad),fill=color)
            b=io.BytesIO();im.save(b,format='PNG');image=core.upload(b.getvalue(),'anchors.png')
            t=core.create_task(dict(image_id=image['id'],base_version_id=image['current_version_id'],request_key=core.ident(),regions=[dict(x=400,y=400,w=100,h=100,note='test')]))
            t=core.claim(t['id']);refs=core.prepare(t['id'],t['lease']);M=np.array([[1.22,0,.3],[0,1.22,.4]])
            raw=Path(t['folder'])/'raw.png';core.png(raw,Image.fromarray(cv2.warpAffine(np.array(im),M,(1254,1254),borderValue=(255,255,255))))
            result=normalize(t['id'],t['lease'],1,raw);proof=json.loads(Path(result['alignment']).read_text(encoding='utf-8'))
            self.assertGreaterEqual(proof['spatial_bins'],4);self.assertLess(proof['p95_error_original_px'],1.5);self.assertAlmostEqual(proof['matrix_reference_to_model'][0][0],1.22,places=2)
            original_registered=Path(result['path']).read_bytes()
            from unittest.mock import patch
            with patch('alignment.flat_text',side_effect=ValueError('non-flat test background')):
                fallback=normalize(t['id'],t['lease'],1,raw,flat=True)
            self.assertEqual(Path(fallback['path']).read_bytes(),original_registered)
            self.assertIn('non-flat test background',fallback['warning'])
            self.assertFalse(fallback['reviewed'])
            report=json.loads(Path(fallback['alignment']).read_text(encoding='utf-8'))
            self.assertEqual(report['flat_text']['fallback'],'registered_model_pixels')
        core.DATA=previous
