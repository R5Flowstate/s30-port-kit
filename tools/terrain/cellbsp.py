import numpy as np
class CellBsp:
    def __init__(s, D, B, ext=".bsp_lump", sign=1.0):
        L=lambda i:np.fromfile(f"{D}/{B}.{i:04x}{ext}",np.uint8)
        s.pl=L(0x01).view("<f4").reshape(-1,4).astype(np.float64)
        s.bn=L(0x6A).view("<u4").reshape(-1,2).astype(np.int64)
        s.sign=sign
        nd=L(0x77).view("<u4").reshape(-1,8); f=nd.view("<f4")
        s.ncell=len(L(0x6B))//8
        s.rmn=f[:s.ncell,0:3]; s.rmx=f[:s.ncell,4:7]
    def cells(s, P):
        P=np.asarray(P,np.float64); node=np.zeros(len(P),np.int64)
        for _ in range(200):
            pn=s.bn[node,0]; act=pn!=0xFFFFFFFF
            if not act.any(): break
            pl=s.pl[np.where(act,pn,0)]
            d=(P*pl[:,:3]).sum(1)+s.sign*pl[:,3]
            node=np.where(act, s.bn[node,1]+(d<0), node)
        return s.bn[node,1]
if __name__=="__main__":
    import sys
    D,B=sys.argv[1],sys.argv[2]; ext=sys.argv[3] if len(sys.argv)>3 else ".bsp_lump"
    for sg in (1.0,-1.0):
        c=CellBsp(D,B,ext,sg); rng=np.random.default_rng(0)
        i=rng.integers(0,c.ncell,20000); P=c.rmn[i]+(c.rmx[i]-c.rmn[i])*rng.random((20000,3))
        k=c.cells(P); inbox=np.all((c.rmn[k]-1<=P)&(P<=c.rmx[k]+1),1)
        print("sign",sg,"point in own cell box",inbox.mean(),"same cell as sampled box",(k==i).mean())
