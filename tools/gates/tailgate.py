"""Compare every v10 txtr tail's streaming fields with the values implied by its dims/format."""
import kitpaths as KP
import sys, struct, collections

import check_v10_txtr_headers as C
from synth_v10_tail import mip_size_aligned
blob, aoff, acount, pages = C.parse_pak(sys.argv[1])
res = lambda p, o: pages[p][0] + o if p < len(pages) else None
bad = collections.Counter(); ex = collections.defaultdict(list); n = 0
over = []
for i in range(acount):
    b = aoff + i * 80
    if blob[b + 0x4C:b + 0x50] not in (b"txtr", b"TXTR", b"rtxt", b"RTXT"): continue
    guid = struct.unpack_from("<Q", blob, b)[0]
    so = res(*struct.unpack_from("<II", blob, b + 0x10))
    h = blob[so:so + 56]
    fmt, w, hh = struct.unpack_from("<HHH", h, 8); arr = h[0x10]
    ds = struct.unpack_from("<I", h, 0x14)[0]; perm, mand, opt, cdn = h[0x18], h[0x19], h[0x1A], h[0x1B]
    packed = struct.unpack_from("<7H", h, 0x20)
    np = res(*struct.unpack_from("<II", h, 0)); name = blob[np:blob.find(b"\0", np)].decode("latin1") if np else ""
    n += 1
    s = mand + opt; arr_e = max(1, arr)
    sizes = []; ww, w2 = w, hh
    for _ in range(s):
        sizes.append(mip_size_aligned(ww, w2, fmt)); ww = max(1, ww // 2); w2 = max(1, w2 // 2)
    exp_ds = sum(sizes) * arr_e if (s and arr_e == 1) else 0
    exp_pk = [(z - 1) // 4096 for z in sizes[:7]] + [0] * (7 - min(7, s))
    tag = "0x%016X %s fmt %d %dx%d arr %d perm/mand/opt %d/%d/%d" % (guid, name, fmt, w, hh, arr, perm, mand, opt)
    if ds != exp_ds: bad["dataSize"] += 1; ex["dataSize"].append("%s ds %d exp %d" % (tag, ds, exp_ds))
    if s and arr_e == 1 and list(packed) != exp_pk:
        bad["packed"] += 1; ex["packed"].append("%s packed %s exp %s" % (tag, list(packed), exp_pk))
        for k in range(min(7, s)):
            if (packed[k] + 1) * 4096 > ((sizes[k] + 4095) // 4096) * 4096: over.append((tag, k, packed[k], exp_pk[k])); break
    if not s and any(packed): bad["packed_nonstream"] += 1; ex["packed_nonstream"].append("%s packed %s" % (tag, list(packed)))
    if cdn != (1 if s and arr_e == 1 else 0): bad["minStream"] += 1; ex["minStream"].append("%s cdn %d" % (tag, cdn))
    if perm + mand + opt != C.total_mip_count(w, hh): bad["mipcount"] += 1; ex["mipcount"].append(tag)
print("txtr", n, dict(bad), "packed OVER-READ", len(over))
for k, v in ex.items():
    print("==", k, len(v))
    for x in v[:12]: print("  ", x)
for o in over[:20]: print("OVER", o)
