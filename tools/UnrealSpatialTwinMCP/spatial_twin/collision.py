"""Precise collision against exported bodies, using Unreal's Chaos GJK/EPA."""
import ctypes
from . import geometry as g
from .navigation import Navigation,invoke
from .store import encode

IDENTITY={'position':[0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]}


def bodies(q,entity,other_bounds=None):
    collision=entity.get('collision',{}); transform=entity.get('collision_transform',entity['transform'])
    complex=collision.get('trace_mode')=='UseComplexAsSimple'
    coverage=collision.get('complex_coverage' if complex else 'simple_coverage')
    if coverage=='UNKNOWN':raise ValueError('Collision representation incomplete')
    hashes=collision.get('complex' if complex else 'simple',[])
    if not hashes and (complex or not collision.get('shapes')):
        if coverage=='EMPTY':return []
        raise ValueError('Collision representation missing')
    result=[]
    for digest in hashes:
        mesh=g.load_mesh(q.geometry(digest)['path'])
        if complex:
            box=other_bounds if other_bounds else g.spatial_bounds(entity)
            # Transform exact world corners to local space before the asset BVH query.
            corners=[g.local(transform,[x,y,z]) for x in (box[0][0],box[1][0]) for y in (box[0][1],box[1][1]) for z in (box[0][2],box[1][2])]
            local_box=[[min(p[i] for p in corners) for i in range(3)],[max(p[i] for p in corners) for i in range(3)]]
            for index in mesh.candidates(local_box): result.append({'vertices':[g.point(transform,mesh.vertices[k]) for k in mesh.triangles[index]],'margin':0})
        else: result.append({'vertices':[g.point(transform,p) for p in mesh.vertices],'margin':0})
    if not complex:
        for shape in collision.get('shapes',[]):
            pose=g.compose(transform,shape.get('transform',IDENTITY)); typ=shape['type']
            if typ=='box':
                e=shape['extent'];vertices=[g.point(pose,[x,y,z]) for x in (-e[0],e[0]) for y in (-e[1],e[1]) for z in (-e[2],e[2])];margin=0
            elif typ=='sphere':vertices=[g.point(transform,shape.get('center',[0,0,0]))];margin=shape['radius']*min(abs(v) for v in transform['scale'])
            elif typ=='capsule':
                margin=shape['radius']*min(abs(v) for v in pose['scale'][:2]);half=max(0,(shape['length']*.5+shape['radius'])*abs(pose['scale'][2])-margin)
                rigid={**pose,'scale':[1,1,1]};vertices=[g.point(rigid,[0,0,z]) for z in (-half,half)]
            else: raise ValueError('Unsupported collision primitive '+typ)
            result.append({'vertices':vertices,'margin':margin})
    return result


def blocks(a,b):
    ca,cb=a.get('collision',{}),b.get('collision',{})
    if not ca.get('enabled') or not cb.get('enabled') or ca.get('aggregate_only') or cb.get('aggregate_only'):return False
    return ca.get('responses',{}).get(cb.get('object_type','WorldStatic'),'Block')=='Block' and cb.get('responses',{}).get(ca.get('object_type','WorldStatic'),'Block')=='Block'


def penetration(q,a,b):
    if not blocks(a,b) or not g.overlaps(g.spatial_bounds(a),g.spatial_bounds(b)):return {'state':'READY','penetration':0,'overlap':False}
    try:
        aa,bb=bodies(q,a,g.spatial_bounds(b)),bodies(q,b,g.spatial_bounds(a))
        if not aa or not bb:
            return {'state':'READY','penetration':0,'overlap':False,'pruned_by_geometry_bvh':True}
        native=Navigation(q.store,'').native(q.db)
        native.STCollisionQuery.argtypes=ctypes.c_char_p,ctypes.c_void_p,ctypes.c_int
        native.STCollisionQuery.restype=ctypes.c_int
        return invoke(native.STCollisionQuery,encode({'origin':a['bounds'][0],'a':aa,'b':bb}))
    except (ValueError,AttributeError,OSError) as error:return {'state':'UNKNOWN','reason':str(error)}
