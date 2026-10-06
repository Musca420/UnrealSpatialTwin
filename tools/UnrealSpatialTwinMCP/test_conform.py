import unittest
from spatial_twin.conform import support_prisms,area
from spatial_twin import geometry as g


class ConformTest(unittest.TestCase):
    def test_explicit_exclusion_preserves_opening_and_surface_height(self):
        base=[[[0,0,10],[10,0,10],[10,10,10]],[[0,0,10],[10,10,10],[0,10,10]]]
        ground=[[[0,0,0],[10,0,0],[10,10,0]],[[0,0,0],[10,10,0],[0,10,0]]]
        hole=[[2,2],[8,2],[8,8],[2,8]]
        bodies=support_prisms(base,ground,exclusion_footprints=[hole])
        self.assertAlmostEqual(sum(abs(area([[p[0],p[1]] for p in b['vertices'][:len(b['vertices'])//2]])) for b in bodies),64)
        for b in bodies:
            for p in b['vertices']:
                self.assertFalse(2<p[0]<8 and 2<p[1]<8)
        self.assertEqual(support_prisms(base,ground,exclusion_footprints=[[[0,0],[10,0],[10,10],[0,10]]]),[])
        with self.assertRaisesRegex(ValueError,'Nonconvex'):
            support_prisms(base,ground,exclusion_footprints=[[[0,0],[10,0],[5,2],[10,10],[0,10]]])
        with self.assertRaisesRegex(ValueError,'budget'):
            support_prisms(base,ground,exclusion_footprints=[hole],max_cells=1)

    def test_crossing_surfaces_use_upper_envelope_and_closed_convex_solids(self):
        bearing=[[[0,0,10],[10,0,10],[0,10,10]]]
        surfaces=[[[0,0,0],[10,0,10],[0,10,0]],[[0,0,10],[10,0,0],[0,10,10]]]
        bodies=support_prisms(bearing,surfaces)
        self.assertGreaterEqual(len(bodies),2)
        for body in bodies:
            v=body['vertices'];n=len(v)//2;edges={}
            for p in v[:n]:self.assertAlmostEqual(p[2],max(p[0],10-p[0])+.02,places=6)
            for p in v[n:]:self.assertAlmostEqual(p[2],9.98)
            for tri in body['triangles']:
                a,b,c=[v[i] for i in tri];normal=g.cross(g.sub(b,a),g.sub(c,a))
                self.assertGreater(g.length(normal),1e-10)
                self.assertTrue(all(g.dot(normal,g.sub(p,a))<=1e-7*g.length(normal) for p in v))
                for i,j in zip(tri,(*tri[1:],tri[0])):edges[tuple(sorted((i,j)))]=edges.get(tuple(sorted((i,j))),0)+1
            self.assertEqual(set(edges.values()),{2})

    def test_unknown_coverage_refuses_instead_of_bridging_gap(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN'):
            support_prisms([[[0,0,10],[10,0,10],[0,10,10]]],[[[0,0,0],[1,0,0],[0,1,0]]])

    def test_existing_embedding_omitted_and_budgets_enforced(self):
        face=[[[0,0,0],[10,0,0],[0,10,0]]]
        self.assertEqual(support_prisms(face,face),[])
        with self.assertRaisesRegex(ValueError,'body budget'):
            support_prisms([[[0,0,10],[10,0,10],[0,10,10]]],face,max_bodies=0)
        with self.assertRaisesRegex(ValueError,'horizontal'):
            support_prisms([[[0,0,9],[10,0,10],[0,10,10]]],face)


if __name__=='__main__':unittest.main()
