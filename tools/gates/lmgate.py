"""Type-9 lightmap gate: plane 0 read as raw HDR and as RGBM must equal the S30 RGB9E5 texel; plane 1 byte-identical."""
import numpy as np, struct, sys
src, dst = sys.argv[1], sys.argv[2]   # dirs with mp_rr_desertlands_mu5.bsp.XXXX.bsp_lump
P = lambda d, l: "%s/mp_rr_desertlands_mu5.bsp.%04x.bsp_lump" % (d, l)
hs, hd = open(P(src, 0x53), "rb").read(), open(P(dst, 0x53), "rb").read()
a, b = np.fromfile(P(src, 0x62), np.uint8), np.fromfile(P(dst, 0x62), np.uint8)
oa = ob = 0; worst = 0.0; worst_rgbm = 0.0; bad = 0
for i in range(len(hs) // 8):
    ts, td = hs[i * 8], hd[i * 8]; w, h = struct.unpack_from("<HH", hs, i * 8 + 4); n = w * h
    assert hs[i*8+1:] [:7] == hd[i*8+1:][:7], "header bytes past type changed"
    assert ts == 11 and td == 9, (ts, td)
    v = a[oa:oa + 4 * n].view(np.uint32).astype(np.int64)
    e = (v >> 27) - 24
    ref = np.stack([(v & 511), (v >> 9) & 511, (v >> 18) & 511], 1).astype(np.float64) * np.exp2(e)[:, None]
    q = b[ob:ob + 8 * n].view(np.float16).reshape(-1, 4).astype(np.float64)
    raw = q[:, :3]; rgbm = raw * (q[:, 3:4] * 3.984375 + 0.015625)
    d = np.abs(raw - ref); bad += int((d > 0).sum())
    rel = np.abs(rgbm - ref) / np.maximum(ref, 1e-30)
    worst_rgbm = max(worst_rgbm, float(rel[ref > 0].max()) if (ref > 0).any() else 0)
    p1s, p1d = a[oa + 4 * n:oa + 8 * n], b[ob + 8 * n:ob + 12 * n]
    assert (p1s == p1d).all(), "plane 1 changed on page %d" % i
    print("page %d %dx%d raw-exact mismatches %d  max %.4g  rgbm max rel err %.2e  alpha %s" % (i, w, h, int((d > 0).sum()), ref.max(), float(rel[ref > 0].max()), np.unique(q[:, 3])))
    oa += 8 * n; ob += 12 * n
assert oa == len(a) and ob == len(b)
print("GATE", "PASS" if bad == 0 and worst_rgbm < 1e-4 else "FAIL", "raw mismatches", bad, "rgbm worst rel", worst_rgbm)
