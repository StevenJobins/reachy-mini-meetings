"""Reachy Mini 3D model for the midterm slides (xr-client/pages/midterm/reachy.json + .bin).

Takes Pollen's MuJoCo model (reachy_mini package, Apache-2.0) in its rest pose, keeps the visible parts (no motors,
screws), decimates them and writes 5 groups that the slides animate: base, upper (body yaw), head, two antennas.
Positions are 16-bit in each group's box, colours per vertex. Used for the deck as:

    python export_reachy_model.py ../../xr-client/pages/midterm/reachy 220000 4000 2

Needs mujoco (3.2.x has Intel-Mac wheels), trimesh, fast-simplification and the reachy_mini package.
"""
import mujoco, numpy as np, json, sys, trimesh
import importlib.util, pathlib
M=str(pathlib.Path(importlib.util.find_spec("reachy_mini").origin).parent/"descriptions/reachy_mini/mjcf/scene.xml")
OUT=sys.argv[1]; TARGET=int(sys.argv[2]) if len(sys.argv)>2 else 70000
FLOOR=int(sys.argv[3]) if len(sys.argv)>3 else 1500; AGG=int(sys.argv[4]) if len(sys.argv)>4 else 2
m=mujoco.MjModel.from_xml_path(M); d=mujoco.MjData(m); mujoco.mj_forward(m,d)
def subtree(root):
    ids={root}
    for b in range(m.nbody):
        p=b
        while p!=0:
            if p==root: ids.add(b); break
            p=m.body_parentid[p]
    return ids
head=subtree(m.body("xl_330").id)
upper=subtree(m.body("body_down_3dprint").id)
ant={}
for jn in ("right_antenna","left_antenna"):
    j=m.joint(jn).id; ant[jn]=(subtree(m.jnt_bodyid[j]), d.xanchor[j].copy(), d.xaxis[j].copy())
def group_of(b):
    for jn,(ids,_,_) in ant.items():
        if b in ids: return jn
    if b in head: return "head"
    if b in upper: return "upper"
    return "base"
parts={}
tot=0
for g in range(m.ngeom):
    if m.geom_group[g]!=2 or m.geom_type[g]!=mujoco.mjtGeom.mjGEOM_MESH: continue
    mid=m.geom_dataid[g]
    import re
    if re.search(r'dc15|bts2|phs_|b3b|bearing|horn|pp01|mp01|m2_|m3_|screw|nut|washer', m.mesh(mid).name): continue
    va,vn=m.mesh_vertadr[mid],m.mesh_vertnum[mid]; fa,fn=m.mesh_faceadr[mid],m.mesh_facenum[mid]
    V=m.mesh_vert[va:va+vn].astype(np.float64); F=m.mesh_face[fa:fa+fn].astype(np.int64)
    R=d.geom_xmat[g].reshape(3,3); t=d.geom_xpos[g]
    Vw=V@R.T+t
    mat=m.geom_matid[g]; rgba=(m.mat_rgba[mat] if mat>=0 else m.geom_rgba[g]).copy()
    grp=group_of(m.geom_bodyid[g])
    if rgba[3]<1: rgba=np.array([0.05,0.09,0.16,1.0])   # lenses: dark glass instead of see-through
    parts.setdefault(grp,[]).append((trimesh.Trimesh(Vw,F,process=True),rgba,m.mesh(mid).name))
    tot+=fn
print("total faces",tot)
ratio=min(1.0,TARGET/tot)
pivots={"base":[0,0,0],"upper":d.xanchor[m.joint("yaw_body").id].tolist(),
        "right_antenna":ant["right_antenna"][1].tolist(),"left_antenna":ant["left_antenna"][1].tolist()}
axes={"upper":d.xaxis[m.joint("yaw_body").id].tolist(),"right_antenna":ant["right_antenna"][2].tolist(),"left_antenna":ant["left_antenna"][2].tolist()}
hb=np.vstack([p[0].vertices for p in parts["head"]]); pivots["head"]=((hb.min(0)+hb.max(0))/2).tolist()
blobs=bytearray(); meta={"groups":[],"cameraPos":None}
for grp,lst in parts.items():
    P=[];C=[];I=[];off=0
    for tm,rgba,name in lst:
        nf=max(min(len(tm.faces),FLOOR),int(len(tm.faces)*ratio))
        n0=len(tm.faces)
        if n0>nf:
            try: tm=tm.simplify_quadric_decimation(face_count=nf, aggression=AGG)
            except Exception as e: print("decimate fail",name,e)
        v=np.asarray(tm.vertices)-np.array(pivots[grp]); f=np.asarray(tm.faces)
        P.append(v); C.append(np.tile((np.clip(rgba[:3],0,1)*255).astype(np.uint8),(len(v),1))); I.append(f+off); off+=len(v)
    P=np.vstack(P); lo=P.min(0); sc=(P.max(0)-lo)/65535.0; sc[sc==0]=1e-9
    Q=np.round((P-lo)/sc).astype(np.uint16); C=np.vstack(C); I=np.vstack(I).astype(np.uint32 if off>65535 else np.uint16)
    def add(arr):
        global blobs
        while len(blobs)%4: blobs.append(0)
        o=len(blobs); blobs+=arr.tobytes(); return [o,arr.size]
    meta["groups"].append({"name":grp,"pivot":pivots[grp],"axis":axes.get(grp),"pos":add(Q.ravel()),"q":[lo.tolist(),sc.tolist()],"col":add(C.ravel()),
        "idx":add(I.ravel()),"idxType":"u32" if I.dtype==np.uint32 else "u16"})
    print(grp,len(P),"verts",len(I),"tris")
lens=[g for g in range(m.ngeom) if m.geom_group[g]==2 and m.geom_type[g]==mujoco.mjtGeom.mjGEOM_MESH and "big_lens" in m.mesh(m.geom_dataid[g]).name]
meta["front"]=(d.geom_xpos[lens[0]]-np.array(pivots["head"])).tolist() if lens else None
allv=np.vstack([p[0].vertices for l in parts.values() for p in l]); meta["bbox"]=[allv.min(0).tolist(),allv.max(0).tolist()]
open(OUT+".bin","wb").write(blobs); json.dump(meta,open(OUT+".json","w"))
print("bin bytes",len(blobs)); print(json.dumps(meta["bbox"]))
