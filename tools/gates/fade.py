import numpy as np, sys, collections, struct
D, B, ext = sys.argv[1], sys.argv[2], sys.argv[3]; lo, hi = int(sys.argv[4]), int(sys.argv[5])
L = lambda i: np.fromfile(f"{D}/{B}.{i:04x}{ext}", np.uint8)
nd = L(0x77).view("<u4").reshape(-1, 8); rc = nd[:, 7] & 255; ro = nd[:, 7] >> 8
refs = L(0x78).view("<u4"); fade = L(0x27).view("<u2")
print("nodes", len(nd), "fade entries", len(fade))
c = collections.Counter(); cnt = 0
for i in np.flatnonzero(rc):
    r = refs[ro[i]:ro[i] + rc[i]]
    k = ((r >= lo) & (r < hi)).sum()
    if k: c[int(fade[i]) if i < len(fade) else -1] += int(k); cnt += k
print("refs in range", cnt)
for f, n in sorted(c.items(), key=lambda x: -x[1])[:12]: print("  fade %5d : %d refs" % (f, n))
