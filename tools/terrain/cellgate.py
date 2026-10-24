"""For every world mesh: cells its vertices lie in (cell BSP) vs cells whose tree references it."""
import kitpaths as KP
import numpy as np,sys,struct,collections

from cellbsp import CellBsp
D,B,ext=sys.argv[1],sys.argv[2],sys.argv[3]; lo,hi=int(sys.argv[4]),int(sys.argv[5])
L=lambda i:np.fromfile(f"{D}/{B}.{i:04x}{ext}",np.uint8)
cb=CellBsp(D,B,ext,-1.0)
nd=L(0x77).view("<u4").reshape(-1,8); cc=nd[:,3]&255; fc=nd[:,3]>>8; rc=nd[:,7]&255; ro=nd[:,7]>>8
refs=L(0x78).view("<u4")
par=np.full(len(nd),-1)
for i in np.flatnonzero(cc): par[fc[i]:fc[i]+cc[i]]=i
root=np.arange(len(nd))
for _ in range(64):
    m=par[root]>=0
    if not m.any():break
    root[m]=par[root[m]]
refcells=collections.defaultdict(set)
for i in np.flatnonzero(rc):
    for r in refs[ro[i]:ro[i]+rc[i]]: refcells[int(r)].add(int(root[i]))
me=L(0x50).tobytes(); ms=L(0x52).tobytes(); idx=L(0x4F).view("<u2"); pos=L(0x03).view("<f4").reshape(-1,3)
vb=L(0x49).view("<u4").reshape(-1,8)
nm=len(me)//28
miss_v=tot_v=0; meshes_bad=0; multi=0; nmesh=0; nref=0
for k in range(lo,min(hi,nm)):
    fi,tri,fv,lv,vt=struct.unpack_from("<IHHHB",me,k*28); fl=struct.unpack_from("<I",me,k*28+24)[0]; ms_=struct.unpack_from("<H",me,k*28+22)[0]
    if ((fl>>9)&3)!=1: continue
    vo=struct.unpack_from("<4hI",ms,ms_*12)[4]
    ii=idx[fi:fi+tri*3].astype(np.int64)+vo
    P=pos[vb[np.unique(ii),0]]
    c=cb.cells(P); have=refcells.get(k,set())
    if not have: continue
    nmesh+=1; nref+=len(have)
    bad=~np.isin(c,list(have)); miss_v+=bad.sum(); tot_v+=len(c)
    if bad.any(): meshes_bad+=1
    if len(set(c.tolist()))>1: multi+=1
print("lit-bump meshes",nmesh,"refs/mesh %.3f"%(nref/max(nmesh,1)),"meshes spanning >1 cell",multi,
      "meshes with verts in an unreferenced cell",meshes_bad,"verts %d/%d (%.4f)"%(miss_v,tot_v,miss_v/max(tot_v,1)))
