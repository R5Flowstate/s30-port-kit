"""Per world mesh: sample its lightmap page (0x62 plane 0, RGBM) at its vertices' lightmap uv. Lists materials that come out black."""
import struct, sys, collections
import numpy as np
D, B, ext = sys.argv[1], sys.argv[2], sys.argv[3]
L = lambda i: np.fromfile("%s.%04x%s" % (D + "/" + B, i, ext), np.uint8)
hdr = L(0x53).tobytes(); pages = [struct.unpack_from("<BBBBHH", hdr, i * 8) for i in range(len(hdr) // 8)]
lm = L(0x62); off = 0; planes = []
for t, ct, tag, _, w, h in pages:
    p0 = lm[off:off + w * h * 4].reshape(h, w, 4); off += w * h * 4
    p1 = lm[off:off + w * h * 4].reshape(h, w, 4); off += w * h * 4
    planes.append((p0, p1, w, h))
td, strs = L(2).tobytes(), L(0xF).tobytes()
names = []
for k in range(len(td) // 16):
    ni = struct.unpack_from("<i", td, k * 16)[0]; names.append(strs[ni:strs.index(b"\0", ni)].decode(errors="replace"))
me, ms = L(0x50).tobytes(), L(0x52).tobytes(); idx = L(0x4F).view("<u2")
vb = L(0x49).view(np.dtype([("p", "<u4"), ("n", "<u4"), ("uv", "<f4", 2), ("c", "u1", 4), ("lm", "<f4", 2), ("t", "<u4")]))
stat = collections.defaultdict(lambda: [0, 0, 0.0])
for k in range(len(me) // 28):
    fi, tri, fv, lv = struct.unpack_from("<IHHH", me, k * 28); fl = struct.unpack_from("<I", me, k * 28 + 24)[0]
    if ((fl >> 9) & 3) != 1: continue
    si = struct.unpack_from("<H", me, k * 28 + 22)[0]
    t, page, _, _, vo = struct.unpack_from("<4hI", ms, si * 12)
    if page < 0 or page >= len(planes): continue
    p0, p1, w, h = planes[page]
    v = vb[vo + fv:vo + fv + lv + 1]
    x = np.clip((v["lm"][:, 0] * w).astype(int), 0, w - 1); y = np.clip((v["lm"][:, 1] * h).astype(int), 0, h - 1)
    s = p0[y, x].astype(float)
    lum = (s[:, :3].max(1) / 255.0) * (s[:, 3] / 255.0 * 3.984375 + 0.015625)
    e = stat[names[t]]; e[0] += 1; e[1] += int(np.median(lum) < 0.02); e[2] += float(np.median(lum))
tot = sum(e[0] for e in stat.values()); blk = sum(e[1] for e in stat.values())
print("lit-bump meshes", tot, "black (median rgbm < 0.02)", blk)
for n, e in sorted(stat.items(), key=lambda kv: -kv[1][1])[:25]:
    print("%5d/%5d black  mean %.3f  %s" % (e[1], e[0], e[2] / e[0], n))
