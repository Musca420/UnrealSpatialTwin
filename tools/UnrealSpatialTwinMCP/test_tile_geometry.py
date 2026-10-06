import math
import unittest
from spatial_twin import geometry as g


class TileGeometryTest(unittest.TestCase):
    def test_bvh_subset_under_lwc_rotation_and_negative_scale(self):
        vertices=[];triangles=[]
        for x in range(100):
            start=len(vertices);vertices.extend([[x*100,0,0],[x*100+10,0,0],[x*100,10,0]]);triangles.append([start,start+2,start+1])
        mesh=g.Mesh(vertices,triangles)
        transform={'position':[1e9,-1e9,600],'rotation':[0,0,math.sin(.4),math.cos(.4)],'scale':[-2,3,.5]}
        box=g.transform_bounds([[4950,-10,-10],[5070,20,10]],transform)
        points,faces=g.tile_mesh(mesh,transform,box)
        expected=[[g.point(transform,vertices[i]) for i in face] for face in triangles]
        actual=[[points[i] for i in face] for face in faces]
        self.assertLess(len(faces),10)
        self.assertIn(expected[50],actual)
        for face in expected:
            bounds=[[min(p[i] for p in face) for i in range(3)],[max(p[i] for p in face) for i in range(3)]]
            if g.overlaps(box,bounds):self.assertIn(face,actual)
        self.assertEqual(actual,[f for f in expected if f in actual])
        all_points,all_faces=g.tile_mesh(mesh,transform,box,preserve_volume=True)
        self.assertEqual(all_faces,triangles);self.assertEqual(len(all_points),len(vertices))
        self.assertEqual(g.tile_mesh(mesh,transform,[[0,0,0],[1,1,1]]),([],[]))
        transform['scale'][0]=0
        self.assertEqual(g.tile_mesh(mesh,transform,box)[1],triangles)


if __name__=='__main__':unittest.main()
