import kitpaths as KP
import sys,glob,os,pickle

import pak_msw as P
want=set(int(os.path.basename(f)[:-4],16) for f in glob.glob((KP.MAPS + r'\mp_rr_desertlands_mu5\extract\shader\*.msw')))
out={}
for pak in glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak')):
    d,pages,ao0,ac,rel=P.load(pak)
    for i in range(ac):
        ao=ao0+i*80
        if P.u32(d,ao+0x4C)==P.FCC_SHDR:
            g=P.u64(d,ao)
            if g in want and g not in out:
                s=P.read_shader(pak,g)
                if s and s['type']<=5: out[g]=s
    P._CACHE.clear()
    print(os.path.basename(pak),len(out),flush=True)
pickle.dump(out,open('twins.pkl','wb'))
