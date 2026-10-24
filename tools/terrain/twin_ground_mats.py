"""terrain_mats.txt for matmap.py: how much ground each S21-twin material covers ("<count> <material>" per line).

Count = up-facing (clockwise from above, retail ground winding) lit-bump triangles of the twin BSP. matmap only uses it to
order candidates (ties go to the material the twin's ground uses most); exact values are not load-bearing.
mu5 shipped an earlier inline count (data/mu5/terrain_mats.txt); this reproduces its ordering, not its numbers.

    py twin_ground_mats.py "<twin wrap>/maps/<twin>.bsp.%04X.bsp_lump.client" <out terrain_mats.txt>"""
import collections, struct, sys
import numpy as np

def main(pat, out):
    L = lambda i: open(pat % i, "rb").read()
    td, strs, ms, me = L(2), L(0xF), L(0x52), L(0x50)
    names = []
    for k in range(len(td) // 16):
        o = struct.unpack_from("<i", td, k * 16)[0]
        names.append(strs[o:strs.index(b"\0", o)].decode().lower().replace("\\", "/"))
    pos = np.frombuffer(L(3), np.float32).reshape(-1, 3)
    vlb = np.frombuffer(L(0x49), np.uint32).reshape(-1, 8)
    idx = np.frombuffer(L(0x4F), np.uint16)
    cnt = collections.Counter()
    for k in range(len(me) // 28):
        f = struct.unpack_from("<IH8hHI", me, k * 28)
        if (f[11] >> 9) & 3 != 1:          # lit-bump vertex type only
            continue
        t, lm, _, _, vo = struct.unpack_from("<4hI", ms, f[10] * 12)
        ii = idx[f[0]:f[0] + f[1] * 3].astype(np.int64) + vo
        P = pos[vlb[ii, 0]].reshape(-1, 3, 3)
        nz = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])[:, 2]
        cnt[names[t]] += int((nz < 0).sum())
    with open(out, "w") as fo:
        for m, n in cnt.most_common():
            if n: fo.write("%d %s\n" % (n, m))
    print("materials", sum(1 for v in cnt.values() if v))

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
