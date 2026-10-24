#!/usr/bin/env python3
"""patch_shdr_cbremap.py -- run the full S25+ -> S21 shader transform pipeline
(lib/shader/cb_remap.convert_blob) over every DXBC blob in the pak.

Passes (see cb_remap.py's module docstring for the full derivation):
  P1     CBufUberDynamic layout remap (materialID cbN[1]->cbN[4], dcl
         resize, RDEF honesty). Idempotent: size-80/materialID@64 blobs
         (the state this pak is already in after the first cbremap run)
         are recognized and skipped.
  P2     Exposure neutralization: x-only reads of CBufUberDynamic reg 0
         replaced with immediate 0.0 (genuine S21 never reads exposure
         interaction; the newer-season counteraction block is gated on rate != 0).
  REPAIR The earlier wrong exposure relocation (reads of CBufUberStatic
         reg 36 .y) is unambiguously ours and is rewritten to 0.0 as well.
  P3     Cubemap ambient: cb6/icb dp4 extraction rewritten to the genuine
         S21 g_cubemapSamples structured-buffer load (ld_structured t72,
         stride 4), dcl swapped in place.
  P4     Dither-discard removal: discards fed by DitherTexture (t64) are
         NOPed; alpha-test discards preserved.

Slot resolution now uses the RDEF resource-binding table's REAL BindPoints
(the earlier claim they were stripped was false); the SHEX dcl scan remains
as a cross-check only.

SIZE POLICY: transforms are size-preserving except a rare P3 case where
the one-dword ld_structured growth cannot be funded from dead and/ushr
producers. Such blobs are NOT written in place -- they are counted and
reported separately for a pipeline rebuild.

Page/asset layout reuses the same repak-contiguous-page model as
patch_shdr_features.py (aligned=False -- this pak's pages are written back
to back with no alignment padding, self-validated against the built pak's
entry sizes). Blobs are located and deduped by absolute FILE OFFSET, since
multiple shdr asset entries (permutation variants) can share the same
physical DXBC blob via negative-dataSize child references.

Usage:
  python patch_shdr_cbremap.py [pak_path]
    pak_path defaults to out/mp_rr_freedm_skulltown_s27.rpak next to this
    script.
"""
import kitpaths as KP
import struct
import sys
import os
import shutil

W = os.path.dirname(os.path.abspath(__file__))


import dxbc
import cb_remap

DEFAULT_PAK = os.path.join(W, 'out', 'mp_rr_freedm_skulltown_s27.rpak')
ORACLE_DIR = os.path.join(W, 'oracle_s21_shdr')


def _oracle_guids():
    skip = set()
    if not os.path.isdir(ORACLE_DIR):
        return skip
    for dp, _, fs in os.walk(ORACLE_DIR):
        for fn in fs:
            if not fn.lower().endswith('.msw'):
                continue
            h = fn[2:-4] if fn.lower().startswith('0x') else fn[:-4]
            try:
                skip.add(int(h, 16))
            except ValueError:
                pass
    return skip

u16 = lambda b, o: struct.unpack_from('<H', b, o)[0]
u32 = lambda b, o: struct.unpack_from('<I', b, o)[0]
i32 = lambda b, o: struct.unpack_from('<i', b, o)[0]
u64 = lambda b, o: struct.unpack_from('<Q', b, o)[0]
FCC_SHDR = struct.unpack('<I', b'shdr')[0]


def parse(d, aligned=False):
    """Repak page/asset model (contiguous pages -- aligned=False for our
    repak-built paks). Mirrors shdr_s21oracle.py's parse()."""
    sp = u16(d, 0x48); opt = u16(d, 0x4A); slabs = u16(d, 0x4C); pc = u16(d, 0x4E)
    ptrc = u32(d, 0x54); ac = u32(d, 0x58); usec = u32(d, 0x5C); depc = u32(d, 0x60)
    u7 = u32(d, 0x64); u8 = u32(d, 0x68); e74 = u32(d, 0x74)
    pages_off = 0x80 + sp + opt + slabs * 16
    desc_off = pages_off + pc * 12
    assets_off = desc_off + ptrc * 8
    gd_off = assets_off + ac * 80
    rel_off = gd_off + usec * 8
    pdata = rel_off + depc * 4 + u7 * 4 + u8 + e74
    pages = []
    fo = pdata

    def ialign(v, a):
        return (v + a - 1) // a * a if a else v

    for i in range(pc):
        al = u32(d, pages_off + i * 12 + 4)
        ds = i32(d, pages_off + i * 12 + 8)
        if aligned:
            fo = ialign(fo, al)
        pages.append((fo, ds))
        fo += ds
    relocs = set()
    for i in range(ptrc):
        relocs.add((u32(d, desc_off + i * 8), u32(d, desc_off + i * 8 + 4)))
    return pages, assets_off, ac, relocs


def res(pages, i, off):
    return None if i < 0 or i >= len(pages) else pages[i][0] + off


def find_shdr_blobs(d):
    """Scan every shdr asset's entries, resolve each entry's physical DXBC
    blob via the relocation map, dedupe by absolute file offset (entries
    can share a blob via negative-dataSize child references). Returns a
    list of dicts: {offset, size, guid, entry_index}."""
    pages, ao0, ac, relocs = parse(d, aligned=False)
    seen = {}
    order = []

    for i in range(ac):
        ao = ao0 + i * 80
        if u32(d, ao + 0x4C) != FCC_SHDR:
            continue
        guid = u64(d, ao)
        hpi, hpo = i32(d, ao + 0x10), i32(d, ao + 0x14)
        ho = res(pages, hpi, hpo)
        cpi, cpo = i32(d, ao + 0x18), i32(d, ao + 0x1C)
        co = res(pages, cpi, cpo)
        if ho is None or co is None:
            continue

        typ = d[ho + 8]
        if typ > 5:
            continue  # child/reference asset -- no own bytecode entries

        env = d[ho + 9]
        sc = list(d[ho + 10:ho + 16])
        mask = d[ho + 16]
        if env == 0xFF:
            n = (1 if sc[0] else 0) + 1
            for k in (1, 2, 3):
                if sc[k]:
                    n *= 2
            if mask:
                n *= 2
        else:
            n = env

        esz = 24 if typ == 1 else 16  # type==1 (vertex): 24B entries, else 16B
        for k in range(min(n, 32)):
            eo = co + k * esz
            dslot = (cpi, cpo + k * esz)
            if dslot not in relocs:
                continue
            dptr = res(pages, i32(d, eo), i32(d, eo + 4))
            dsz = u32(d, eo + 8)
            if dptr is None or dsz <= 32:
                continue
            if d[dptr:dptr + 4] != b'DXBC':
                continue
            tot = u32(d, dptr + 24)
            if not (32 < tot <= dsz + 64):
                continue
            if dptr in seen:
                continue
            seen[dptr] = {'offset': dptr, 'size': tot, 'guid': guid, 'entry_index': k}
            order.append(dptr)

    return [seen[off] for off in order]


def main():
    pak_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PAK
    if not os.path.isfile(pak_path):
        print(f"ERROR: pak not found: {pak_path}")
        return 1

    bak_path = pak_path + '.pre_p234_bak'
    if not os.path.isfile(bak_path):
        shutil.copy2(pak_path, bak_path)
        print(f"backup -> {bak_path}")
    else:
        print(f"backup already exists, leaving it: {bak_path}")

    data = bytearray(open(pak_path, 'rb').read())
    blobs = find_shdr_blobs(bytes(data))
    print(f"pak: {pak_path}")
    print(f"unique shdr DXBC blobs found: {len(blobs)}")

    n_scanned = 0
    n_patched = 0
    n_untouched = 0
    n_size_changed = 0

    tot = {
        'p1_applied': 0, 'p1_already': 0,
        'usages_remapped': 0, 'dcl_resized': 0, 'rdef_patched': 0, 'bounds_fixed': 0,
        'p2_zeroed': 0, 'p2_mixed': 0,
        'repair_zeroed': 0, 'repair_mixed': 0,
        'p3_blobs': 0, 'p3_sites': 0, 'p3_prod_nopped': 0, 'p3_prod_kept': 0,
        'p3_dcl_swapped': 0, 'p3_skipped_sites': 0, 'p3_already': 0,
        'p4_blobs': 0, 'p4_nopped': 0, 'p4_kept': 0, 'p4_inconclusive': 0,
        'p5_applied': 0, 'p5_renamed': 0,
    }
    all_reasons = []
    size_changed_blobs = []

    for b in blobs:
        n_scanned += 1
        off, sz = b['offset'], b['size']
        blob = bytes(data[off:off + sz])
        assert blob[:4] == b'DXBC', f"blob at file offset {off} is not DXBC -- corrupt scan"

        try:
            patched, report = cb_remap.convert_blob(blob)
        except AssertionError as e:
            print(f"  FATAL invariant violation at offset {off} guid=0x{b['guid']:016X}: {e}")
            raise

        if report['reasons']:
            all_reasons.extend((off, b['guid'], r) for r in report['reasons'])

        p1, p2, rp, p3, p4 = (report['p1'], report['p2'], report['repair'],
                              report['p3'], report['p4'])
        tot['p1_applied'] += 1 if p1['applied'] else 0
        tot['p1_already'] += 1 if p1['already'] else 0
        tot['usages_remapped'] += p1['usages_remapped']
        tot['dcl_resized'] += p1['dcl_resized']
        tot['rdef_patched'] += p1['rdef_patched']
        tot['bounds_fixed'] += p1['bounds_fixed']
        tot['p2_zeroed'] += p2['zeroed']
        tot['p2_mixed'] += p2['mixed_skipped']
        tot['repair_zeroed'] += rp['zeroed']
        tot['repair_mixed'] += rp['mixed_skipped']
        tot['p3_blobs'] += 1 if p3['sites'] else 0
        tot['p3_sites'] += p3['sites']
        tot['p3_prod_nopped'] += p3['producers_nopped']
        tot['p3_prod_kept'] += p3['producers_kept']
        tot['p3_dcl_swapped'] += p3['dcl_swapped']
        tot['p3_skipped_sites'] += p3['skipped_sites']
        tot['p3_already'] += 1 if p3['already'] else 0
        tot['p4_blobs'] += 1 if (p4['dither_nopped'] or p4['kept'] or p4['inconclusive']) else 0
        tot['p4_nopped'] += p4['dither_nopped']
        tot['p4_kept'] += p4['kept']
        tot['p4_inconclusive'] += p4['inconclusive']
        tot['p5_applied'] += report['p5']['applied']
        tot['p5_renamed'] += report['p5']['renamed']

        if not report['patched']:
            n_untouched += 1
            continue

        if report['size_changed'] or len(patched) != sz:
            n_size_changed += 1
            size_changed_blobs.append((off, b['guid'], sz, len(patched)))
            print(f"  SIZE-CHANGED (not written in place) offset={off} "
                  f"guid=0x{b['guid']:016X}: {sz} -> {len(patched)} bytes")
            continue

        if not dxbc.verify_hash(patched):
            print(f"  FATAL checksum mismatch after patch at offset {off} guid=0x{b['guid']:016X}")
            raise AssertionError("checksum verify failed post-patch")

        data[off:off + sz] = patched
        n_patched += 1

    open(pak_path, 'wb').write(data)

    print(f"\n{'=' * 60}")
    print(f"SUMMARY")
    print(f"{'=' * 60}")
    print(f"blobs scanned:                 {n_scanned}")
    print(f"patched in place:              {n_patched}")
    print(f"untouched (no applicable pass): {n_untouched}")
    print(f"size-changed (NOT written, needs pipeline rebuild): {n_size_changed}")
    print()
    print(f"P1 applied / already-remapped:            {tot['p1_applied']} / {tot['p1_already']}")
    print(f"P1 usages_remapped (cbN[1]->cbN[4]):      {tot['usages_remapped']}")
    print(f"P1 dcl_resized / rdef_patched / bounds:   {tot['dcl_resized']} / {tot['rdef_patched']} / {tot['bounds_fixed']}")
    print(f"P2 exposure reads zeroed:                 {tot['p2_zeroed']}")
    print(f"P2 mixed-component reads left (logged):   {tot['p2_mixed']}")
    print(f"REPAIR old UberStatic[36].y reads zeroed: {tot['repair_zeroed']}")
    print(f"REPAIR mixed skipped:                     {tot['repair_mixed']}")
    print(f"P3 blobs converted / sites:               {tot['p3_blobs']} / {tot['p3_sites']}")
    print(f"P3 producers NOPed / kept-live:           {tot['p3_prod_nopped']} / {tot['p3_prod_kept']}")
    print(f"P3 dcl cb6->t72 swaps:                    {tot['p3_dcl_swapped']}")
    print(f"P3 sites skipped (loud) / already-conv:   {tot['p3_skipped_sites']} / {tot['p3_already']}")
    print(f"P4 blobs with DitherTexture:              {tot['p4_blobs']}")
    print(f"P4 dither discards NOPed:                 {tot['p4_nopped']}")
    print(f"P4 non-dither discards preserved:         {tot['p4_kept']}")
    print(f"P4 inconclusive (skipped loud):           {tot['p4_inconclusive']}")
    print(f"P5 camera 928->880 / names rewritten:     {tot['p5_applied']} / {tot['p5_renamed']}")

    if size_changed_blobs:
        print(f"\nsize-changed blobs ({len(size_changed_blobs)}) -- rebuild the pak "
              f"through the pipeline to take these:")
        for off, guid, old, new in size_changed_blobs:
            print(f"  offset={off} guid=0x{guid:016X}: {old} -> {new}")

    if all_reasons:
        print(f"\nnon-empty per-blob log reasons ({len(all_reasons)}):")
        for off, guid, r in all_reasons[:80]:
            print(f"  offset={off} guid=0x{guid:016X}: {r}")
        if len(all_reasons) > 80:
            print(f"  ... and {len(all_reasons) - 80} more")

    print(f"\nwrote patched pak -> {pak_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
