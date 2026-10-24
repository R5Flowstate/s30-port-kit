import kitpaths as KP
import struct,sys,re
def scan(path, guids):
    d=open(path,'rb').read()
    for name,g in guids:
        h=struct.pack('<I',g & 0xffffffff)
        for m in re.finditer(re.escape(h),d):
            o=m.start()-0x10
            if o<0: continue
            cmp,dcmp=struct.unpack_from('<II',d,o+0x14)
            ps,sf,fn,fl,u4=struct.unpack_from('<HHHHH',d,o+0x1C)
            u5=d[o+0x26:o+0x28]
            if 0<ps<300 and u5==b'\xff\x00':
                print('%-6s %s off %x cmp %d dcmp %d pathSize %d flags %#x unk4 %d unk5 %s path/data ptr %s'%(name,path.split('/')[-1][:40],o,cmp,dcmp,ps,fl,u4,u5.hex(),d[o:o+16].hex()))
hu=[('0061',0x5ac934d65d7622a4),('0062',0x8748ecfca4e46c8e),('0069',0x3d62b979ff56546a),('0053',0x566b3e6b0fe3d89f),('007A',0xbe3c0323a9f19e48)]
mu=[('0061',0x371721228233cc7c),('0062',0xb4e87589ea4f5613),('0069',0xdeb8be41d9fa9b3d),('0053',0xd99999a29d8c64a2),('007A',0x8e05c239f38f2336)]
D=(KP.WORK + '/mu5_lm2/hudec/')
scan(D+'mp_rr_desertlands_hu_client_perm.rpak.dec.rpak',hu); scan(D+'mp_rr_desertlands_hu_client_temp.rpak.dec.rpak',hu)
V=(KP.MAPS + '/mp_rr_desertlands_mu5/wrap_build/paks/Win64/')
scan(V+'mp_rr_desertlands_mu5_client_perm.rpak',mu); scan(V+'mp_rr_desertlands_mu5_client_temp.rpak',mu)
