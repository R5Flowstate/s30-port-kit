"""Gate: every lit-bump vertex references valid position/normal/tangent entries (tangent at +28, colour at +16)."""
import sys, os, glob, numpy as np
b = sys.argv[1]
f = lambda i: next(p for p in glob.glob(b + ".*") if p.lower().endswith(".%04x.bsp_lump" % i) or p.lower().endswith(".%04x.bsp_lump.client" % i))
v = np.fromfile(f(0x49), np.dtype([("p","<u4"),("n","<u4"),("uv","<f4",2),("c","u1",4),("lm","<f4",2),("t","<u4")]))
npos, nn = os.path.getsize(f(0x03)) // 12, os.path.getsize(f(0x1E)) // 12
bad = {"pos": int((v["p"] >= npos).sum()), "nrm": int((v["n"] >= nn).sum()), "tan": int(((v["t"] & 0x7FFFFFFF) >= nn).sum()),
       "lm_nan": int((~np.isfinite(v["lm"])).sum()), "uv_nan": int((~np.isfinite(v["uv"])).sum())}
print(os.path.basename(b), len(v), bad, "PASS" if not any(bad.values()) else "FAIL")
sys.exit(1 if any(bad.values()) else 0)
