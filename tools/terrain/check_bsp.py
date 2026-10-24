"""Gate for an S21 client rBSP (split lumps).

Exit 1 if any check fails. Sidecar names may be lowercase or uppercase, with or
without a .client suffix (hu's extract uses uppercase + .client).
"""
import os
import struct
import sys

import numpy as np

# hu leaves 3 meshes below firstSky out of the cell tree; more than that is a miss.
UNREF_BUDGET = 3
# Mesh bounds are a sphere (origin + radius). Retail radii contain their verts
# with well under a unit of slack; allow a small float margin.
BOUND_SLACK = 2.0

# flags >> 9 & 3 -> vertex lump, stride. Lit-flat (0x48) is absent on these maps.
VERT = {
    0: (0x48, 28),
    1: (0x49, 32),
    2: (0x47, 20),
    3: (0x4A, 24),
}

MESH_DT = np.dtype([
    ("fi", "<u4"), ("tri", "<u2"), ("fv", "<u2"), ("lv", "<u2"),
    ("vt", "u1"), ("cube", "u1"), ("st", "u1", 4),
    ("lux", "<i2", 2), ("fade", "<u2"), ("ms", "<u2"), ("flags", "<u4"),
])
SORT_DT = np.dtype([
    ("td", "<u2"), ("lm", "<i2"), ("z", "<i2"), ("lv", "<u2"), ("vo", "<i4"),
])
MODEL_DT = np.dtype([
    ("mins", "<f4", 3), ("maxs", "<f4", 3),
    ("fm", "<i4"), ("nm", "<i4"), ("tail", "V32"),
])


def lump_paths(bsp):
    folder = os.path.dirname(bsp)
    base = os.path.basename(bsp)
    found = {}
    prefix = base + "."
    for name in os.listdir(folder):
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        hexpart = rest.split(".", 1)[0]
        if len(hexpart) != 4:
            continue
        try:
            idx = int(hexpart, 16)
        except ValueError:
            continue
        if not rest[4:].startswith(".bsp_lump"):
            continue
        path = os.path.join(folder, name)
        # Prefer a bare sidecar over a .client twin if both exist.
        if idx not in found or name.endswith(".bsp_lump"):
            found[idx] = path
    return found


class Map:
    def __init__(self, path):
        self.path = path
        self.paths = lump_paths(path)
        hdr = open(path, "rb").read()
        if len(hdr) < 16 + 128 * 16:
            raise SystemExit("header too small: %s" % path)
        self.magic, self.version, self.flags = struct.unpack_from("<4sHH", hdr, 0)
        self.hdr = [struct.unpack_from("<4i", hdr, 16 + 16 * i) for i in range(128)]

    def raw(self, i):
        p = self.paths.get(i)
        if not p:
            return b""
        return open(p, "rb").read()

    def size(self, i):
        p = self.paths.get(i)
        return os.path.getsize(p) if p else 0


def sprp_prop_count(m):
    g = m.raw(0x23)
    if len(g) < 20:
        return None, "game lump too small"
    cnt = struct.unpack_from("<i", g, 0)[0]
    if cnt < 1 or 4 + 16 * cnt > len(g):
        return None, "game lump directory"
    lump_ofs = m.hdr[0x23][0]
    for k in range(cnt):
        gid, _, ver, fo, fl = struct.unpack_from("<iHHii", g, 4 + 16 * k)
        if gid != 0x73707270:
            continue
        start = fo - lump_ofs
        if start < 0 or start + fl > len(g):
            return None, "sprp extent"
        d = g[start:start + fl]
        nd = struct.unpack_from("<i", d, 0)[0]
        o = 4 + 128 * nd
        if o + 12 > len(d):
            return None, "sprp dictionary"
        return struct.unpack_from("<I", d, o)[0], None
    return None, "no sprp"


def pos_index(path, stride):
    mm = np.memmap(path, dtype=np.uint8, mode="r")
    n = len(mm) // stride
    return np.ndarray(n, dtype="<u4", buffer=mm, strides=(stride,))


def subtree_totals(nodes):
    cc = (nodes[:, 3] & np.uint32(0xFF)).astype(np.int64)
    fc = (nodes[:, 3] >> np.uint32(8)).astype(np.int64)
    rc = (nodes[:, 7] & np.uint32(0xFF)).astype(np.int64)
    n = len(nodes)
    parent = np.full(n, -1, np.int64)
    bad = []
    for i in range(n):
        if cc[i] == 0:
            continue
        a, b = int(fc[i]), int(fc[i] + cc[i])
        if a < 0 or b > n:
            bad.append("node %d child range %d+%d" % (i, a, int(cc[i])))
            continue
        parent[a:b] = i
    sub = rc.copy()
    for i in range(n - 1, -1, -1):
        p = int(parent[i])
        if p >= 0:
            sub[p] += sub[i]
    return cc, fc, rc, (nodes[:, 7] >> np.uint32(8)).astype(np.int64), sub, bad


def check_header(m):
    err = []
    if m.magic != b"rBSP":
        err.append("magic %r" % m.magic)
    if m.version not in (51, 52) or m.flags != 1:
        err.append("version %d flags %d" % (m.version, m.flags))
    for i, (ofs, ln, ver, un) in enumerate(m.hdr):
        sz = m.size(i)
        if sz == 0:
            # 0x39 is present in the hu header and absent from the extract.
            if ln != 0 and i != 0x39:
                err.append("lump %02X header %d has no sidecar" % (i, ln))
            continue
        if i == 0x69:
            if ln < sz:
                err.append("lump 69 header %d < file %d" % (ln, sz))
        elif ln != sz:
            err.append("lump %02X header %d != file %d" % (i, ln, sz))
    return err


def check_level(m):
    err = []
    raw = m.raw(0x7B)
    if len(raw) != 36:
        return ["level info is %d bytes" % len(raw)]
    li = struct.unpack("<5I3fI", raw)
    fd, ft, fs, fp, nprops = li[:5]
    nmesh = m.size(0x50) // 28
    if not (0 <= fd <= ft <= fs <= nmesh):
        err.append("ranges decal %d trans %d sky %d meshes %d" % (fd, ft, fs, nmesh))
    if fp != (fs + 63) & ~63:
        err.append("firstStaticProp %d != align64(firstSky %d)" % (fp, fs))
    props, why = sprp_prop_count(m)
    if props is None:
        err.append(why)
    elif props != nprops:
        err.append("numStaticProps %d != sprp %d" % (nprops, props))
    models = np.frombuffer(m.raw(0x0E), MODEL_DT)
    if len(m.raw(0x0E)) != len(models) * 64:
        err.append("model lump stride")
    if nmesh:
        cover = np.zeros(nmesh, np.int8)
        for i, mod in enumerate(models):
            if int(mod["nm"]) <= 0:
                continue
            a, b = int(mod["fm"]), int(mod["fm"]) + int(mod["nm"])
            if a < 0 or b > nmesh:
                err.append("model %d mesh range %d+%d" % (i, a, int(mod["nm"])))
                continue
            cover[a:b] += 1
        if (cover != 1).any():
            err.append("mesh model cover uncovered %d multi %d" % (
                int((cover == 0).sum()), int((cover > 1).sum())))
    return err


def check_indices(m):
    err = []
    if m.size(0x50) % 28 or m.size(0x52) % 12 or m.size(0x4F) % 2:
        return ["mesh/sort/index stride"]
    meshes = np.frombuffer(m.raw(0x50), MESH_DT)
    sorts = np.frombuffer(m.raw(0x52), SORT_DT)
    indices = np.frombuffer(m.raw(0x4F), "<u2")
    if MESH_DT.itemsize != 28 or SORT_DT.itemsize != 12:
        return ["dtype padding"]
    vcount = {}
    pidx = {}
    for vt, (lump, stride) in VERT.items():
        sz = m.size(lump)
        if sz == 0:
            vcount[vt] = 0
            continue
        if sz % stride:
            err.append("vertex lump %02X size %d not a multiple of %d" % (lump, sz, stride))
            vcount[vt] = 0
            continue
        vcount[vt] = sz // stride
        pidx[vt] = pos_index(m.paths[lump], stride)
    npos = m.size(0x03) // 12
    if m.size(0x03) % 12:
        err.append("positions stride")
    # Window end for a vertexOffset is the next offset used by the same vertex lump.
    vos = {vt: set() for vt in VERT}
    for mi, mesh in enumerate(meshes):
        if int(mesh["tri"]) == 0:
            continue
        vt = int((int(mesh["flags"]) >> 9) & 3)
        ms = int(mesh["ms"])
        if ms >= len(sorts):
            continue
        vos[vt].add(int(sorts[ms]["vo"]))
    end = {}
    for vt, starts in vos.items():
        ordered = sorted(starts)
        stops = ordered[1:] + [vcount.get(vt, 0)]
        for a, b in zip(ordered, stops):
            end[(vt, a)] = b
    bad_win = bad_span = bad_pos = 0
    shown = []
    for mi, mesh in enumerate(meshes):
        tri = int(mesh["tri"])
        if tri == 0:
            continue
        fi = int(mesh["fi"])
        if fi < 0 or fi + tri * 3 > len(indices):
            bad_win += 1
            if len(shown) < 6:
                shown.append("mesh %d index range %d+%d" % (mi, fi, tri * 3))
            continue
        ms = int(mesh["ms"])
        if ms >= len(sorts):
            bad_win += 1
            if len(shown) < 6:
                shown.append("mesh %d sort %d" % (mi, ms))
            continue
        vt = int((int(mesh["flags"]) >> 9) & 3)
        vo = int(sorts[ms]["vo"])
        ind = indices[fi:fi + tri * 3].astype(np.int64)
        if int(ind.max()) > 65535:
            bad_win += 1
        stop = end.get((vt, vo), vcount.get(vt, 0))
        abs_i = ind + vo
        if vo < 0 or int(abs_i.min()) < vo or int(abs_i.max()) >= stop or int(abs_i.max()) >= vcount.get(vt, 0):
            bad_win += 1
            if len(shown) < 6:
                shown.append("mesh %d idx %d..%d vo %d window %d verts %d" % (
                    mi, int(ind.min()), int(ind.max()), vo, stop, vcount.get(vt, 0)))
        if int(mesh["fv"]) != int(ind.min()) or int(mesh["fv"]) + int(mesh["lv"]) != int(ind.max()):
            bad_span += 1
            if len(shown) < 6:
                shown.append("mesh %d fv %d lv %d idx %d..%d" % (
                    mi, int(mesh["fv"]), int(mesh["lv"]), int(ind.min()), int(ind.max())))
        if vt in pidx and int(abs_i.max()) < len(pidx[vt]):
            if int(pidx[vt][abs_i].max()) >= npos:
                bad_pos += 1
    if bad_win:
        err.append("%d meshes outside sort window" % bad_win)
    if bad_span:
        err.append("%d meshes fv/lv != index span" % bad_span)
    if bad_pos:
        err.append("%d meshes position index out of range" % bad_pos)
    err.extend(shown)
    return err


def check_bounds(m):
    err = []
    if m.size(0x51) != m.size(0x50) // 28 * 32:
        return ["mesh bounds count != meshes"]
    meshes = np.frombuffer(m.raw(0x50), MESH_DT)
    sorts = np.frombuffer(m.raw(0x52), SORT_DT)
    indices = np.frombuffer(m.raw(0x4F), "<u2")
    bounds = np.frombuffer(m.raw(0x51), "<f4").reshape(-1, 8)
    pos = np.memmap(m.paths[0x03], dtype="<f4", mode="r").reshape(-1, 3) if m.size(0x03) else None
    pidx = {}
    for vt, (lump, stride) in VERT.items():
        if m.size(lump) and m.size(lump) % stride == 0:
            pidx[vt] = pos_index(m.paths[lump], stride)
    bad = 0
    shown = []
    for mi, mesh in enumerate(meshes):
        tri = int(mesh["tri"])
        if tri == 0 or pos is None:
            continue
        vt = int((int(mesh["flags"]) >> 9) & 3)
        if vt not in pidx:
            continue
        ms = int(mesh["ms"])
        if ms >= len(sorts):
            continue
        fi = int(mesh["fi"])
        if fi + tri * 3 > len(indices):
            continue
        vo = int(sorts[ms]["vo"])
        ind = indices[fi:fi + tri * 3].astype(np.int64) + vo
        if ind.min() < 0 or ind.max() >= len(pidx[vt]):
            continue
        pi = pidx[vt][np.unique(ind)]
        if pi.max() >= len(pos):
            continue
        p = np.asarray(pos[pi], np.float64)
        o = bounds[mi, 0:3].astype(np.float64)
        rad = float(bounds[mi, 3])
        if p.size and np.linalg.norm(p - o, axis=1).max() > rad + BOUND_SLACK:
            bad += 1
            if len(shown) < 4:
                shown.append("mesh %d radius %.2f" % (mi, rad))
    if bad:
        err.append("%d mesh bounds do not contain their vertices" % bad)
        err.extend(shown)
    return err


def check_visibility(m):
    err = []
    raw = m.raw(0x7B)
    if len(raw) != 36:
        return ["no level info"]
    fd, ft, fs, fp, nprops = struct.unpack("<5I3fI", raw)[:5]
    nmesh = m.size(0x50) // 28
    nodes_b = m.raw(0x77)
    refs = np.frombuffer(m.raw(0x78), "<u4")
    if len(nodes_b) % 32:
        return ["cell node stride"]
    nodes = np.frombuffer(nodes_b, "<u4").reshape(-1, 8)
    if m.size(0x79) != len(refs) * 32:
        err.append("obj ref bounds %d != refs %d" % (m.size(0x79) // 32, len(refs)))
    cc, fc, rc, ro, sub, bad = subtree_totals(nodes)
    err.extend(bad[:8])
    if ((rc > 0) & ((ro < 0) | (ro + rc > len(refs)))).any():
        err.append("obj ref slice out of range")
    if len(refs):
        gap = ((refs >= fs) & (refs < fp)).sum()
        if gap:
            err.append("%d obj refs sit in the sky/prop gap" % int(gap))
        over = (refs >= fp + nprops).sum()
        if over:
            err.append("%d obj refs past the prop id space" % int(over))
        mesh_ids = refs[refs < fs]
        if len(mesh_ids) and int(mesh_ids.max()) >= nmesh:
            err.append("mesh obj ref >= mesh count")
        if nmesh:
            seen = np.zeros(nmesh, np.uint8)
            ok = mesh_ids[mesh_ids < nmesh]
            seen[ok] = 1
            missing = int((seen[:fs] == 0).sum()) if fs <= nmesh else nmesh
            if missing > UNREF_BUDGET:
                err.append("%d meshes below firstSky are unreferenced (budget %d)" % (missing, UNREF_BUDGET))
    # A fully visible node marks its whole subtree as one run from its own ref offset.
    lo = np.where(rc > 0, ro, np.int64(1) << 40)
    hi = np.where(rc > 0, ro + rc, -1)
    for i in range(len(nodes) - 1, -1, -1):
        if cc[i]:
            a, b = int(fc[i]), int(fc[i] + cc[i])
            lo[i] = min(lo[i], int(lo[a:b].min()))
            hi[i] = max(hi[i], int(hi[a:b].max()))
    has = sub > 0
    broken = int((has & ((hi - lo != sub) | (lo != ro))).sum())
    if broken:
        err.append("%d cell nodes whose subtree refs are not one run from the node's offset" % broken)
    totals = m.raw(0x25)
    if totals:
        t = np.frombuffer(totals, "<u4")
        if len(t) > len(nodes) or not np.array_equal(t, sub[:len(t)].astype(np.uint32)):
            err.append("cell subtree totals prefix mismatch (%d u32, %d nodes)" % (len(t), len(nodes)))
    # CSM ids are shadow meshes or static props, the same split hu uses.
    crefs = np.frombuffer(m.raw(0x64), "<u4") if m.size(0x64) else np.zeros(0, np.uint32)
    nsm = m.size(0x7F) // 12
    if len(crefs):
        low = crefs < fp
        if low.any() and (int(crefs[low].max()) >= nsm or int((crefs < nsm).sum()) != int(low.sum())):
            err.append("csm refs below firstStaticProp are not exactly the shadow meshes")
        if ((crefs >= fp) & (crefs >= fp + nprops)).any() or (crefs >= fp + nprops).any():
            err.append("csm prop ref out of range")
        if ((crefs >= nsm) & (crefs < fp)).any():
            err.append("csm refs in the shadow/prop gap")
    return err


# S21 world meshes share one per-frame index buffer of 0x900000 indices; the walk stops at the first mesh
# that does not fit, so a map whose world triangles exceed it loses every later mesh when enough is in view.
S21_WORLD_TRI_BUDGET = 0x900000 // 3
WORLD_TRI_BUDGET = S21_WORLD_TRI_BUDGET


def check_tri_budget(m):
    raw = m.raw(0x7B)
    if len(raw) != 36:
        return ["no level info"]
    first_sky = struct.unpack("<5I3fI", raw)[2]
    meshes = np.frombuffer(m.raw(0x50), MESH_DT)
    tris = int(meshes["tri"][:first_sky].astype(np.int64).sum())
    if tris > WORLD_TRI_BUDGET:
        return ["world triangles below firstSky %d > per-frame budget %d" % (tris, WORLD_TRI_BUDGET)]
    return []


CHECKS = (
    ("header lengths", check_header),
    ("level info", check_level),
    ("indices", check_indices),
    ("mesh bounds", check_bounds),
    ("visibility", check_visibility),
    ("world triangle budget", check_tri_budget),
)


def main(argv):
    global WORLD_TRI_BUDGET
    if len(argv) == 3 and argv[1] == "--world-tri-budget":
        WORLD_TRI_BUDGET = int(argv[2], 0); argv = argv[:1]
    if len(argv) != 1:
        print("usage: check_bsp.py <map.bsp> [--world-tri-budget N]  (stock S21 client: 0x900000 indices / 3)")
        return 2
    m = Map(argv[0])
    failed = 0
    for name, fn in CHECKS:
        err = fn(m)
        if err:
            failed += 1
            print("FAIL %s" % name)
            for line in err[:12]:
                print("  %s" % line)
        else:
            print("PASS %s" % name)
    print("%s %s" % (os.path.basename(argv[0]), "FAIL" if failed else "PASS"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
