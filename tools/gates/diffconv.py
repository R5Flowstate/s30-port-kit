import os, sys, hashlib, struct
a, b = sys.argv[1], sys.argv[2]
def h(p): return hashlib.md5(open(p, "rb").read()).hexdigest() if os.path.exists(p) else None
fa = {f for f in os.listdir(a) if ".bsp." in f}; fb = {f for f in os.listdir(b) if ".bsp." in f}
for f in sorted(fa | fb):
    ha, hb = h(os.path.join(a, f)), h(os.path.join(b, f))
    if ha != hb: print("DIFF", f, "fresh" if ha else "-", "cur" if hb else "-", os.path.getsize(os.path.join(a, f)) if ha else 0, os.path.getsize(os.path.join(b, f)) if hb else 0)
H = lambda d: open(os.path.join(d, "mp_rr_desertlands_mu5.bsp"), "rb").read()
x, y = H(a), H(b)
print("hdr len", len(x), len(y), "prefix16 equal", x[:16] == y[:16])
for i in range(128):
    ea, eb = struct.unpack_from("<4i", x, 16 + 16 * i), struct.unpack_from("<4i", y, 16 + 16 * i)
    if ea != eb: print("HDR %02x fresh %s cur %s" % (i, ea, eb))
if len(x) > 16 + 16 * 128 and x[16 + 2048:] != y[16 + 2048:]: print("header tail differs")
