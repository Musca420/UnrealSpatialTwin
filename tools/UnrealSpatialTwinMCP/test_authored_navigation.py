import struct
import unittest
from types import SimpleNamespace
from spatial_twin.staging import ucx_navigation
from spatial_twin import geometry as g
from materialize import same_surface


class AuthoredNavigationTest(unittest.TestCase):
    def test_prediction_does_not_depend_on_triangle_winding(self):
        vertices=[[0,0,0],[2,0,0],[0,2,0],[0,0,2]]
        faces=[[0,2,1],[0,1,3],[1,2,3],[2,0,3]]
        first=ucx_navigation([SimpleNamespace(vertices=vertices,triangles=faces)])
        second=ucx_navigation([SimpleNamespace(vertices=vertices,triangles=[list(reversed(t)) for t in faces])])
        self.assertEqual(first,second)

    def test_prediction_orients_each_hull_independently_and_offsets_indices(self):
        vertices=[[0,0,0],[2,0,0],[0,2,0],[0,0,2]]
        faces=[[0,2,1],[0,1,3],[1,2,3],[2,0,3]]
        first=SimpleNamespace(vertices=vertices,triangles=faces)
        second=SimpleNamespace(vertices=[[x+100,y,z] for x,y,z in vertices],triangles=[list(reversed(t)) for t in faces])
        blob=ucx_navigation([first,second]);version,nv,nt=struct.unpack_from('<III',blob,4)
        self.assertEqual((version,nv,nt),(1,8,8))
        points=[struct.unpack_from('<3d',blob,16+24*i) for i in range(nv)]
        triangles=[struct.unpack_from('<3I',blob,16+24*nv+12*i) for i in range(nt)]
        for index,tri in enumerate(triangles):
            center=[.5+(100 if index>=4 else 0),.5,.5]
            a,b,c=(points[i] for i in tri)
            self.assertGreater(g.dot(g.cross(g.sub(b,a),g.sub(c,a)),g.sub(center,a)),0)
            self.assertEqual({i//4 for i in tri},{index//4})

    def test_native_retriangulation_allowed_but_missing_reversed_or_duplicate_surface_refused(self):
        vertices=[[0,0,0],[10,0,0],[10,10,0],[0,10,0]]
        source=SimpleNamespace(vertices=vertices,triangles=[[0,1,2],[0,2,3]])
        alternate=SimpleNamespace(vertices=vertices,triangles=[[0,1,3],[1,2,3]])
        self.assertTrue(same_surface(source,alternate))
        for faces in ([[0,1,2]],[[2,1,0],[3,2,0]],[[0,1,2],[0,2,3],[0,1,2]]):
            self.assertFalse(same_surface(source,SimpleNamespace(vertices=vertices,triangles=faces)))


if __name__=='__main__':unittest.main()
