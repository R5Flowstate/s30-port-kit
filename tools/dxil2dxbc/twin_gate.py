"""Compare a converted DXBC against its retail S21 twin.

Checks: input/output signature rows, resource bind slots, and the set of
(cbuffer slot, register, component) the shader reads.
"""
import re, sys, subprocess
import refl


def disasm(p):
    t, _ = refl.dump(p)
    return t


def sig(t, which):
    return [tuple(r[:6]) for r in refl.signature(t, False, which)]


def binds(t):
    out = set()
    for b in refl.bindings(t, False):
        out.add((b[1].replace('sampler_c', 'sampler'), b[4]))
    return out


def cbreads(t):
    body = t[t.find('\nps_') if '\nps_' in t else t.find('\nvs_'):]
    s = set()
    for m in re.finditer(r'\bcb(\d+)\[(\d+)\]\.([xyzw]+)', body):
        for c in m.group(3):
            s.add((int(m.group(1)), int(m.group(2)), c))
    dyn = set(int(m.group(1)) for m in re.finditer(r'\bcb(\d+)\[r\d', body))
    return s, dyn


def compare(ours, twin):
    a, b = disasm(ours), disasm(twin)
    r = {}
    r['in'] = sig(a, 'in') == sig(b, 'in')
    r['out'] = sig(a, 'out') == sig(b, 'out')
    ba, bb = binds(a), binds(b)
    r['binds_extra'] = sorted(ba - bb)
    r['binds_missing'] = sorted(bb - ba)
    ca, da = cbreads(a)
    cb, db = cbreads(b)
    r['cb_extra'] = sorted(ca - cb)
    r['cb_missing'] = sorted(cb - ca)
    r['ninstr'] = (a.count('\n  '), b.count('\n  '))
    if not r['in']:
        r['in_rows'] = (sig(a, 'in'), sig(b, 'in'))
    if not r['out']:
        r['out_rows'] = (sig(a, 'out'), sig(b, 'out'))
    return r


if __name__ == '__main__':
    import json
    print(json.dumps(compare(sys.argv[1], sys.argv[2]), indent=1))
