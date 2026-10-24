"""Append lit-bump world meshes to an S21 v51 client BSP (split lumps).

Source directory is only read. Output is a full copy with terrain meshes inserted
at the end of the opaque mesh range (level info lump 0x7B).
"""
import argparse
import os
import shutil
import struct
import sys

import numpy as np

import check_bsp

REF_CAP = 255
REF_OFF_MAX = 1 << 24
LM_PAGE = None   # landscape page: the 0x53 entry with tag 1, found at run time
MAT = "world/moss/icelandic_moss_01_wld_icelandic_dirt_01_wld_bm"
# flags>>9&3 == 1 is lit bump; the mesh vertex-type byte for that is 2.
VT_BYTE = {0: 1, 1: 2, 2: 0, 3: 3}


def bsp_file(folder):
    names = [n for n in os.listdir(folder) if n.endswith(".bsp") and ".bsp_lump" not in n]
    if len(names) != 1:
        raise SystemExit("expected one .bsp header in %s, found %s" % (folder, names))
    return names[0]


def lump_path(folder, base, idx):
    return os.path.join(folder, "%s.%04x.bsp_lump" % (base, idx))


def read(folder, base, idx):
    return open(lump_path(folder, base, idx), "rb").read()


def write(folder, base, idx, data):
    open(lump_path(folder, base, idx), "wb").write(data)


def norm_name(s):
    return s.replace("\\", "/").lower()


def as_str(x):
    if isinstance(x, bytes):
        return x.decode("latin1")
    return str(x)


def texdata_table(raw_td, raw_names):
    names = []
    rows = []
    for k in range(len(raw_td) // 16):
        off, w, h, fl = struct.unpack_from("<4i", raw_td, k * 16)
        end = raw_names.index(b"\0", off)
        names.append(raw_names[off:end].decode("latin1"))
        rows.append((off, w, h, fl))
    return names, rows


def load_npz(path):
    z = np.load(path, allow_pickle=True)
    need = ("pos", "nrm", "uv", "lmuv", "col", "tri",
            "mesh_first_tri", "mesh_tri_count", "mesh_material", "mesh_flags", "mat_names")
    missing = [k for k in need if k not in z.files]
    if missing:
        raise SystemExit("npz missing %s" % ", ".join(missing))
    pos = np.asarray(z["pos"], np.float32)
    nrm = np.asarray(z["nrm"], np.float32)
    uv = np.asarray(z["uv"], np.float32)
    lmuv = np.asarray(z["lmuv"], np.float32)
    col = np.asarray(z["col"], np.uint8)
    tri = np.asarray(z["tri"], np.uint32)
    first = np.asarray(z["mesh_first_tri"], np.int32)
    count = np.asarray(z["mesh_tri_count"], np.int32)
    mat = np.asarray(z["mesh_material"], np.int32)
    flags = np.asarray(z["mesh_flags"], np.uint32)
    names = [as_str(x) for x in z["mat_names"]]
    refs = None
    if "ref_node" in z.files:
        rf, rc, rn = (np.asarray(z[k], np.int64) for k in ("ref_first", "ref_count", "ref_node"))
        if len(rf) != len(first) or len(rc) != len(first):
            raise SystemExit("ref arrays must have one entry per mesh")
        refs = [rn[a:a + c].tolist() for a, c in zip(rf, rc)]
        if any(c < 1 for c in rc):
            raise SystemExit("mesh without a cell ref")
    n = len(pos)
    if pos.shape != (n, 3) or nrm.shape != (n, 3) or uv.shape != (n, 2) or lmuv.shape != (n, 2) or col.shape != (n, 4):
        raise SystemExit("vertex array shapes do not match pos [%d,3]" % n)
    if tri.ndim != 2 or tri.shape[1] != 3:
        raise SystemExit("tri must be [T,3]")
    if not (len(first) == len(count) == len(mat) == len(flags)) or len(first) == 0:
        raise SystemExit("mesh arrays must be the same non-zero length")
    if int(tri.max()) >= n:
        raise SystemExit("triangle index past vertex count")
    # The client's per-frame world batch list holds 0x800 entries and a new batch starts whenever consecutive visible
    # meshes differ in {distance LOD band, material sort, dlight bits}; past the cap the walk stops (distant terrain
    # flickers). Group by material, then Morton order inside a material so near/far LOD bands form long runs.
    c = pos[tri[first, 0]]
    q = np.clip(((c[:, :2] - c[:, :2].min(0)) / max(float(np.ptp(c[:, :2])), 1.0) * 1023), 0, 1023).astype(np.uint64)
    morton = np.zeros(len(first), np.uint64)
    for bit in range(10):
        morton |= ((q[:, 0] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(2 * bit)
        morton |= ((q[:, 1] >> np.uint64(bit)) & np.uint64(1)) << np.uint64(2 * bit + 1)
    order = np.lexsort((morton, mat))
    first, count, mat, flags = first[order], count[order], mat[order], flags[order]
    if refs is not None:
        refs = [refs[i] for i in order]
    return pos, nrm, uv, lmuv, col, tri, first, count, mat, flags, names, refs


def ensure_materials(names, rows, blob, wanted):
    by_norm = {}
    for i, name in enumerate(names):
        by_norm.setdefault(norm_name(name), i)
    blob = bytearray(blob)
    for raw in wanted:
        key = norm_name(raw)
        if key in by_norm:
            continue
        if len(rows) >= 2048:
            raise SystemExit("texdata limit 2048")
        text = raw.replace("\\", "/").encode("latin1") + b"\0"
        off = len(blob)
        blob += text
        # hu terrain blend texdata is 2048^2 with flags 0xA00.
        rows.append((off, 2048, 2048, 0xA00))
        names.append(raw.replace("\\", "/"))
        by_norm[key] = len(rows) - 1
    packed = b"".join(struct.pack("<4i", *row) for row in rows)
    return by_norm, bytes(blob), packed


def page_size(raw, page):
    if (page + 1) * 8 > len(raw):
        raise SystemExit("lightmap page %d is not in lump 0x53 (%d headers)" % (page, len(raw) // 8))
    return struct.unpack_from("<HH", raw, page * 8 + 4)


def nearest_cube(cubes, origin):
    if len(cubes) == 0:
        return 255
    if len(cubes) > 255:
        raise SystemExit("cubemap index does not fit the mesh u8")
    d = ((cubes.astype(np.float64) - origin) ** 2).sum(1)
    return int(d.argmin())


def overlap(mn, mx, a, b):
    return bool(np.all(mn <= b) and np.all(mx >= a))


def collect_nodes(mn, mx, mins, maxs, cc, fc, roots):
    hits = []
    stack = [int(r) for r in roots if overlap(mn, mx, mins[r], maxs[r])]
    while stack:
        i = stack.pop()
        c = int(cc[i])
        if c == 0:
            hits.append(i)
            continue
        a = int(fc[i])
        kids = [c_ for c_ in range(a, a + c) if overlap(mn, mx, mins[c_], maxs[c_])]
        if kids:
            stack.extend(kids)
        else:
            hits.append(i)
    return hits


def nearest_leaf(origin, mins, maxs, cc):
    leaves = np.flatnonzero(cc == 0)
    if len(leaves) == 0:
        raise SystemExit("cell tree has no leaves")
    o = origin.astype(np.float64)
    lo = mins[leaves].astype(np.float64)
    hi = maxs[leaves].astype(np.float64)
    d = np.maximum(np.maximum(lo - o, o - hi), 0.0)
    return int(leaves[int(np.linalg.norm(d, axis=1).argmin())])


def expand_chain(i, mn, mx, mins, maxs, parent):
    while i >= 0:
        mins[i] = np.minimum(mins[i], mn)
        maxs[i] = np.maximum(maxs[i], mx)
        i = int(parent[i])


def clip_bounds(mn, mx, node_mn, node_mx):
    a = np.maximum(mn, node_mn)
    b = np.minimum(mx, node_mx)
    if np.any(a > b):
        a, b = mn, mx
    row = np.zeros(8, np.float32)
    row[0:3] = a
    row[4:7] = b
    return row


def split_overflow(mins, maxs, cc, fc, parent, refs_per, bnd_per):
    """Move a leaf's refs onto two children until every node has <= REF_CAP refs."""
    pending = [i for i in range(len(refs_per)) if len(refs_per[i]) > REF_CAP]
    splits = 0
    while pending:
        i = pending.pop()
        if len(refs_per[i]) <= REF_CAP:
            continue
        if int(cc[i]) != 0:
            raise SystemExit("internal cell node %d holds %d refs" % (i, len(refs_per[i])))
        r = refs_per[i]
        b = bnd_per[i]
        extent = maxs[i] - mins[i]
        axis = int(np.argmax(extent))
        center = (b[:, 0:3] + b[:, 4:7]) * np.float32(0.5)
        mid = float(mins[i, axis] + maxs[i, axis]) * 0.5
        left = center[:, axis] < mid
        if not left.any() or left.all():
            left = np.zeros(len(r), np.bool_)
            left[:len(r) // 2] = True
        if not left.any() or left.all():
            raise SystemExit("cannot split node %d" % i)
        a_max = maxs[i].copy()
        a_max[axis] = np.float32(mid)
        b_min = mins[i].copy()
        b_min[axis] = np.float32(mid)
        ia = len(refs_per)
        ib = ia + 1
        mins = np.vstack([mins, mins[i], b_min])
        maxs = np.vstack([maxs, a_max, maxs[i]])
        cc = np.concatenate([cc, np.zeros(2, np.int64)])
        fc = np.concatenate([fc, np.zeros(2, np.int64)])
        parent = np.concatenate([parent, np.array([i, i], np.int64)])
        refs_per.append(r[left])
        refs_per.append(r[~left])
        bnd_per.append(np.stack([clip_bounds(b[k, 0:3], b[k, 4:7], mins[ia], maxs[ia]) for k in np.flatnonzero(left)]))
        bnd_per.append(np.stack([clip_bounds(b[k, 0:3], b[k, 4:7], mins[ib], maxs[ib]) for k in np.flatnonzero(~left)]))
        refs_per[i] = np.zeros(0, np.uint32)
        bnd_per[i] = np.zeros((0, 8), np.float32)
        cc[i] = 2
        fc[i] = ia
        splits += 1
        for k in (ia, ib):
            if len(refs_per[k]) > REF_CAP:
                pending.append(k)
        if len(refs_per) >= REF_OFF_MAX:
            raise SystemExit("cell node index exceeds 24 bits")
    return mins, maxs, cc, fc, parent, splits


def pack_nodes(mins, maxs, cc, fc, refs_per, bnd_per):
    # The cull walker marks a fully visible node's 0x25 total as one run starting at
    # its ref offset, so refs are laid out depth-first: own refs, then each child subtree.
    n = len(refs_per)
    chunks_r = []
    chunks_b = []
    ro = np.zeros(n, np.int64)
    rc = np.zeros(n, np.int64)
    is_child = np.zeros(n, np.bool_)
    for i in range(n):
        if cc[i]:
            is_child[int(fc[i]):int(fc[i]) + int(cc[i])] = True
    stack = [int(r) for r in np.flatnonzero(~is_child)[::-1]]
    visited = 0
    cursor = 0
    while stack:
        i = stack.pop()
        visited += 1
        r = refs_per[i]
        if len(r) > REF_CAP:
            raise SystemExit("node %d has %d refs" % (i, len(r)))
        ro[i] = cursor
        rc[i] = len(r)
        if len(r):
            chunks_r.append(np.asarray(r, np.uint32))
            chunks_b.append(bnd_per[i])
            cursor += len(r)
        if cc[i]:
            stack.extend(range(int(fc[i]) + int(cc[i]) - 1, int(fc[i]) - 1, -1))
    if visited != n:
        raise SystemExit("cell tree visits %d of %d nodes" % (visited, n))
    sub = rc.copy()
    for i in range(n - 1, -1, -1):
        if cc[i]:
            sub[i] += sub[int(fc[i]):int(fc[i]) + int(cc[i])].sum()
    ro[sub == 0] = 0
    if cursor >= REF_OFF_MAX:
        raise SystemExit("obj ref offset exceeds 24 bits (%d)" % cursor)
    if int(fc.max()) >= REF_OFF_MAX:
        raise SystemExit("child index exceeds 24 bits")
    nodes = np.zeros((len(refs_per), 8), np.uint32)
    fv = nodes.view("<f4")
    fv[:, 0:3] = mins
    fv[:, 4:7] = maxs
    nodes[:, 3] = cc.astype(np.uint32) | (fc.astype(np.uint32) << np.uint32(8))
    nodes[:, 7] = rc.astype(np.uint32) | (ro.astype(np.uint32) << np.uint32(8))
    refs = np.concatenate(chunks_r) if chunks_r else np.zeros(0, np.uint32)
    bounds = np.concatenate(chunks_b) if chunks_b else np.zeros((0, 8), np.float32)
    return nodes, refs, bounds


def refresh_header(folder, base):
    path = os.path.join(folder, base)
    hdr = bytearray(open(path, "rb").read())
    for i in range(128):
        if i == 0x69:
            continue
        lp = lump_path(folder, base, i)
        if os.path.isfile(lp):
            struct.pack_into("<i", hdr, 16 + 16 * i + 4, os.path.getsize(lp))
    open(path, "wb").write(hdr)


def write_synthetic(path):
    """Four 64-unit quads near mu5's center, on the collision median z."""
    centers = ((384.0, 256.0), (480.0, 256.0), (384.0, 360.0), (520.0, 400.0))
    z = -12.0
    half = 32.0
    pos, nrm, uv, lmuv, col, tris = [], [], [], [], [], []
    for k, (x, y) in enumerate(centers):
        base = len(pos)
        corners = ((-half, -half), (half, -half), (half, half), (-half, half))
        u0 = 0.05 + k * 0.08
        for j, (dx, dy) in enumerate(corners):
            pos.append((x + dx, y + dy, z))
            nrm.append((0.0, 0.0, 1.0))
            uv.append((0.0 if dx < 0 else 1.0, 0.0 if dy < 0 else 1.0))
            lmuv.append((u0 + (0.0 if dx < 0 else 0.04), 0.05 + (0.0 if dy < 0 else 0.04)))
            col.append((180, 160, 120, 128))
        tris.extend(((base, base + 1, base + 2), (base, base + 2, base + 3)))
    nmesh = len(centers)
    np.savez(
        path,
        pos=np.asarray(pos, np.float32),
        nrm=np.asarray(nrm, np.float32),
        uv=np.asarray(uv, np.float32),
        lmuv=np.asarray(lmuv, np.float32),
        col=np.asarray(col, np.uint8),
        tri=np.asarray(tris, np.uint32),
        mesh_first_tri=np.arange(nmesh, dtype=np.int32) * 2,
        mesh_tri_count=np.full(nmesh, 2, np.int32),
        mesh_material=np.zeros(nmesh, np.int32),
        mesh_flags=np.full(nmesh, 0x100A00, np.uint32),
        mat_names=np.array([MAT]),
    )


def inject(src, out, npz_path):
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(src, out)
    base = bsp_file(out)
    pos, nrm, uv, lmuv, col, tri, first, count, mat, flags, mat_names, mesh_refs = load_npz(npz_path)
    td_names, td_rows = texdata_table(read(out, base, 0x02), read(out, base, 0x0F))
    by_norm, name_blob, td_packed = ensure_materials(td_names, td_rows, read(out, base, 0x0F), mat_names)
    mat_ids = []
    for i in mat:
        if int(i) < 0 or int(i) >= len(mat_names):
            raise SystemExit("mesh material %d" % int(i))
        key = norm_name(mat_names[int(i)])
        if key not in by_norm:
            raise SystemExit("missing material %s" % mat_names[int(i)])
        mat_ids.append(by_norm[key])

    global LM_PAGE
    lmh = read(out, base, 0x53)
    land = [i // 8 for i in range(0, len(lmh), 8) if struct.unpack_from("<H", lmh, i + 2)[0] == 1]
    if len(land) != 1:
        raise SystemExit("expected one landscape (tag 1) lightmap page in 0x53, got %s" % land)
    LM_PAGE = land[0]
    wh = page_size(lmh, LM_PAGE)
    cubes = np.frombuffer(read(out, base, 0x2A), "<i4").reshape(-1, 4)[:, :3] if os.path.getsize(lump_path(out, base, 0x2A)) else np.zeros((0, 3), np.int32)
    li = list(struct.unpack("<5I3fI", read(out, base, 0x7B)))
    insert = li[0]
    old_fs, old_fp = li[2], li[3]

    meshes_b = read(out, base, 0x50)
    old_meshes = np.frombuffer(meshes_b, check_bsp.MESH_DT).copy()
    old_sorts = np.frombuffer(read(out, base, 0x52), check_bsp.SORT_DT).copy()
    old_idx = np.frombuffer(read(out, base, 0x4F), "<u2").copy()
    pos0 = os.path.getsize(lump_path(out, base, 0x03)) // 12
    nrm0 = os.path.getsize(lump_path(out, base, 0x1E)) // 12
    vb0 = os.path.getsize(lump_path(out, base, 0x49)) // 32

    pos_parts, nrm_parts, vb_parts, idx_parts = [], [], [], []
    new_meshes = []
    new_sorts = []
    aabbs = []
    pbase, nbase, vbase = pos0, nrm0, vb0
    sort_used = 0
    icursor = len(old_idx)
    vdt = np.dtype([
        ("p", "<u4"), ("n", "<u4"), ("uv", "<f4", 2), ("col", "u1", 4),
        ("lm", "<f4", 2), ("t", "<u4"),
    ])
    whf = np.asarray(wh, np.float64)
    for i in range(len(first)):
        fl = int(flags[i])
        if ((fl >> 9) & 3) != 1:
            raise SystemExit("mesh %d flags 0x%x are not lit bump" % (i, fl))
        c0 = int(first[i])
        c1 = c0 + int(count[i])
        if c0 < 0 or c1 > len(tri) or c1 <= c0:
            raise SystemExit("mesh %d triangle range %d+%d" % (i, c0, int(count[i])))
        tris = tri[c0:c1].astype(np.int64)
        base_v = int(tris.min())
        span = int(tris.max()) - base_v + 1
        if span <= 0 or span > 65536:
            raise SystemExit("mesh %d vertex span %d" % (i, span))
        local = tris - base_v
        rec_pos = pos[base_v:base_v + span]
        rec_lm = lmuv[base_v:base_v + span]
        vb = np.zeros(span, vdt)
        vb["p"] = np.arange(pbase, pbase + span, dtype=np.uint32)
        vb["n"] = np.arange(nbase, nbase + span, dtype=np.uint32)
        vb["uv"] = uv[base_v:base_v + span]
        # +28 = tangent index into 0x1E (bit 31 = bitangent sign); tangent = +u (world x) on the surface
        rn = nrm[base_v:base_v + span].astype(np.float64)
        tg = np.array([1.0, 0.0, 0.0]) - rn * rn[:, :1]
        tg /= np.maximum(np.linalg.norm(tg, axis=1, keepdims=True), 1e-9)
        vb["t"] = np.arange(nbase + span, nbase + 2 * span, dtype=np.uint32) | np.uint32(0x80000000)
        vb["lm"] = rec_lm
        vb["col"] = col[base_v:base_v + span]
        origin = (rec_pos.min(0) + rec_pos.max(0)) * np.float32(0.5)
        ext = np.maximum((rec_pos.max(0) - rec_pos.min(0)) * np.float32(0.5), np.float32(0))
        radius = max(float(np.linalg.norm(ext.astype(np.float64))), 1e-3)
        cube = nearest_cube(cubes, origin.astype(np.float64))
        # meshes of one material share a sort while its u16 vertex window has room (retail: ~1 sort per material)
        if new_sorts and new_sorts[-1][0] == mat_ids[i] and sort_used + span <= 65536:
            si = len(old_sorts) + len(new_sorts) - 1
            local = local + sort_used
        else:
            si = len(old_sorts) + len(new_sorts)
            if si > 65535:
                raise SystemExit("material sort index exceeds u16")
            new_sorts.append([mat_ids[i], LM_PAGE, 0, 0, vbase])
            sort_used = 0
        sort_used += span
        new_sorts[-1][3] = sort_used - 1
        mn, mx = rec_pos.min(0), rec_pos.max(0)
        for t0 in range(0, len(local), 65535):
            chunk = local[t0:t0 + 65535]
            used = np.unique(chunk)
            lux = np.floor(rec_lm.min(0).astype(np.float64) * whf + 1e-4)
            lux = np.clip(lux, 0, whf - 1).astype(np.int16)
            fv = int(chunk.min())
            new_meshes.append({
                "fi": icursor, "tri": len(chunk), "fv": fv, "lv": int(chunk.max()) - fv,
                "vt": VT_BYTE[1], "cube": cube, "lux": lux, "ms": si, "flags": fl,
                "mn": mn, "mx": mx, "origin": origin, "radius": radius, "ext": ext,
                "src": i,
            })
            flat = np.ascontiguousarray(chunk, dtype=np.uint16).reshape(-1)
            idx_parts.append(flat)
            icursor += int(flat.size)
        pos_parts.append(np.ascontiguousarray(rec_pos, np.float32))
        nrm_parts.append(np.ascontiguousarray(nrm[base_v:base_v + span], np.float32))
        nrm_parts.append(np.ascontiguousarray(tg, np.float32))
        vb_parts.append(vb.tobytes())
        pbase += span
        nbase += 2 * span
        vbase += span

    n_new = len(new_meshes)
    if n_new == 0:
        raise SystemExit("no meshes")
    new_fp = ((old_fs + n_new) + 63) & ~63
    delta = new_fp - old_fp

    # Mesh array: insert at the opaque/decal boundary. Records of existing meshes
    # keep their index-buffer and sort links; only indices >= insert move.
    add = np.zeros(n_new, check_bsp.MESH_DT)
    for i, m in enumerate(new_meshes):
        add[i]["fi"] = m["fi"]
        add[i]["tri"] = m["tri"]
        add[i]["fv"] = m["fv"]
        add[i]["lv"] = m["lv"]
        add[i]["vt"] = m["vt"]
        add[i]["cube"] = m["cube"]
        add[i]["lux"] = m["lux"]
        add[i]["fade"] = 0
        add[i]["ms"] = m["ms"]
        add[i]["flags"] = m["flags"]
    meshes_out = np.concatenate([old_meshes[:insert], add, old_meshes[insert:]])

    old_bounds = np.frombuffer(read(out, base, 0x51), "<f4").reshape(-1, 8).copy()
    add_b = np.zeros((n_new, 8), np.float32)
    for i, m in enumerate(new_meshes):
        add_b[i, 0:3] = m["origin"]
        add_b[i, 3] = m["radius"]
        add_b[i, 4:7] = m["ext"]
        add_b[i, 7] = 0.0
    bounds_out = np.concatenate([old_bounds[:insert], add_b, old_bounds[insert:]])

    sorts_out = np.zeros(len(old_sorts) + len(new_sorts), check_bsp.SORT_DT)
    sorts_out[:len(old_sorts)] = old_sorts
    for i, (td, lm, z, lv, vo) in enumerate(new_sorts):
        s = sorts_out[len(old_sorts) + i]
        s["td"] = td
        s["lm"] = lm
        s["z"] = z
        s["lv"] = lv
        s["vo"] = vo

    models = np.frombuffer(read(out, base, 0x0E), check_bsp.MODEL_DT).copy()
    all_pos = np.concatenate(pos_parts)
    models[0]["mins"] = np.minimum(models[0]["mins"], all_pos.min(0))
    models[0]["maxs"] = np.maximum(models[0]["maxs"], all_pos.max(0))
    for mod in models:
        if int(mod["nm"]) <= 0:
            if int(mod["fm"]) >= insert:
                mod["fm"] = int(mod["fm"]) + n_new
            continue
        if int(mod["fm"]) >= insert:
            mod["fm"] = int(mod["fm"]) + n_new
        elif int(mod["fm"]) + int(mod["nm"]) > insert:
            mod["nm"] = int(mod["nm"]) + n_new

    li[0] += n_new
    li[1] += n_new
    li[2] += n_new
    li[3] = new_fp

    # Cell refs: mesh ids below the old sky, then prop ids at/above the old prop base.
    refs = np.frombuffer(read(out, base, 0x78), "<u4").astype(np.int64)
    if ((refs >= old_fs) & (refs < old_fp)).any():
        raise SystemExit("obj refs in the sky/prop gap")
    orig = refs.copy()
    refs[(orig >= insert) & (orig < old_fs)] += n_new
    if delta:
        refs[orig >= old_fp] += delta
    refs = refs.astype(np.uint32)
    ob = np.frombuffer(read(out, base, 0x79), "<f4").reshape(-1, 8).copy()
    nodes = np.frombuffer(read(out, base, 0x77), "<u4").reshape(-1, 8).copy()
    cc = (nodes[:, 3] & np.uint32(0xFF)).astype(np.int64)
    fc = (nodes[:, 3] >> np.uint32(8)).astype(np.int64)
    rc = (nodes[:, 7] & np.uint32(0xFF)).astype(np.int64)
    ro = (nodes[:, 7] >> np.uint32(8)).astype(np.int64)
    if int(rc.sum()) != len(refs):
        raise SystemExit("obj ref slices do not partition the ref array")
    mins = nodes.view("<f4")[:, 0:3].copy()
    maxs = nodes.view("<f4")[:, 4:7].copy()
    parent = np.full(len(nodes), -1, np.int64)
    for i in range(len(nodes)):
        if cc[i]:
            parent[int(fc[i]):int(fc[i]) + int(cc[i])] = i
    roots = np.flatnonzero(parent < 0)
    refs_per = []
    bnd_per = []
    for i in range(len(nodes)):
        if rc[i]:
            a, b = int(ro[i]), int(ro[i]) + int(rc[i])
            refs_per.append(refs[a:b].copy())
            bnd_per.append(ob[a:b].copy())
        else:
            refs_per.append(np.zeros(0, np.uint32))
            bnd_per.append(np.zeros((0, 8), np.float32))

    fade_dist = np.frombuffer(read(out, base, 0x27), "<u2")
    def contains(i, mn, mx):
        return bool(np.all(mins[i] <= mn) and np.all(maxs[i] >= mx))

    placed = []
    for k, m in enumerate(new_meshes):
        if mesh_refs is not None:
            for h in dict.fromkeys(mesh_refs[m["src"]]):
                if h < 0 or h >= len(nodes) or fade_dist[h] != 65535:
                    raise SystemExit("mesh %d ref node %d is not a never-fade node" % (k, h))
                while len(refs_per[h]) >= REF_CAP and parent[h] >= 0:
                    h = int(parent[h])
                if len(refs_per[h]) >= REF_CAP:
                    raise SystemExit("no room for mesh %d" % k)
                row = np.array([*m["mn"], 0, *m["mx"], 0], np.float32)
                refs_per[h] = np.concatenate([refs_per[h], np.array([insert + k], np.uint32)])
                bnd_per[h] = np.concatenate([bnd_per[h], row.reshape(1, 8)], axis=0)
                placed.append(1)
            continue
        # one ref per mesh, at the deepest node whose box holds the whole mesh (retail: ~1.1 refs/mesh)
        top = [int(r) for r in roots if contains(int(r), m["mn"], m["mx"])]
        if not top:
            best = max(roots, key=lambda r: float(np.prod(np.clip(np.minimum(maxs[r], m["mx"]) - np.maximum(mins[r], m["mn"]), 0, None))))
            expand_chain(int(best), m["mn"], m["mx"], mins, maxs, parent)
            top = [int(best)]
        h = top[0]
        while cc[h]:
            kids = [c for c in range(int(fc[h]), int(fc[h]) + int(cc[h])) if contains(c, m["mn"], m["mx"]) and fade_dist[c] == 65535]
            if len(kids) != 1:
                break
            h = kids[0]
        while len(refs_per[h]) >= 255 and parent[h] >= 0:
            h = int(parent[h])
        if len(refs_per[h]) >= 255:
            raise SystemExit("no room for mesh %d" % k)
        row = np.array([*m["mn"], 0, *m["mx"], 0], np.float32)
        refs_per[h] = np.concatenate([refs_per[h], np.array([insert + k], np.uint32)])
        bnd_per[h] = np.concatenate([bnd_per[h], row.reshape(1, 8)], axis=0)
        placed.append(1)

    mins, maxs, cc, fc, parent, splits = split_overflow(mins, maxs, cc, fc, parent, refs_per, bnd_per)
    nodes_out, refs_out, ob_out = pack_nodes(mins, maxs, cc, fc, refs_per, bnd_per)
    _, _, _, _, sub, bad = check_bsp.subtree_totals(nodes_out)
    if bad:
        raise SystemExit("cell tree: %s" % bad[0])
    totals = np.frombuffer(read(out, base, 0x25), "<u4").copy()
    if len(totals) > len(sub):
        raise SystemExit("cell totals longer than the node array")

    # Writes.
    if name_blob != read(out, base, 0x0F):
        write(out, base, 0x0F, name_blob)
        write(out, base, 0x02, td_packed)
    write(out, base, 0x50, meshes_out.tobytes())
    write(out, base, 0x51, bounds_out.astype("<f4").tobytes())
    write(out, base, 0x52, sorts_out.tobytes())
    write(out, base, 0x4F, old_idx.tobytes() + b"".join(p.tobytes() for p in idx_parts))
    with open(lump_path(out, base, 0x03), "ab") as f:
        f.write(np.concatenate(pos_parts).tobytes())
    with open(lump_path(out, base, 0x1E), "ab") as f:
        f.write(np.concatenate(nrm_parts).tobytes())
    with open(lump_path(out, base, 0x49), "ab") as f:
        f.write(b"".join(vb_parts))
    write(out, base, 0x0E, models.tobytes())
    write(out, base, 0x7B, struct.pack("<5I3fI", *li))
    write(out, base, 0x77, nodes_out.tobytes())
    write(out, base, 0x78, refs_out.tobytes())
    write(out, base, 0x79, np.ascontiguousarray(ob_out, dtype="<f4").tobytes())
    write(out, base, 0x25, np.ascontiguousarray(sub[:len(totals)], dtype="<u4").tobytes())
    fade_path = lump_path(out, base, 0x27)
    if os.path.isfile(fade_path):
        fade = np.frombuffer(open(fade_path, "rb").read(), "<u2")
        if len(nodes_out) != len(nodes):
            if len(fade) != len(nodes):
                raise SystemExit("lump 0x27 is %d u16 for %d nodes" % (len(fade), len(nodes)))
            fade = np.concatenate([np.array(fade, copy=True), np.zeros(len(nodes_out) - len(nodes), np.uint16)])
            write(out, base, 0x27, fade.tobytes())
    if delta:
        csm = np.frombuffer(read(out, base, 0x64), "<u4").astype(np.int64)
        csm[csm >= old_fp] += delta
        write(out, base, 0x64, csm.astype("<u4").tobytes())
    refresh_header(out, base)
    print("inserted %d meshes at %d (%d obj refs, %d cell splits, prop id delta %d, lightmap page %d %dx%d)" % (
        n_new, insert, sum(placed), splits, delta, LM_PAGE, wh[0], wh[1]))
    return os.path.join(out, base)


def main(argv):
    ap = argparse.ArgumentParser(description="Inject terrain world meshes into an S21 client BSP")
    ap.add_argument("--src", required=True, help="client_v51 directory (not modified)")
    ap.add_argument("--out", required=True, help="client_v51_terrain directory")
    ap.add_argument("--npz", required=True, help="terrain_meshes.npz")
    ap.add_argument("--make-synthetic", action="store_true",
                    help="write --npz as four quads, then inject it")
    args = ap.parse_args(argv)
    if args.make_synthetic:
        write_synthetic(args.npz)
        print("wrote %s" % args.npz)
    bsp = inject(args.src, args.out, args.npz)
    print(bsp)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
