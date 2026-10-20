"""Synthetic raster sampling tests, not image model evidence."""
import unittest
import numpy as np
from ppt_validate import page_change_audit

class PageSamplingGuardTests(unittest.TestCase):
    def setUp(self):
        self.before=np.full((100,100,4),200,dtype=np.uint8);self.after=self.before.copy()
        self.points=[(20,20),(40,20),(40,30),(20,30)]
        self.after[25,25,0]=190
    def test_all_four_corners_get_the_same_three_pixel_guard(self):
        for x,y in [(17,17),(43,17),(43,33),(17,33)]:self.after[y,x,2]=199
        result=page_change_audit(self.before,self.after,self.points)
        self.assertEqual(result['page_edge_guard_changed_pixels'],4)
        self.assertEqual(result['page_edge_guard_max_channel_delta'],1)
        self.assertEqual(result['page_outside_changed_pixels'],0)
        self.assertEqual(result['page_edge_guard_pixels'],3)
    def test_single_pixel_four_pixels_away_is_still_rejected(self):
        for x,y in [(16,20),(44,20),(20,16),(20,34),(16,16)]:
            after=self.after.copy();after[y,x,2]=199
            with self.subTest(x=x,y=y),self.assertRaisesRegex(ValueError,'范围外出现 1 个'):page_change_audit(self.before,after,self.points)
    def test_rotation_does_not_allow_the_entire_bounding_box(self):
        points=[(50,20),(80,50),(50,80),(20,50)];after=self.before.copy();after[50,50,0]=190;after[17,47,2]=199
        self.assertEqual(page_change_audit(self.before,after,points)['page_edge_guard_changed_pixels'],1)
        after[20,20,2]=199
        with self.assertRaisesRegex(ValueError,'范围外'):page_change_audit(self.before,after,points)
    def test_fractional_real_case_corner_with_one_channel_one_level(self):
        before=np.full((900,1600,4),200,dtype=np.uint8);after=before.copy();after[800,1100,0]=190;after[812,1062,2]=199
        points=[(1063.3333333333333,795.8333333333334),(1173.3333333333333,795.8333333333334),(1173.3333333333333,810.8333333333334),(1063.3333333333333,810.8333333333334)]
        result=page_change_audit(before,after,points)
        self.assertEqual(result['page_edge_guard_changed_pixels'],1);self.assertEqual(result['page_edge_guard_max_channel_delta'],1)
    def test_guard_clips_to_page_edges_without_wrapping(self):
        points=[(0,0),(5,0),(5,5),(0,5)];after=self.before.copy();after[2,2,0]=190;after[8,8,2]=199
        self.assertEqual(page_change_audit(self.before,after,points)['page_edge_guard_changed_pixels'],1)
        after[-1,-1,2]=199
        with self.assertRaisesRegex(ValueError,'范围外'):page_change_audit(self.before,after,points)
    def test_only_guard_change_cannot_be_reported_as_a_visible_edit(self):
        after=self.before.copy();after[17,17,2]=199
        with self.assertRaisesRegex(ValueError,'实际修改区域显示无变化'):page_change_audit(self.before,after,self.points)
    def test_different_render_dimensions_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'尺寸变化'):page_change_audit(self.before,self.after[:-1],self.points)

if __name__=='__main__':unittest.main()
