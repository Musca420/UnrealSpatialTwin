"""Convex supports between supplied bearing triangles and a surface envelope.

Pure derived geometry in caller coordinates (cm). Callers select source faces;
this module does not infer building semantics or modify canonical/editor state.
Unknown surface coverage and excessive subdivision refuse, never fill by guess.
"""
import math


def area(p):
    return sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(p,p[1:]+p[:1]))*.5


def clean(p):
    points=[]
    for v in p:
        if not points or math.dist(v,points[-1])>1e-7:points.append(v)
    if len(points)>1 and math.dist(points[0],points[-1])<=1e-7:points.pop()
    while len(points)>2:
        for i,b in enumerate(points):
            a,c=points[i-1],points[(i+1)%len(points)]
            if abs((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))<1e-7:
                points.pop(i);break
        else:break
    return points if len(points)>2 and abs(area(points))>1e-6 else []


def clip(poly,value):
    """Convex polygon intersected with the affine half-plane value >= 0."""
    out=[]
    for a,b in zip(poly,poly[1:]+poly[:1]):
        fa,fb=value(a),value(b)
        if fa>=0:out.append(a)
        if (fa<0)!=(fb<0):
            t=fa/(fa-fb);out.append([a[i]+t*(b[i]-a[i]) for i in (0,1)])
    return clean(out)


def partition(poly,triangle):
    outside=[];inside=poly
    for a,b in zip(triangle,triangle[1:]+triangle[:1]):
        def side(p):return (b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0])
        part=clip(inside,lambda p:-side(p))
        if part:outside.append(part)
        inside=clip(inside,side)
        if not inside:return [],[poly]
    return inside,outside


def surface(tri):
    a,b,c=tri;den=(b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    if abs(den)<1e-8:return None
    x=((b[2]-a[2])*(c[1]-a[1])-(c[2]-a[2])*(b[1]-a[1]))/den
    y=((b[0]-a[0])*(c[2]-a[2])-(c[0]-a[0])*(b[2]-a[2]))/den
    poly=[[p[0],p[1]] for p in tri]
    if den<0:poly.reverse()
    return poly,(x,y,a[2]-x*a[0]-y*a[1])


def height(plane,p):return plane[0]*p[0]+plane[1]*p[1]+plane[2]


def support_prisms(bearing_triangles,support_triangles,clearance=.02,max_bodies=256,max_cells=4096,min_depth=.1,exclusion_footprints=()):
    """Exact piecewise-planar upper envelope; horizontal bearing faces only.

    Returns closed convex meshes with a gap of `clearance` at both contacts.
    Depths below `min_depth` are omitted to avoid native convex weld slivers.
    Use a nearby origin for numerical precision. Geometry below an already
    intersecting bearing face is omitted; no existing intersections are repaired.
    Collision/Shadow validation of the resulting authoring asset is mandatory.
    Explicit convex XY exclusion footprints leave entire columns empty (e.g.
    entrances or existing fixtures). They are authoring constraints, not an
    approximation of the source collision volume.
    """
    if not math.isfinite(clearance) or clearance<=0:raise ValueError('Positive finite contact clearance required')
    if not math.isfinite(min_depth) or min_depth<=0:raise ValueError('Positive finite minimum depth required')
    if not bearing_triangles or not support_triangles:raise ValueError('Bearing and support triangles required')
    if sum(map(len,(bearing_triangles,support_triangles)))>100000:raise ValueError('Triangle budget exceeded')
    for tri in (*bearing_triangles,*support_triangles):
        if len(tri)!=3 or any(len(p)!=3 or any(not math.isfinite(v) for v in p) for p in tri):raise ValueError('Finite triangles required')
    surfaces=[s for t in support_triangles if (s:=surface(t)) is not None];result=[]
    exclusions=[]
    for footprint in exclusion_footprints:
        if len(footprint)<3 or any(len(p)!=2 or any(not math.isfinite(v) for v in p) for p in footprint):raise ValueError('Finite convex exclusion footprint required')
        polygon=clean(footprint)
        if not polygon:raise ValueError('Degenerate exclusion footprint')
        if area(polygon)<0:polygon.reverse()
        for a,b in zip(polygon,polygon[1:]+polygon[:1]):
            if any((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]) < -1e-7 for p in polygon):raise ValueError('Nonconvex exclusion footprint')
        exclusions.append(polygon)
    for bearing in bearing_triangles:
        top=min(p[2] for p in bearing)-clearance
        if max(p[2] for p in bearing)-min(p[2] for p in bearing)>1e-5:raise ValueError('Bearing faces must be horizontal')
        base=surface(bearing)
        if base is None:raise ValueError('Degenerate bearing triangle')
        footprints=[base[0]]
        for exclusion in exclusions:
            footprints=[part for poly in footprints for part in partition(poly,exclusion)[1]]
            if len(footprints)>max_cells:raise ValueError('Exclusion subdivision budget exceeded')
        cells=[(poly,None) for poly in footprints]
        for footprint,plane in surfaces:
            new=[]
            for poly,prior in cells:
                inside,outside=partition(poly,footprint);new.extend((p,prior) for p in outside)
                if inside:
                    if prior is None:new.append((inside,plane))
                    else:
                        values=[height(plane,p)-height(prior,p) for p in inside]
                        if max(values)<=1e-8:new.append((inside,prior))
                        elif min(values)>=-1e-8:new.append((inside,plane))
                        else:
                            high=clip(inside,lambda p:height(plane,p)-height(prior,p))
                            low=clip(inside,lambda p:height(prior,p)-height(plane,p))
                            if high:new.append((high,plane))
                            if low:new.append((low,prior))
            cells=new
            if len(cells)>max_cells:raise ValueError('Surface subdivision budget exceeded')
        for poly,plane in cells:
            if plane is None:raise ValueError('Support surface coverage UNKNOWN')
            poly=clip(poly,lambda p:top-height(plane,p)-clearance-min_depth)
            if not poly:continue
            n=len(poly);vertices=[[p[0],p[1],height(plane,p)+clearance] for p in poly]+[[p[0],p[1],top] for p in poly]
            triangles=[]
            for i in range(1,n-1):triangles.extend([(0,i+1,i),(n,n+i,n+i+1)])
            for i in range(n):
                j=(i+1)%n;triangles.extend([(i,j,n+j),(i,n+j,n+i)])
            result.append({'vertices':vertices,'triangles':triangles})
            if len(result)>max_bodies:raise ValueError('Convex body budget exceeded')
    return result
