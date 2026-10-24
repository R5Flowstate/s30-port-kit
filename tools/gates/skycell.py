import kitpaths as KP
import numpy as np, sys, collections

from cellbsp import CellBsp
D, B, ext, t0, t1 = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5])
L = lambda i: np.fromfile(f"{D}/{B}.{i:04x}{ext}", np.uint8)
cb = CellBsp(D, B, ext, -1.0)
nd = L(0x77).view("<u4").reshape(-1, 8); cc = nd[:, 3] & 255; fc = nd[:, 3] >> 8; rc = nd[:, 7] & 255; ro = nd[:, 7] >> 8
refs = L(0x78).view("<u4")
par = np.full(len(nd), -1)
for i in np.flatnonzero(cc): par[fc[i]:fc[i] + cc[i]] = i
root = np.arange(len(nd))
for _ in range(64):
    m = par[root] >= 0
    if not m.any(): break
    root[m] = par[root[m]]
cellrefs = collections.defaultdict(set)
for i in np.flatnonzero(rc):
    cellrefs[int(root[i])].update(int(r) for r in refs[ro[i]:ro[i] + rc[i]])
print("cells", cb.ncell)
for z in (2000, 6000, 12000, 20000, 30000):
    P = np.array([[x, y, z] for x in range(-40000, 40001, 8000) for y in range(-40000, 40001, 8000)], float)
    c = cb.cells(P); u = collections.Counter(c.tolist())
    for cell, n in u.most_common(3):
        r = cellrefs.get(cell, set())
        orig = sum(1 for x in r if x < t0); terr = sum(1 for x in r if t0 <= x < t1)
        print("z %6d cell %5d (%3d pts) box %s..%s refs: orig meshes %d, terrain meshes %d, other %d" % (z, cell, n, np.round(cb.rmn[cell]), np.round(cb.rmx[cell]), orig, terr, len(r) - orig - terr))
