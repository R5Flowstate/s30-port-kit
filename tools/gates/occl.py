import numpy as np, sys
p, hfg = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None
V = np.fromfile(p % 0x75, np.float32).reshape(-1, 3); I = np.fromfile(p % 0x76, np.uint16).reshape(-1, 3)
T = V[I]
print("occluder verts", len(V), "tris", len(I), "z range", V[:, 2].min(), V[:, 2].max())
n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]); area = np.linalg.norm(n, axis=1) / 2; nz = n[:, 2] / np.maximum(2 * area, 1e-9)
print("total area %.3g; up-facing (nz>0.7) area share %.2f; largest tri area %.3g" % (area.sum(), area[nz > 0.7].sum() / area.sum(), area.max()))
if hfg:
    G = np.load(hfg); O, S = -45500.0, 91000.0 / 4095
    rng = np.random.default_rng(0); above = []; dz = []
    for t in range(len(T)):
        if area[t] < 1e5: continue
        w = rng.dirichlet((1, 1, 1), 64); P = w @ T[t]
        gx = np.clip(((P[:, 0] - O) / S).round().astype(int), 0, 4095); gy = np.clip(((P[:, 1] - O) / S).round().astype(int), 0, 4095)
        z = G[gy, gx]; ok = ~np.isnan(z)
        if ok.sum() == 0: continue
        d = P[ok, 2] - z[ok]; dz.append(d)
    dz = np.concatenate(dz) if dz else np.zeros(0)
    print("samples on big occluder tris", len(dz), "occluder - terrain dz: p1 %.1f p50 %.1f p99 %.1f, within +-64u %.2f, above terrain %.2f" % (np.percentile(dz, 1), np.percentile(dz, 50), np.percentile(dz, 99), (np.abs(dz) < 64).mean(), (dz > 0).mean()))
