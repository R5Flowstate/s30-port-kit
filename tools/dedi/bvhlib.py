import struct
class Bvh:
    def __init__(s, D, L):
        P = "%s/%s.bsp." % (D, L)
        s.nodes = open(P+"0012.bsp_lump","rb").read()
        s.leaf = open(P+"0013.bsp_lump","rb").read()
        s.verts = open(P+"0003.bsp_lump","rb").read()
        s.models = open(P+"000e.bsp_lump","rb").read()
        m = struct.unpack_from("<6f6I3ff", s.models, 0)
        s.origin = m[12:15]; s.scale = m[15]
    def node(s, i):
        b = s.nodes[i*64:i*64+64]
        h = struct.unpack_from("<24h", b, 0)
        w = struct.unpack_from("<4I", b, 48)
        t = [w[2]&0xF,(w[2]>>4)&0xF,w[3]&0xF,(w[3]>>4)&0xF]
        idx = [x>>8 for x in w]
        bounds = []
        for c in range(4):
            mn = [h[0+c]*2*s.scale*16384, h[8+c], h[16+c]]
            bounds.append(((h[c],h[8+c],h[16+c]),(h[4+c],h[12+c],h[20+c])))
        return t, idx, bounds, w
    def cbounds(s, b):
        sc = s.scale*65536
        mn = [b[0][k]*sc + s.origin[k] for k in range(3)]
        mx = [b[1][k]*sc + s.origin[k] for k in range(3)]
        return mn, mx
    def vert(s, i):
        return struct.unpack_from("<3f", s.verts, 12*i)
    def tris4(s, o, vb=0):
        h0, h1 = struct.unpack_from("<HH", s.leaf, o)
        n = (h0>>12)+1; base = (h1<<10) + vb; out = []
        for k in range(n):
            x = struct.unpack_from("<I", s.leaf, o+4+4*k)[0]
            base += x & 0x7FF
            out.append((s.vert(base), s.vert(base+1+((x>>11)&0x1FF)), s.vert(base+1+((x>>20)&0x1FF))))
        return h0 & 0xFFF, out, 4+4*n

HF = None
def load_hf(D, L):
    P = "%s/%s.bsp." % (D, L)
    h = open(P+"0015.bsp_lump","rb").read()
    hfs = []
    for i in range(len(h)//40):
        ox,oy,oz,sx,sy,sz,d0,d1,cnt = struct.unpack_from("<6f2HI", h, i*40)
        hfs.append(dict(origin=(ox,oy,oz), scale=(sx,sy,sz), dims=(d0,d1)))
    return hfs, open(P+"0016.bsp_lump","rb").read()

def patch(leaf, o):
    c0,c1,d0,d1,b6,qpp = struct.unpack_from("<HHBBBB", leaf, o)
    mve, hoff, bias, hs = struct.unpack_from("<fIff", leaf, o+8)
    rle = struct.unpack_from("<Q", leaf, o+24)[0]
    sp = struct.unpack_from("<4H", leaf, o+32)
    return dict(coord=(c0,c1), dims=(d0,d1), ttb=b6&1, hidx=b6>>1, qpp=qpp, mve=mve, hoff=hoff, bias=bias, hs=hs, rle=rle, sp=sp)

def patch_tris(p, hf, heights):
    """Engine-order triangles: list of (v0,v1,v2,surfprop) in world space; hole tris dropped."""
    q = p["qpp"]
    spr = (p["dims"][0]-1)//q + 1
    spc = (p["dims"][1]-1)//q + 1
    dx = q*hf["scale"][0]; dy = q*hf["scale"][1]
    ox = p["coord"][0]*hf["scale"][0] + hf["origin"][0]
    oy = p["coord"][1]*hf["scale"][1] + hf["origin"][1]
    base = p["hoff"]
    def S(r, c):
        v = heights[base + r*spr + c]
        if v == 0: return None
        return (ox + c*dx, oy + r*dy, (v-1)*p["hs"] + p["bias"])
    out = []; spi = 0; poly = 0
    for r in range(spc-1):
        for c in range(spr-1):
            a, b, cc, d = S(r,c), S(r,c+1), S(r+1,c), S(r+1,c+1)
            sp = p["sp"][spi] if spi < 4 else p["sp"][3]
            if p["ttb"] == 0:
                t = [(a, d, cc), (a, b, d)]
            else:
                t = [(a, b, cc), (b, d, cc)]
            for tri in t:
                if None not in tri:
                    out.append((tri[0], tri[1], tri[2], sp))
            poly += 2
            if (p["rle"] >> (poly >> 1)) & 1:
                spi += 1
    return out, spr, spc
