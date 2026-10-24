import struct, sys, collections
D = sys.argv[1]; L = sys.argv[2]
nodes = open(f"{D}/{L}.bsp.0012.bsp_lump","rb").read()
leaf = open(f"{D}/{L}.bsp.0013.bsp_lump","rb").read()
nn = len(nodes)//64
top = collections.Counter(); sub = collections.Counter(); bad = []
for i in range(nn):
    w = struct.unpack_from("<4I", nodes, i*64+48)
    types = [w[2]&0xF, (w[2]>>4)&0xF, w[3]&0xF, (w[3]>>4)&0xF]
    for c in range(4):
        t = types[c]; idx = w[c]>>8
        top[t]+=1
        if t == 3:
            o = idx*4
            if o+4>len(leaf): bad.append((i,c,"oob",idx)); continue
            n = struct.unpack_from("<I", leaf, o)[0]
            if n>4096 or o+4+4*n>len(leaf): bad.append((i,c,"cnt",n)); continue
            p = o + 4 + 4*n
            for k in range(n):
                ci, st, sz = struct.unpack_from("<BBH", leaf, o+4+4*k)
                sub[st]+=1
                if st in (0,1) or st>=10:
                    bad.append((i,c,k,st))
                p += 4*sz
print("nodes", nn, "top child types", sorted(top.items()))
print("bundle subtypes", sorted(sub.items()))
print("bad", len(bad), bad[:10])
