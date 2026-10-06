"""Double-precision transforms and a deduplicated triangle BVH, never a world scan."""
import math
import hashlib
from pathlib import Path
import struct
from functools import lru_cache
from .store import vector, bounds


def spatial_bounds(entity):
    """Derived conservative index extent; preserve native render/collision source boxes."""
    render=entity.get('bounds');collision=entity.get('collision_bounds')
    if not render:return collision
    if not collision:return render
    return [[min(render[0][i],collision[0][i]) for i in range(3)],
            [max(render[1][i],collision[1][i]) for i in range(3)]]


def add(a,b): return [x+y for x,y in zip(a,b)]
def sub(a,b): return [x-y for x,y in zip(a,b)]
def mul(a,s): return [x*s for x in a]
def dot(a,b): return sum(x*y for x,y in zip(a,b))
def cross(a,b): return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def length(v): return math.sqrt(dot(v,v))
def unit(v):
    n=length(v)
    if n==0: raise ValueError('Zero vector')
    return mul(v,1/n)


def rotate(q,v):
    u=q[:3]; t=mul(cross(u,v),2)
    return add(v,add(mul(t,q[3]),cross(u,t)))


def point(transform,p):
    return add(transform['position'],rotate(transform['rotation'],[p[i]*transform['scale'][i] for i in range(3)]))


def local(transform,p,direction=False):
    q=transform['rotation']; q=[-q[0],-q[1],-q[2],q[3]]
    value=rotate(q,p if direction else sub(p,transform['position']))
    if any(abs(x)<1e-30 for x in transform['scale']): raise ValueError('Singular transform')
    return [value[i]/transform['scale'][i] for i in range(3)]


def transform_bounds(box,transform):
    lo,hi=box
    corners=[point(transform,[x,y,z]) for x in (lo[0],hi[0]) for y in (lo[1],hi[1]) for z in (lo[2],hi[2])]
    return [[min(p[i] for p in corners) for i in range(3)],[max(p[i] for p in corners) for i in range(3)]]


def overlaps(a,b): return all(a[0][i]<=b[1][i] and a[1][i]>=b[0][i] for i in range(3))
def box_distance(p,b): return math.sqrt(sum(max(b[0][i]-p[i],0,p[i]-b[1][i])**2 for i in range(3)))


def ray_box(o,d,box,maximum):
    near,far=0.,maximum
    for i in range(3):
        if abs(d[i])<1e-30:
            if not box[0][i]<=o[i]<=box[1][i]: return None
        else:
            a=(box[0][i]-o[i])/d[i]; b=(box[1][i]-o[i])/d[i]
            near=max(near,min(a,b)); far=min(far,max(a,b))
            if near>far: return None
    return near


def ray_triangle(o,d,a,b,c,maximum):
    e1=sub(b,a); e2=sub(c,a); h=cross(d,e2); det=dot(e1,h)
    if abs(det)<1e-20: return None
    inv=1/det; s=sub(o,a); u=inv*dot(s,h)
    if u<0 or u>1: return None
    q=cross(s,e1); v=inv*dot(d,q)
    if v<0 or u+v>1: return None
    t=inv*dot(e2,q)
    return t if 0<=t<=maximum else None


class Mesh:
    def __init__(self,vertices,triangles):
        self.vertices=vertices; self.triangles=triangles
        self.boxes=[[[min(vertices[k][i] for k in t) for i in range(3)],
                     [max(vertices[k][i] for k in t) for i in range(3)]] for t in triangles]
        self.root=self.build(list(range(len(triangles)))) if triangles else None

    def build(self,ids):
        box=[[min(self.boxes[t][0][i] for t in ids) for i in range(3)],
             [max(self.boxes[t][1][i] for t in ids) for i in range(3)]]
        if len(ids)<=8: return (box,ids,None)
        axis=max(range(3),key=lambda i:box[1][i]-box[0][i])
        ids.sort(key=lambda t:self.boxes[t][0][axis]+self.boxes[t][1][axis]); middle=len(ids)//2
        return (box,self.build(ids[:middle]),self.build(ids[middle:]))

    def ray(self,origin,direction,maximum):
        hit=None; stack=[self.root] if self.root else []
        while stack:
            box,left,right=stack.pop()
            if ray_box(origin,direction,box,maximum) is None: continue
            if right is not None:
                stack.extend([left,right]); continue
            for index in left:
                a,b,c=(self.vertices[k] for k in self.triangles[index])
                distance=ray_triangle(origin,direction,a,b,c,maximum)
                if distance is not None:
                    maximum=distance; hit=(distance,index,a,b,c)
        return hit

    def candidates(self,box):
        stack=[self.root] if self.root else []
        while stack:
            bound,left,right=stack.pop()
            if not overlaps(bound,box): continue
            if right is not None: stack.extend([left,right])
            else: yield from left


def load_mesh(path):
    from . import file_signature
    path=Path(path).resolve()
    try:return _load_mesh(str(path),file_signature(path))
    except OSError as error:raise ValueError('Geometry unavailable: '+str(path)) from error


@lru_cache(maxsize=64)
def _load_mesh(path,signature):
    from . import file_signature
    data=Path(path).read_bytes()
    name=Path(path).stem
    if len(name)==40 and all(c in '0123456789abcdefABCDEF' for c in name):
        if hashlib.sha1(data).hexdigest()!=name.lower():raise ValueError('Geometry content hash mismatch')
    if len(data)<16 or data[:4]!=b'STG1': raise ValueError('Invalid Spatial Twin geometry')
    version,nv,nt=struct.unpack_from('<III',data,4)
    if version!=1 or len(data)!=16+nv*24+nt*12: raise ValueError('Invalid geometry length/version')
    vertices=list(struct.iter_unpack('<ddd',data[16:16+nv*24]))
    triangles=list(struct.iter_unpack('<III',data[16+nv*24:]))
    if any(not math.isfinite(v) for p in vertices for v in p):raise ValueError('Nonfinite geometry coordinate')
    if any(k>=nv for t in triangles for k in t): raise ValueError('Invalid triangle index')
    if file_signature(path)!=signature:raise ValueError('Geometry changed during read; retry against a stable cache')
    return Mesh(vertices,triangles)


load_mesh.cache_clear=_load_mesh.cache_clear
load_mesh.cache_info=_load_mesh.cache_info


def mesh_hit(mesh,transform,origin,direction,maximum):
    hit=mesh.ray(local(transform,origin),local(transform,direction,True),maximum)
    if hit is None: return None
    distance,index,a,b,c=hit
    normal=cross(sub(b,a),sub(c,a))
    # Surface normals use inverse transpose; reflected scale must not reverse
    # the source surface merely because world-space triangle winding flips.
    normal=rotate(transform['rotation'],[normal[i]/transform['scale'][i] for i in range(3)])
    return {'distance':distance,'position':add(origin,mul(direction,distance)),
            'normal':unit(normal),'triangle':index}


def tile_mesh(mesh,transform,world_bounds,preserve_volume=False):
    """Conservative BVH subset, retaining source triangle order and winding.

    Filled-volume rasterization needs the complete object's extent. Degenerate
    scale and small meshes use the original path without an inverse transform.
    """
    if preserve_volume or len(mesh.triangles)<=32 or any(abs(s)<1e-12 for s in transform['scale']):
        return [point(transform,p) for p in mesh.vertices],mesh.triangles
    corners=[local(transform,[x,y,z]) for x in (world_bounds[0][0],world_bounds[1][0])
             for y in (world_bounds[0][1],world_bounds[1][1]) for z in (world_bounds[0][2],world_bounds[1][2])]
    epsilon=max(1e-5,max(abs(v) for p in corners for v in p)*1e-12)
    bounds=[[min(p[i] for p in corners)-epsilon for i in range(3)],
            [max(p[i] for p in corners)+epsilon for i in range(3)]]
    faces=[mesh.triangles[i] for i in sorted(mesh.candidates(bounds))]
    indices=sorted({i for face in faces for i in face});remap={index:i for i,index in enumerate(indices)}
    return [point(transform,mesh.vertices[i]) for i in indices],[[remap[i] for i in face] for face in faces]


def convex_contains(mesh,position):
    """Test exported convex faces; flat/open triangle surfaces are not solids."""
    if not mesh.vertices or not mesh.triangles:return False
    lo,hi=mesh.root[0]
    if any(position[i]<lo[i] or position[i]>hi[i] for i in range(3)):return False
    if any(b-a<=1e-10 for a,b in zip(lo,hi)):return False
    center=[sum(p[i] for p in mesh.vertices)/len(mesh.vertices) for i in range(3)]
    for triangle in mesh.triangles:
        a,b,c=(mesh.vertices[k] for k in triangle);normal=cross(sub(b,a),sub(c,a))
        if dot(normal,sub(center,a))>0:normal=mul(normal,-1)
        if dot(normal,sub(position,a))>1e-10*max(1,length(normal)):return False
    return True


def closed_convex_planes(mesh):
    """Certify small closed convex collision surfaces, not an AABB inference.

    Exact coordinate welding handles render UV seams. Missing/degenerate faces,
    disconnected surfaces, nonmanifold edges and concavity refuse proof. Work
    is capped; large meshes remain unknown. Cache belongs to the loaded mesh,
    so file replacement/deletion invalidation also invalidates this evidence.
    """
    if hasattr(mesh,'_support_planes'):return mesh._support_planes
    mesh._support_planes=None
    if not mesh.root or len(mesh.triangles)>256 or len(mesh.vertices)>512:return None
    lo,hi=mesh.root[0];span=max(b-a for a,b in zip(lo,hi))
    epsilon=max(1e-10,min(1e-7,span*1e-12))
    if any(b-a<=epsilon for a,b in zip(lo,hi)):return None
    vertices=list(dict.fromkeys(tuple(p) for p in mesh.vertices));indices={p:i for i,p in enumerate(vertices)}
    center=[sum(p[i] for p in vertices)/len(vertices) for i in range(3)]
    edges={};planes=[]
    for face_index,triangle in enumerate(mesh.triangles):
        a,b,c=(mesh.vertices[k] for k in triangle);n=cross(sub(b,a),sub(c,a))
        if length(n)<=epsilon*epsilon:return None
        n=unit(n)
        if dot(n,sub(center,a))>0:n=mul(n,-1)
        if any(dot(n,sub(p,a))>epsilon for p in vertices):return None
        planes.append((n,dot(n,a)))
        ids=[indices[tuple(p)] for p in (a,b,c)]
        if len(set(ids))!=3:return None
        for x,y in zip(ids,ids[1:]+ids[:1]):edges.setdefault(tuple(sorted((x,y))),[]).append(face_index)
    if any(len(v)!=2 for v in edges.values()):return None
    adjacent=[set() for _ in planes]
    for a,b in edges.values():adjacent[a].add(b);adjacent[b].add(a)
    reached={0};pending=[0]
    while pending:
        for face in adjacent[pending.pop()]-reached:reached.add(face);pending.append(face)
    if len(reached)!=len(planes):return None
    mesh._support_planes=(planes,epsilon)
    return mesh._support_planes


def compose(a,b):
    from .shadow import qmul
    return {'position':point(a,b['position']),'rotation':qmul(a['rotation'],b['rotation']),
            'scale':[a['scale'][i]*b['scale'][i] for i in range(3)]}


def shape_hit(shape,transform,origin,direction,maximum):
    """Analytic ray intersection with Unreal's scaled box/sphere/capsule shapes."""
    identity={'position':[0,0,0],'rotation':[0,0,0,1],'scale':[1,1,1]}
    pose=compose(transform,shape.get('transform',identity))
    # Unreal spheres/capsules stay spherical under nonuniform scale.
    if shape['type']=='sphere':
        center=point(transform,shape.get('center',[0,0,0]))
        radius=shape['radius']*min(abs(v) for v in transform['scale'])
        o=sub(origin,center); b=dot(o,direction); c=dot(o,o)-radius*radius; disc=b*b-c
        if c<0:return {'distance':0.,'position':origin,'normal':None,'initial_overlap':True,'primitive':'sphere'}
        if disc<0: return None
        roots=[t for t in (-b-math.sqrt(disc),-b+math.sqrt(disc)) if 0<=t<=maximum]
        if not roots: return None
        t=min(roots); normal=unit(sub(add(origin,mul(direction,t)),center))
    elif shape['type']=='box':
        o=local(pose,origin); d=local(pose,direction,True); extent=shape['extent']
        t=ray_box(o,d,[mul(extent,-1),extent],maximum)
        if t is None: return None
        if all(abs(o[i])<extent[i] for i in range(3)):
            return {'distance':0.,'position':origin,'normal':None,'initial_overlap':True,'primitive':'box'}
        p=add(o,mul(d,t)); axis=min(range(3),key=lambda i:abs(abs(p[i])-extent[i]))
        n=[0.,0.,0.]; n[axis]=(1 if p[axis]>=0 else -1)/pose['scale'][axis]
        normal=unit(rotate(pose['rotation'],n))
    elif shape['type']=='capsule':
        scale=[abs(v) for v in pose['scale']]
        radius=shape['radius']*min(scale[0],scale[1])
        half=max(0.,(shape['length']*.5+shape['radius'])*scale[2]-radius)
        pose={**pose,'scale':[1,1,1]}; o=local(pose,origin); d=local(pose,direction,True)
        closest=[0,0,max(-half,min(half,o[2]))]
        if dot(sub(o,closest),sub(o,closest))<radius*radius:
            return {'distance':0.,'position':origin,'normal':None,'initial_overlap':True,'primitive':'capsule'}
        hits=[]; a=d[0]*d[0]+d[1]*d[1]; b=o[0]*d[0]+o[1]*d[1]; c=o[0]*o[0]+o[1]*o[1]-radius*radius
        disc=b*b-a*c
        if a>1e-30 and disc>=0:
            for distance in ((-b-math.sqrt(disc))/a,(-b+math.sqrt(disc))/a):
                z=o[2]+distance*d[2]
                if 0<=distance<=maximum and -half<=z<=half:
                    hits.append((distance,[o[0]+distance*d[0],o[1]+distance*d[1],0]))
        for z in (-half,half):
            offset=sub(o,[0,0,z]); b=dot(offset,d); disc=b*b-dot(offset,offset)+radius*radius
            if disc<0: continue
            for distance in (-b-math.sqrt(disc),-b+math.sqrt(disc)):
                p=add(o,mul(d,distance))
                if 0<=distance<=maximum and (p[2]<=-half if z<0 else p[2]>=half): hits.append((distance,sub(p,[0,0,z])))
        if not hits: return None
        t,n=min(hits,key=lambda hit:hit[0]);normal=unit(rotate(pose['rotation'],n))
    else: raise ValueError('Unknown collision shape '+shape['type'])
    return {'distance':t,'position':add(origin,mul(direction,t)),'normal':normal,'primitive':shape['type']}
