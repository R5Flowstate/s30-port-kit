"""Assemble S21-shaped shader MSWs from converted entries.

Layout (S21): dimensions [variant, instancing]; envOptScales[0..1] are the
cumulative strides of those dimensions, envOptScales[4] (PS) / [5] (VS) holds
the live entry count, and the debug half that follows is null entries.
  * variant  = the non-bindless S30 entries in order (classic, dither)
  * instancing present when the S30 entry is instanced (flag 0x40) or the S21
    twin carries it; the missing half is synthesized (static from instanced via
    CBufModelInstance, instanced PS from static via g_modelInst[SV_InstanceID])
  * a slot whose bytecode equals an earlier slot is an alias to it
  * debug slots set bit 54 on the feature word when bit 50 is set
Self-check: every shader with an S21 twin must reproduce the twin's features,
slot layout and flags.
"""
import kitpaths as KP
import os, sys, json, collections, glob


import msw
import pak_msw as P
import dxil2s21
import link_gate
OWNER = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 's21all_owner.json')))

EX = (KP.MAPS + r'\mp_rr_desertlands_mu5\extract\shader')
OUT = (KP.MAPS + r'\mp_rr_desertlands_mu5\extract\shader_s21')
S21_PAKS = glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak'))
INST = 0x40


def dbg(w):
    return w | (1 << 54) if w & (1 << 50) else w


_TWINS = None


def s21_twin(g):
    global _TWINS
    if _TWINS is None:
        import pickle
        _TWINS = pickle.load(open('twins.pkl', 'rb'))
    return _TWINS.get(g)


_cache = {}


def variant(blob, mode):
    key = (blob, mode)
    tag = os.path.basename(blob)[:-5] + {'static': '_static', 'inst_ps': '_instps'}[mode]
    cached = os.path.join(dxil2s21.WORK, tag + '.s21.dxbc')
    if key not in _cache and os.path.exists(cached) and os.path.getmtime(cached) > os.path.getmtime(dxil2s21.__file__):
        _cache[key] = open(cached, 'rb').read()
    if key not in _cache:
        r = dxil2s21.convert(blob, static=(mode == 'static'), inst_ps=(mode == 'inst_ps'))
        _cache[key] = open(r['dxbc'], 'rb').read() if r.get('ok') else None
    return _cache[key]


def build(gname, m, conv, tw):
    live = []
    for i, e in enumerate(m.entries):
        r = conv.get('%s_%d' % (gname, i))
        if r and not ((e.flags[0] | e.flags[1]) & (0x07 << 56)):
            live.append((i, r, e.flags))
    if not live:
        return None, 'no convertible entry'
    if len(live) > 2:
        return None, 'live %d' % len(live)
    kind = live[0][1]['kind']
    nvar = len(live)
    fw = 0 if kind == 0 else 1            # feature-flag word: PS word0, VS word1
    s30_inst = any(fl[0] & INST for _, _, fl in live)
    twin_inst = bool(tw and tw['features'][2])
    has_inst = s30_inst or twin_inst
    slots = []                            # (buf, flags)
    for inst in ((0, 1) if has_inst else (0,)):
        for d0, (i, r, fl) in enumerate(live):
            blob = 'blobs/%s_%d.dxbc' % (gname, i)
            own = open(r['dxbc'], 'rb').read()
            if not has_inst:
                slots.append((own, fl))
            elif inst == 0:
                if s30_inst:
                    b = variant(blob, 'static')
                    if b is None:
                        if carries_instance(own, kind):
                            return None, 'static entry %d cannot drop the instance id' % i
                        b = own
                    sfl = (fl[0] & ~INST, fl[1] & ~INST) if kind == 1 else (fl[0] & ~INST, fl[1])
                    slots.append((b, sfl))
                elif carries_instance(own, kind):
                    b = variant(blob, 'static')
                    if b is None:
                        return None, 'static entry %d cannot drop the instance stream' % i
                    slots.append((b, fl))
                else:
                    slots.append((own, fl))
            else:
                if s30_inst:
                    slots.append((own, fl))
                else:
                    b = variant(blob, 'inst_ps') if kind == 0 else None
                    ifl = (fl[0] | INST, fl[1]) if kind == 0 else (fl[0] | INST, fl[1] | INST)
                    slots.append((b if b is not None else own, ifl))
    entries = []
    for k, (b, fl) in enumerate(slots):
        ref = next((j for j in range(k) if slots[j][0] == b), None)
        if ref is not None:
            entries.append({'buf': None, 'ref': ref, 'flags': slots[ref][1]})
        else:
            entries.append({'buf': b, 'ref': 0xFFFF, 'flags': fl})
    n = len(entries)
    for e in list(entries):
        fl = list(e['flags'])
        fl[fw] = dbg(fl[fw])
        entries.append({'buf': None, 'ref': 0xFFFF, 'flags': tuple(fl)})
    sc = [0] * 6
    stride = 1
    if nvar == 2:
        sc[0] = 1
        stride = 2
    if has_inst:
        sc[1] = stride
    sc[4 if kind == 0 else 5] = n
    feats = bytes([0xFF] + sc)
    return {'guid': int(gname, 16), 'type': kind, 'features': feats, 'name': m.name or None, 'entries': entries}, None


def carries_instance(b, kind):
    names = {(n, i) for fcc in (b'ISGN', b'OSGN') for n, i, sv, r, m in link_gate.sig(b, fcc)}
    return ('SV_INSTANCEID', 0) in names or (kind == 1 and ('COLOR', 1) in names)


def s21_original(g):
    for fn, pak in OWNER.items():
        if os.path.basename(fn).upper().startswith('0X%016X_' % g):
            s = P.read_shader(os.path.join((KP.WORK + r'\paks_dec'), pak), g)
            if s and s['type'] <= 5:
                return {'guid': g, 'type': s['type'], 'features': bytes(s['features']), 'name': s['name'],
                        'entries': [{'buf': e['buf'], 'ref': e['ref'], 'flags': tuple(e['flags'])} for e in s['entries']]}
    return None


def layout_str(entries, half):
    out = []
    for k, e in enumerate(entries):
        if e['buf']:
            out.append('B')
        elif e['ref'] != 0xFFFF and k < half:
            out.append('A')
        else:
            out.append('N')
    return ''.join(out)


def _job(j):
    dxil2s21.convert(j[0], static=(j[1] == 'static'), inst_ps=(j[1] == 'inst_ps'))


def prefetch(conv):
    from concurrent.futures import ProcessPoolExecutor
    jobs = []
    for n, r in conv.items():
        jobs.append(('blobs/%s.dxbc' % n, 'static'))
        tw = s21_twin(int(n.split('_')[0], 16))
        if r['kind'] == 0 and tw and tw['features'][2]:
            jobs.append(('blobs/%s.dxbc' % n, 'inst_ps'))
    with ProcessPoolExecutor(12) as ex:
        list(ex.map(_job, jobs, chunksize=4))
    print('prefetched', len(jobs))


def main():
    res = json.load(open('run_all.json'))
    conv = {r['n']: r for r in res if r.get('ok') and not r.get('static')}
    prefetch(conv)
    os.makedirs(OUT, exist_ok=True)
    report = collections.Counter()
    problems = []
    for f in sorted(glob.glob(os.path.join(EX, '*.msw'))):
        gname = os.path.basename(f)[:-4]
        g = int(gname, 16)
        m = msw.read_msw(f)
        tw = s21_twin(g)
        sh, why = build(gname, m, conv, tw)
        if sh is None:
            report['fail'] += 1
            problems.append((gname, why))
            continue
        if tw:
            report['twin'] += 1
            half = len(tw['entries']) // 2
            a = (bytes(tw['features']).hex(), layout_str(tw['entries'], half), [tuple(e['flags']) for e in tw['entries']])
            b = (sh['features'].hex(), layout_str(sh['entries'], len(sh['entries']) // 2), [tuple(e['flags']) for e in sh['entries']])
            if a == b:
                report['twin_exact'] += 1
            else:
                report['twin_mismatch'] += 1
                problems.append((gname, 'twin', a[0], b[0], a[1], b[1], a[2] == b[2]))
        P.write_shader_msw(sh, os.path.join(OUT, gname + '.msw'))
        report['written'] += 1
        report['kind%d_%s' % (sh['type'], sh['features'].hex())] += 1
    print(json.dumps(report, indent=0))
    for p in problems[:40]:
        print(p)
    json.dump({'report': report, 'problems': problems}, open('build_msw.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
