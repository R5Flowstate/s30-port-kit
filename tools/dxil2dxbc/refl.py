"""Reflection parsers for dxc -dumpbin (DXIL, S30) and fxc /dumpbin (DXBC, S21).

layout(text) -> {cbuffer_name: {'size': n, 'leaves': [(path, offset, type, arraycount)]}}
bindings(text) -> [(name, type, format, dim, bind, count)]
"""
import kitpaths as KP
import re, subprocess, os, struct

K = KP.WINSDK_BIN
DXC = os.path.join(K, 'dxc.exe')
FXC = os.path.join(K, 'fxc.exe')

TYPESZ = {'float': 4, 'int': 4, 'uint': 4, 'bool': 4, 'half': 4, 'min16float': 4, 'min16int': 4, 'min16uint': 4,
          'dword': 4}


def dump(path):
    if open(path, 'rb').read().find(b'DXIL') >= 0:
        p = subprocess.run([DXC, '-dumpbin', path], capture_output=True, text=True, errors='replace')
        return p.stdout, True
    p = subprocess.run([FXC, '/nologo', '/dumpbin', path], capture_output=True, text=True, errors='replace')
    return p.stdout, False


def type_size(t):
    m = re.match(r'(?:row_major |column_major )?(\w+?)(\d)?(?:x(\d))?$', t)
    base, a, b = m.group(1), m.group(2), m.group(3)
    s = TYPESZ.get(base, 4)
    if a and b:
        return s * int(a) * int(b)
    if a:
        return s * int(a)
    return s


LEAF = re.compile(r'^\s*((?:row_major |column_major )?[\w]+)\s+([\w$]+)(\[(\d+)\])?;\s*;?\s*(?://)?\s*Offset:\s*(\d+)')
CLOSE = re.compile(r'^\s*\}\s*([\w$]+)(\[(\d+)\])?;\s*;?\s*(?://)?\s*Offset:\s*(\d+)(?:\s+Size:\s*(\d+))?')


def layout(text, dxil):
    lines = [l[1:] if dxil else l[2:] for l in text.splitlines() if l.startswith(';' if dxil else '//')]
    out = {}
    i = 0
    n = len(lines)
    while i < n:
        m = re.match(r'^\s*(cbuffer|Resource bind info for) (\w+)', lines[i])
        if not m:
            i += 1
            continue
        name = m.group(2)
        kind = m.group(1)
        depth = 0
        stack = []  # pending struct members: list of leaf lists
        cur = []
        i += 1
        leaves = []
        size = None
        # tokens inside braces
        pending = [[]]
        while i < n:
            l = lines[i]
            s = l.strip()
            if s == '{':
                depth += 1
                pending.append([])
            elif s.startswith('}') and depth == 1 and not CLOSE.match(l):
                break
            elif CLOSE.match(l):
                c = CLOSE.match(l)
                inner = pending.pop()
                depth -= 1
                mname = c.group(1)
                off = int(c.group(4))
                cnt = int(c.group(3)) if c.group(3) else 0
                if c.group(5):
                    size = int(c.group(5))
                pending[-1].append(('S', mname, off, inner, cnt, int(c.group(5)) if c.group(5) else None))
            else:
                m2 = LEAF.match(l)
                if m2:
                    pending[-1].append(('L', m2.group(2), int(m2.group(5)), m2.group(1),
                                        int(m2.group(4)) if m2.group(4) else 0))
            i += 1
        top = pending[-1] if pending else []
        # flatten. dxc offsets are absolute; fxc nested offsets are absolute as well (verified on FogUnion)
        flat = []

        def walk(items, prefix):
            for it in items:
                if it[0] == 'L':
                    flat.append((prefix + it[1], it[2], it[3], it[4]))
                else:
                    walk(it[3], prefix + it[1] + '.')
        walk(top, '')
        # dxc wraps cbuffer contents in a struct named after the cbuffer
        out[name] = {'kind': kind, 'size': size, 'leaves': flat}
    return out


def bindings(text, dxil):
    res = []
    lines = text.splitlines()
    for k, l in enumerate(lines):
        if 'Resource Bindings:' in l:
            for l2 in lines[k + 4:]:
                t = l2.lstrip(';/').strip()
                if not t:
                    break
                parts = t.split()
                if dxil:
                    # Name Type Format Dim ID Bind Count  (bind may be 't0,space3' and count 'unbounded')
                    nm = parts[0]
                    bind = parts[5] if len(parts) > 5 else ''
                    cnt = parts[6] if len(parts) > 6 else ('unbounded' if 'unbounded' in bind else '1')
                    res.append((nm, parts[1], parts[2], parts[3], bind.replace('unbounded', ''), cnt))
                else:
                    res.append((parts[0], parts[1], parts[2], parts[3], parts[4], parts[5] if len(parts) > 5 else '1'))
            break
    return res


def signature(text, dxil, which):
    lines = text.splitlines()
    hdr = ('Input signature:' if which == 'in' else 'Output signature:')
    for k, l in enumerate(lines):
        if hdr in l:
            sig = []
            for l2 in lines[k + 4:]:
                t = l2.lstrip(';/').strip()
                if not t:
                    break
                p = t.split()
                sig.append(tuple(p))
            return sig
    return []
