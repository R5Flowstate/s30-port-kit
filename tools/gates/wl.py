import kitpaths as KP
import struct,sys,collections
M=(KP.MAPS + "/mp_rr_desertlands_mu5/bsp/client_v51/mp_rr_desertlands_mu5.bsp.%04x.bsp_lump")
H=(KP.WORK + "/mu5_terrain/hu/wrap/maps/mp_rr_desertlands_hu.bsp.%04X.bsp_lump.client")
def load(p):
    try: return open(p,'rb').read()
    except Exception as e: return None
for lump,rs in ((0x36,112),(0x37,76)):
    a=load(M%lump); b=load(H%lump)
    print('lump %x mu5 %s hu %s'%(lump,len(a) if a else None,len(b) if b else None), 'recs', len(a)//rs if a else 0, len(b)//rs if b else 0)
    for name,d in (('mu5',a),('hu',b)):
        if not d: continue
        n=len(d)//rs
        print(' ',name)
        for o in range(0,rs,4):
            u=[struct.unpack_from('<I',d,i*rs+o)[0] for i in range(n)]
            f=[struct.unpack_from('<f',d,i*rs+o)[0] for i in range(n)]
            c=collections.Counter(u)
            isint=all(x<100000 for x in u)
            if isint: print('   +%3d int  min %d max %d top %s'%(o,min(u),max(u),c.most_common(5)))
            else:
                ff=[x for x in f if x==x]
                print('   +%3d flt  min %.4g max %.4g distinct %d top %s'%(o,min(ff),max(ff),len(c),[ (round(struct.unpack('<f',struct.pack('<I',k))[0],3),v) for k,v in c.most_common(3)]))
