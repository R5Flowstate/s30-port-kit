import kitpaths as KP
import struct
M=(KP.MAPS + "/mp_rr_desertlands_mu5/bsp/client_v51/mp_rr_desertlands_mu5.bsp.%04x.bsp_lump")
H=(KP.WORK + "/mu5_terrain/hu/wrap/maps/mp_rr_desertlands_hu.bsp.%04X.bsp_lump.client")
import os
for n,p in (('mu5',M),('hu',H)):
    d=open(p%0x53,'rb').read()
    print(n,'0x53 len',len(d))
    for i in range(len(d)//8):
        print('  ',struct.unpack_from('<BBHHH',d,i*8))
    for l in (0x62,0x69,0x7a,0x61):
        q=p%l
        print('   lump %x'%l, os.path.getsize(q) if os.path.exists(q) else None)
