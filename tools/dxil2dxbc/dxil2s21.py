"""S30 DXIL shader entry -> S21 DXBC (SM5) with the S21 engine ABI.

Pipeline per blob:
  dxil-spirv (--ssbo-srv) -> SPIR-V -> SPIRV-Cross HLSL (SM6 text)
  -> ABI rewrite (cbuffer layouts by member name, structured buffers, samplers,
     cubemap cb6 -> t72, S30-only features pinned off, quad ops emulated,
     signature semantics restored)
  -> fxc /O3 vs_5_0 / ps_5_0.

convert(path, variant=None) -> dict(ok, hlsl, dxbc, report)
"""
import os, re, json, struct, subprocess, hashlib, collections

import refl
import linexpr

HERE = os.path.dirname(os.path.abspath(__file__))
DXSPV = os.path.join(HERE, r'dxil-spirv\build\Release\dxil-spirv.exe')
SPVX = os.path.join(HERE, r'dxil-spirv\third_party\SPIRV-Cross\build\Release\spirv-cross.exe')
FXC = refl.FXC
WORK = os.path.join(HERE, 'work')
os.makedirs(WORK, exist_ok=True)

LUM = 'float3(0.2125999927520751953125f, 0.715200006961822509765625f, 0.072200000286102294921875f)'

# --- S21 contract -----------------------------------------------------------
_S21 = None


def s21():
    global _S21
    if _S21 is None:
        c = json.load(open(os.path.join(HERE, 'census_s21.json')))
        _S21 = c['cb']
    return _S21


S21_CB = {  # S21 name -> (register, float4 count)
    'CBufCommonPerCamera': ('b3', 55),
    'CBufUberStatic': ('b0', 38),
    'CBufUberDynamic': ('b1', 5),
    'CBufModelInstance': ('b2', 14),
}
CB_RENAME = {  # S30 cbuffer -> S21 cbuffer
    'CBufCommonPerCamera': 'CBufCommonPerCamera',
    'CBufUberStaticSingular': 'CBufUberStatic',
    'CBufUberDynamicSingular': 'CBufUberDynamic',
    'CBufModelInstance': 'CBufModelInstance',
}
# S30 leaf -> S21 (cbuffer, leaf) where the name or home differs
LEAF_ALIAS = {
    ('CBufCommonPerCamera', 'c_overrideExposureCounteractionRate'): ('CBufCommonPerCamera', 'c_overrideExposureInteractionRate'),
    # mode 1 = indirect diffuse split into its own target, as S21 does when the flag is set;
    # mode 2 (inline SSAO texture) has no S21 counterpart and is never selected
    ('CBufCommonPerCamera', 'c_ssaoMode'): ('CBufCommonPerCamera', 'c_separateIndirectDiffuse'),
    ('CBufCommonPerCamera', 'c_dof.tNear'): ('CBufUberDynamic', 'c_dof.tNear'),
    ('CBufCommonPerCamera', 'c_dof.tFar'): ('CBufUberDynamic', 'c_dof.tFar'),
    ('CBufCommonPerCamera', 'c_dof.sourceNearScaleBias'): ('CBufUberDynamic', 'c_dof.sourceNearScaleBias'),
    ('CBufCommonPerCamera', 'c_dof.targetNearScaleBias'): ('CBufUberDynamic', 'c_dof.targetNearScaleBias'),
    ('CBufCommonPerCamera', 'c_dof.sourceFarScaleBias'): ('CBufUberDynamic', 'c_dof.sourceFarScaleBias'),
    ('CBufCommonPerCamera', 'c_dof.targetFarScaleBias'): ('CBufUberDynamic', 'c_dof.targetFarScaleBias'),
    ('CBufCommonPerCamera', 'c_dof.unused'): ('CBufUberDynamic', 'c_dof.unused'),
}
# S30-only fields pinned to the value that selects the path S21 itself runs.
# Values are HLSL float-bit expressions.
F0 = '0.0f'
PIN = {
    ('CBufCommonPerCamera', 'c_tuaaMipMapBias'): F0,
    ('CBufCommonPerCamera', 'c_pad0'): F0,
    ('CBufCommonPerCamera', 'c_padding0'): F0,
    ('CBufCommonPerCamera', 'c_ssaoUvScale'): '1.0f',
    ('CBufCommonPerCamera', 'c_pbrFalloffBiasSq'): '1.0f',
    ('CBufCommonPerCamera', 'c_debugForceValueAlbedo'): F0,
    ('CBufCommonPerCamera', 'c_debugForceValueGloss'): F0,
    ('CBufCommonPerCamera', 'c_debugForceValueSpecColor'): F0,
    ('CBufCommonPerCamera', 'c_debugForceValueAlpha'): F0,
    ('CBufCommonPerCamera', 'c_debugForceValueAO'): F0,
    ('CBufCommonPerCamera', 'c_debugForceValueCavity'): F0,
    ('CBufCommonPerCamera', 'c_irrVolLookupParams.boundsMin'): F0,
    ('CBufCommonPerCamera', 'c_irrVolLookupParams.irrVolMode'): F0,
    ('CBufCommonPerCamera', 'c_irrVolLookupParams.rcpCellSize'): F0,
    ('CBufCommonPerCamera', 'c_irrVolLookupParams.bindlessOffset'): F0,
    ('CBufCommonPerCamera', 'c_irrVolLookupParams.dims'): F0,
    ('CBufCommonPerCamera', 'c_irrVolLookupParams._pad2'): F0,
    ('CBufUberStaticSingular', 'c_exposureCounteractionType'): F0,
    ('CBufUberStaticSingular', 'c_uvUseModelAnimUVTimeFlags'): F0,
    ('CBufUberDynamicSingular', 'c_exposureCounteractionRate'): F0,
    ('CBufUberDynamicSingular', 'c_bindlessOffsets'): F0,
    ('CBufModelInstance', 'c_modelInst.useIrradianceVolume'): F0,
}
# Fields whose S21 source is an expression of other S21 state.
DERIVED = {}

# S30 cubemapState packs primary<<21 | secondary<<10 (2047 = none) | blend weight;
# S21 cubemapID is the plain primary index.
CUBESTATE_FROM_ID = '((%s << 21u) | 2096128u)'

S30_ONLY_RES = {'ssaoTexture', 'exposureTexture'}
BINDLESS_ENTRY = {'Bindless2d', 'BindlessConstants', 'BindlessVertex', 'PerInstanceBufferArray',
                  'CBufUberDynamicArray', 'Bindless2dArray', 'CBufSpriteCardDynamicArray', 'CBufSpriteCardArray'}
SAMPLER_REG = {'shadowmapSampler': 's0'}


def tsize(t, cnt, packed):
    """Byte span of a leaf. Arrays in a cbuffer stride 16 per element."""
    base = refl.type_size(t)
    if cnt:
        if packed:
            return base * cnt
        return 16 * (cnt - 1) + base if base <= 16 else base * cnt
    return base


def find_leaf(leaves, byte, packed):
    for path, off, t, cnt in leaves:
        sz = tsize(t, cnt, packed)
        if off <= byte < off + sz:
            return path, byte - off, t, cnt
    return None


def s21_leaf(cb, path):
    v = s21().get(cb, {}).get(path)
    return v


def s21_access(cb, byte):
    reg, comp = divmod(byte, 16)
    return '%s_m0[%du].%s' % (cb, reg, 'xyzw'[comp // 4])


class Ctx:
    def __init__(self):
        self.report = collections.Counter()
        self.unmapped = collections.Counter()
        self.notes = []


def map_cb_component(ctx, cb30, leaves30, byte):
    """One 4-byte component of an S30 cbuffer -> HLSL float expression in S21 terms."""
    hit = find_leaf(leaves30, byte, False)
    if hit is None:
        ctx.report['cb_pad_read'] += 1
        return F0
    path, intra, t, cnt = hit
    key = (cb30, path)
    if key in PIN:
        ctx.report['pin:' + path] += 1
        return PIN[key]
    if key in DERIVED:
        ctx.report['derived:' + path] += 1
        return DERIVED[key](intra)
    if key == ('CBufModelInstance', 'c_modelInst.cubemapState'):
        v = s21_leaf('CBufModelInstance', 'c_modelInst.cubemapID')
        if v is None:
            ctx.unmapped[key] += 1
            return None
        ctx.report['cubestate'] += 1
        return 'asfloat%s' % ('(' + CUBESTATE_FROM_ID % ('asuint(%s)' % s21_access('CBufModelInstance', v[0])) + ')')
    tcb, tpath = LEAF_ALIAS.get(key, (CB_RENAME.get(cb30), path))
    if tcb is None:
        ctx.unmapped[(cb30, path)] += 1
        return None
    v = s21_leaf(tcb, tpath)
    if v is None:
        ctx.unmapped[(cb30, path)] += 1
        return None
    off21 = v[0]
    if cnt:
        # same element stride in both (16 per element in a cbuffer)
        pass
    return s21_access(tcb, off21 + intra)


SWZ = 'xyzw'


def find_matching(s, i, open_c='[', close_c=']'):
    depth = 0
    for j in range(i, len(s)):
        if s[j] == open_c:
            depth += 1
        elif s[j] == close_c:
            depth -= 1
            if depth == 0:
                return j
    return -1


def rewrite_cb_access(ctx, text, cb30, leaves30, dyn_fn):
    """Rewrite every `<cb30>_m0[idx](.swz)?` access."""
    tag = cb30 + '_m0['
    out = []
    i = 0
    while True:
        j = text.find(tag, i)
        if j < 0:
            out.append(text[i:])
            break
        # identifier boundary
        if j > 0 and (text[j - 1].isalnum() or text[j - 1] == '_'):
            out.append(text[i:j + len(tag)])
            i = j + len(tag)
            continue
        out.append(text[i:j])
        k = find_matching(text, j + len(tag) - 1)
        idx = text[j + len(tag):k].strip()
        end = k + 1
        m = re.match(r'\.([xyzw]{1,4})\b', text[end:])
        swz = m.group(1) if m else 'xyzw'
        if m:
            end += m.end()
        mi = re.fullmatch(r'(\d+)u?', idx)
        if mi and dyn_fn is None:
            row = int(mi.group(1))
            comps = []
            for c in swz:
                e = map_cb_component(ctx, cb30, leaves30, row * 16 + 4 * SWZ.index(c))
                comps.append(e)
            if any(c is None for c in comps):
                out.append('__UNMAPPED__')
            else:
                out.append(build_vec(comps))
            ctx.report['cb_static'] += 1
        else:
            if dyn_fn is None:
                ctx.unmapped[(cb30, '<dynamic>')] += 1
                out.append('__UNMAPPED__')
            else:
                out.append('%s(%s)%s' % (dyn_fn, idx, ('.' + swz) if m else ''))
                ctx.report['cb_dynamic'] += 1
        i = end
    return ''.join(out)


def build_vec(comps):
    if len(comps) == 1:
        return comps[0]
    # merge contiguous components of the same register
    regs = [re.fullmatch(r'(\w+_m0\[\d+u\])\.([xyzw])', c) for c in comps]
    if all(regs) and len(set(r.group(1) for r in regs)) == 1:
        return '%s.%s' % (regs[0].group(1), ''.join(r.group(2) for r in regs))
    return 'float%d(%s)' % (len(comps), ', '.join(comps))


# --- structured buffers -------------------------------------------------------
def struct_word_map(name30, leaves30, name21):
    """S30 word offset -> S21 word offset (or PIN expr) inside one element."""
    l21 = s21().get(name21, {})
    m = {}
    for path, off, t, cnt in leaves30:
        sz = tsize(t, cnt, True)
        short = path.split('.', 1)[1] if path.startswith('$Element.') else path
        p21 = path if path == '$Element' else '$Element.' + short
        alias = {'cubemapState': 'cubemapID'}
        if p21 not in l21 and short in alias:
            p21 = '$Element.' + alias[short]
            if p21 in l21:
                m[off // 4] = ('cubestate', l21[p21][0] // 4)
                continue
        for w in range(sz // 4):
            if p21 in l21:
                m[off // 4 + w] = l21[p21][0] // 4 + w
            else:
                m[off // 4 + w] = ('pin', short)
    return m


STRUCT_PIN = {
    'useIrradianceVolume': '0u',
}


def rewrite_structured(ctx, text, defs, bufname, stride_words, wmap):
    """Rewrite `<buf>.LoadN(byteaddr)` on ByteAddressBuffer to StructuredBuffer element reads."""
    pat = re.compile(r'\b' + re.escape(bufname) + r'(?:_\d+)?\.Load([234]?)\(')
    out = []
    i = 0
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            break
        out.append(text[i:m.start()])
        n = int(m.group(1) or 1)
        k = find_matching(text, m.end() - 1, '(', ')')
        arg = text[m.end():k]
        i = k + 1
        lf = linexpr.lin(arg, defs)
        words = None
        if lf is not None:
            terms, c = lf
            if c % 4 == 0 and all(v % (stride_words * 4) == 0 for v in terms.values()):
                elem_terms = {a: v // (stride_words * 4) for a, v in terms.items()}
                e_add, w0 = divmod(c // 4, stride_words)
                if w0 + n <= stride_words:
                    idx = linexpr.render(elem_terms)
                    if e_add:
                        idx = '(%s + %du)' % (idx, e_add)
                    words = []
                    for q in range(n):
                        tgt = wmap.get(w0 + q)
                        if isinstance(tgt, tuple) and tgt[0] == 'cubestate':
                            ctx.report['cubestate'] += 1
                            words.append(CUBESTATE_FROM_ID % ('%s[%s].w[%d]' % (bufname, idx, tgt[1])))
                        elif isinstance(tgt, tuple):
                            pv = STRUCT_PIN.get(tgt[1])
                            if pv is None:
                                ctx.unmapped[(bufname, tgt[1])] += 1
                                words.append('__UNMAPPED__')
                            else:
                                ctx.report['spin:' + tgt[1]] += 1
                                words.append(pv)
                        elif tgt is None:
                            words.append('0u')
                        else:
                            words.append('%s[%s].w[%d]' % (bufname, idx, tgt))
                    ctx.report['sb_static'] += 1
        if words is None:
            ctx.report['sb_dynamic'] += 1
            words = ['%s_dyn((%s) >> 2u, %d)' % (bufname, arg, q) for q in range(n)]
        out.append(words[0] if n == 1 else 'uint%d(%s)' % (n, ', '.join(words)))
    return ''.join(out)


def structured_dyn_fn(bufname, stride_words, wmap):
    """Runtime fallback: word address -> S21 word, with remapped / pinned fields."""
    cases = []
    for w30 in range(stride_words):
        tgt = wmap.get(w30)
        if isinstance(tgt, tuple) and tgt[0] == 'cubestate':
            cases.append('    if (o == %du) return %s;' % (w30, CUBESTATE_FROM_ID % ('%s[e].w[%du]' % (bufname, tgt[1]))))
        elif isinstance(tgt, tuple):
            cases.append('    if (o == %du) return %s;' % (w30, STRUCT_PIN.get(tgt[1], '0u')))
        elif tgt is not None and tgt != w30:
            cases.append('    if (o == %du) return %s[e].w[%du];' % (w30, bufname, tgt))
    return ('uint %s_dyn(uint wa, uint q)\n{\n    uint w = wa + q;\n    uint e = w / %du;\n    uint o = w - e * %du;\n'
            '%s\n    return %s[e].w[o];\n}\n') % (bufname, stride_words, stride_words, '\n'.join(cases), bufname)


# --- main --------------------------------------------------------------------
def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, errors='replace')
    return p.returncode, p.stdout + p.stderr


PRELUDE = r'''
#define NonUniformResourceIndex(x) (x)
#define S21_QPX ((((uint)gl_FragCoord.x) & 1u) != 0u)
#define S21_QPY ((((uint)gl_FragCoord.y) & 1u) != 0u)
#define QuadReadAcrossX(v) (S21_QPX ? (v) - ddx_fine(v) : (v) + ddx_fine(v))
#define QuadReadAcrossY(v) (S21_QPY ? (v) - ddy_fine(v) : (v) + ddy_fine(v))
#define QuadReadAcrossDiagonal(v) QuadReadAcrossY(QuadReadAcrossX(v))
'''


def sig_name(var):
    m = re.fullmatch(r'(.*?)(?:_(\d+))?', var)
    return m.group(1), int(m.group(2) or 0)


def fix_signatures(ctx, text, sig_in, sig_out):
    """Restore DXIL semantic names on the SPIRV-Cross I/O structs."""
    def fix_struct(text, sname, sig):
        m = re.search(r'struct ' + sname + r'\n\{\n(.*?)\n\};', text, re.S)
        if not m:
            return text
        # SPIRV-Cross emits TEXCOORD<location>; dxil-spirv's location is the DXIL signature register
        byreg = collections.defaultdict(list)
        for e in sig:
            if e[4] in ('NONE', 'TARGET') and not e[0].upper().startswith('SV_TARGET'):
                byreg[int(e[3])].append(e)
        lines = m.group(1).split('\n')
        new = []
        taken = set()
        for l in lines:
            mm = re.match(r'(\s*(?:\w+ )*)(\w+) (\w+) : (\w+);', l)
            if not mm:
                new.append(l)
                continue
            pre, ty, var, sem = mm.groups()
            ml = re.fullmatch(r'TEXCOORD(\d+)', sem)
            if not ml:
                new.append(l)
                continue
            cands = [e for e in byreg.get(int(ml.group(1)), []) if (e[0], e[1]) not in taken]
            if len(cands) > 1:
                nm, ix = sig_name(var)
                pick = [e for e in cands if e[0] == nm and int(e[1]) == ix] or cands[:1]
            else:
                pick = cands
            if not pick:
                ctx.notes.append('sig: no DXIL element for %s' % var)
                new.append(l)
                continue
            e = pick[0]
            taken.add((e[0], e[1]))
            new.append('%s%s %s : %s%s;' % (pre, ty, var, e[0], e[1]))
        # fxc packs elements in declaration order, so declare them in DXIL (register, component) order
        pos = {}
        sysgen = {'VERTID', 'INSTID', 'FFACE', 'PRIMID', 'SAMPLE', 'COVERAGE'}
        for e in sig:
            reg = int(e[3]) if e[3].isdigit() else 999
            if e[4].upper() in sysgen or not e[3].isdigit():
                reg += 2000
            pos[(e[0].upper(), int(e[1]))] = (reg, 'xyzw'.index(e[2][0]) if e[2][0] in 'xyzw' else 0)

        def key(item):
            k, l = item
            mm = re.match(r'\s*(?:\w+ )*\w+ \w+ : ([A-Za-z_]+?)(\d*);', l)
            if not mm:
                return (1000, 0, k)
            p = pos.get((mm.group(1).upper(), int(mm.group(2) or 0)))
            return (p[0], p[1], k) if p else (1000, 0, k)
        new = [l for k, l in sorted(enumerate(new), key=key)]
        return text[:m.start(1)] + '\n'.join(new) + text[m.end(1):]
    text = fix_struct(text, 'SPIRV_Cross_Input', sig_in)
    text = fix_struct(text, 'SPIRV_Cross_Output', sig_out)
    return text


def kill_resource_calls(ctx, text, res, value):
    """Replace `<res>[...].Method(...)` / `<res>.Method(...)` with a constant."""
    out = []
    i = 0
    pat = re.compile(r'\b' + re.escape(res) + r'\b')
    while True:
        m = pat.search(text, i)
        if not m:
            out.append(text[i:])
            break
        j = m.end()
        if text.startswith('[', j):
            j = find_matching(text, j) + 1
        mm = re.match(r'\.(\w+)\(', text[j:])
        if not mm:
            out.append(text[i:m.end()])
            i = m.end()
            continue
        k = find_matching(text, j + mm.end() - 1, '(', ')')
        out.append(text[i:m.start()])
        out.append(value)
        ctx.report['kill:' + res] += 1
        i = k + 1
    return ''.join(out)


def drop_decl(text, name):
    return re.sub(r'^[^\n;{}]*\b' + re.escape(name) + r'\b(\[\])?\s*:\s*register\([^)]*\);\n', '', text, flags=re.M)


def to_static(ctx, text):
    """Instanced VS -> the S21 static entry: the instance record comes from CBufModelInstance
    (same 224-byte layout) and SV_InstanceID is no longer passed downstream."""
    out = []
    i = 0
    tag = 'g_modelInst['
    while True:
        j = text.find(tag, i)
        if j < 0:
            out.append(text[i:])
            break
        if j > 0 and (text[j - 1].isalnum() or text[j - 1] == '_'):
            out.append(text[i:j + len(tag)])
            i = j + len(tag)
            continue
        k = find_matching(text, j + len(tag) - 1)
        m = re.match(r'\.w\[', text[k + 1:])
        if not m:
            out.append(text[i:k + 1])
            i = k + 1
            continue
        k2 = find_matching(text, k + 1 + m.end() - 1)
        word = text[k + 1 + m.end():k2]
        out.append(text[i:j])
        if re.fullmatch(r'\d+', word):
            w = int(word)
            out.append('asuint(CBufModelInstance_m0[%du].%s)' % (w // 4, 'xyzw'[w % 4]))
        else:
            out.append('asuint(CBufModelInstance_m0[(%s) >> 2u][(%s) & 3u])' % (word, word))
        ctx.report['static_rows'] += 1
        i = k2 + 1
    text = ''.join(out)
    text = re.sub(r'struct S21_g_modelInst \{[^}]*\};\nStructuredBuffer<S21_g_modelInst> g_modelInst : register\(t\d+\);\n', '', text)
    return text


def to_instanced_ps(ctx, text):
    """CBufModelInstance PS -> the S21 instanced PS entry: the record comes from
    g_modelInst[SV_InstanceID], with SV_InstanceID read just before SV_Position."""
    n = [0]

    def rep(m):
        n[0] += 1
        row, comps = int(m.group(1)), m.group(2) or 'xyzw'
        ws = ['g_modelInst[S21_InstanceID].w[%d]' % (row * 4 + 'xyzw'.index(c)) for c in comps]
        return 'asfloat(%s)' % (ws[0] if len(ws) == 1 else 'uint%d(%s)' % (len(ws), ', '.join(ws)))
    text = re.sub(r'\bCBufModelInstance_m0\[(\d+)u\](?:\.([xyzw]{1,4})\b)?', rep, text)
    if re.search(r'\bCBufModelInstance_m0\[', text):
        ctx.unmapped[('CBufModelInstance', '<dynamic-inst>')] += 1
    ctx.report['inst_ps_rows'] += n[0]
    mi = re.search(r'struct SPIRV_Cross_Input\n\{\n(.*?)\n\};', text, re.S)
    body = mi.group(1)
    mp = re.search(r'\n?(\s*)float4 gl_FragCoord : SV_Position;', body)
    line = '    nointerpolation uint S21_InstanceID : SV_InstanceID;'
    if mp:
        body = body[:mp.start()] + '\n' + line + body[mp.start():]
    else:
        body = body + '\n' + line
    text = text[:mi.start(1)] + body + text[mi.end(1):]
    text = text.replace('\nstatic float4 gl_FragCoord;', '\nstatic float4 gl_FragCoord;\nstatic uint S21_InstanceID;', 1)
    if 'static uint S21_InstanceID;' not in text:
        text = text.replace('\nstruct SPIRV_Cross_Input', '\nstatic uint S21_InstanceID;\nstruct SPIRV_Cross_Input', 1)
    text = re.sub(r'(\n(\s*)\w+ = stage_input\.\w+;)', r'\n\2S21_InstanceID = stage_input.S21_InstanceID;\1', text, count=1)
    return text


def static_signature(ctx, text):
    """Drop the SV_InstanceID pass-through output, then every input left unread."""
    mo = re.search(r'struct SPIRV_Cross_Output\n\{\n(.*?)\n\};', text, re.S)
    if mo:
        body = mo.group(1)
        mm = re.search(r'\n?\s*(?:\w+ )*uint (\w+) : SV_InstanceID\d*;', body)
        if mm:
            var = mm.group(1)
            text = text[:mo.start(1)] + body[:mm.start()] + body[mm.end():] + text[mo.end(1):]
            text = re.sub(r'\n\s*stage_output\.' + var + r' = ' + var + r';', '', text)
            text = re.sub(r'\n\s*' + var + r' = [^;]*;', '', text)
            ctx.report['static_drop_out'] += 1
    while True:
        uses = collections.Counter(re.findall(r'\b_\d+\b', text))
        dead = [m for m in re.finditer(r'\n[ \t]*(?:precise )?(?:u?int|float|bool)\d? (_\d+) = [^;]*;', text)
                if uses[m.group(1)] == 1]
        if not dead:
            break
        for m in reversed(dead):
            text = text[:m.start()] + text[m.end():]
    mi = re.search(r'struct SPIRV_Cross_Input\n\{\n(.*?)\n\};', text, re.S)
    if mi:
        keep = []
        for l in mi.group(1).split('\n'):
            m = re.match(r'\s*(?:\w+ )*\w+ (\w+) : (\w+);', l)
            if m:
                var = m.group(1)
                rest = re.sub(r'\n\s*' + var + r' = stage_input\.' + var + r';', '', text)
                uses = len(re.findall(r'\b' + var + r'\b', rest)) - 1  # minus the struct member itself
                uses -= len(re.findall(r'\bstatic [\w<>]+ ' + var + r';', rest))
                if uses <= 0 and (not m.group(2).upper().startswith('SV_') or m.group(2).upper().startswith('SV_INSTANCEID')):
                    text = re.sub(r'\n\s*' + var + r' = stage_input\.' + var + r';', '', text)
                    text = re.sub(r'\nstatic [\w<>]+ ' + var + r';', '', text)
                    ctx.report['static_drop_in'] += 1
                    continue
            keep.append(l)
        mi = re.search(r'struct SPIRV_Cross_Input\n\{\n(.*?)\n\};', text, re.S)
        text = text[:mi.start(1)] + '\n'.join(keep) + text[mi.end(1):]
    return text


def convert(path, tag=None, static=False, inst_ps=False):
    tag = (tag or os.path.basename(path)[:-5]) + ('_static' if static else '') + ('_instps' if inst_ps else '')
    ctx = Ctx()
    blob = open(path, 'rb').read()
    dump, dx = refl.dump(path)
    L30 = refl.layout(dump, True)
    B30 = refl.bindings(dump, True)
    si = refl.signature(dump, True, 'in')
    so = refl.signature(dump, True, 'out')
    kind = struct.unpack_from('<I', chunk(blob, 'DXIL'), 0)[0] >> 16
    names = {b[0] for b in B30}
    if names & BINDLESS_ENTRY:
        return {'ok': False, 'skip': 'bindless', 'kind': kind}
    spv = os.path.join(WORK, tag + '.spv')
    hl0 = os.path.join(WORK, tag + '.s30.hlsl')
    rc, log = run([DXSPV, path, '--output', spv, '--use-reflection-names', '--ssbo-srv'])
    if rc:
        return {'ok': False, 'err': 'dxil-spirv: ' + log[-400:]}
    rc, log = run([SPVX, spv, '--hlsl', '--shader-model', '60', '--output', hl0])
    if rc:
        return {'ok': False, 'err': 'spirv-cross: ' + log[-400:]}
    text = open(hl0).read()

    # leaves with the dxc wrapper struct stripped, S30 names
    def leaves(cb):
        out = []
        for p, o, t, c in L30[cb]['leaves']:
            parts = p.split('.')
            if parts[0] == cb:
                parts = parts[1:]
            if parts and parts[0] in ('c_staticConstants', 'c_dynamicConstants'):
                parts = parts[1:]
            out.append(('.'.join(parts), o, t, c))
        return out

    text = re.sub(r', space0\)', ')', text)
    # SSA uint definitions for the address resolver
    defs = dict(re.findall(r'\buint (_\d+) = ([^;]+);', text))

    extra_fns = []
    text = re.sub(r'cbuffer (\w+)UBO : register\(b\d+\)\n\{\n    float4 \w+_m0\[\d+\] : packoffset\(c0\);\n\};\n\n?', '', text)
    # cubemap: S30 cb6 CubemapSample_s -> S21 g_cubemapSamples t72
    if 'CubemapSample_s' in L30:
        text = rewrite_cb_access(ctx, text, 'CubemapSample_s', leaves('CubemapSample_s'), 'S21_CubemapRow')
        extra_fns.append(CUBEMAP_FN)
    for cb30 in list(L30):
        if L30[cb30]['kind'] != 'cbuffer' or cb30 == 'CubemapSample_s':
            continue
        if cb30 not in CB_RENAME:
            ctx.unmapped[(cb30, '<cbuffer>')] += 1
            continue
        text = rewrite_cb_access(ctx, text, cb30, leaves(cb30), None)

    # structured buffers
    for name in ('g_modelInst', 's_globalLights', 'g_gpuBones', 'g_foliageInstances', 'g_foliageInstancesPrevFrame',
                 'g_boneTransforms', 'g_boneTransformsPrevFrame'):
        if name not in L30 or not re.search(r'ByteAddressBuffer ' + name + r'\b', text):
            continue
        lv = L30[name]
        size = max(o + tsize(t, c, True) for p, o, t, c in lv['leaves'])
        size = lv['size'] or size
        sw = size // 4
        wmap = struct_word_map(name, lv['leaves'], name)
        text = rewrite_structured(ctx, text, defs, name, sw, wmap)
        text = re.sub(r'ByteAddressBuffer ' + name + r'(_\d+)? : register\((t\d+)\);\n',
                      lambda m: '' if m.group(1) else 'struct S21_%s { uint w[%d]; };\nStructuredBuffer<S21_%s> %s : register(%s);\n' % (name, sw, name, name, m.group(2)), text)
        if '%s_dyn(' % name in text:
            extra_fns.append(structured_dyn_fn(name, sw, wmap))

    if static:
        if 'S21_g_modelInst' not in text and not re.search(r'\buint SV_InstanceID :', text):
            return {'ok': False, 'skip': 'no-instancing', 'kind': kind}
        text = to_static(ctx, text)
        extra_fns = [to_static(ctx, f) for f in extra_fns]
    if inst_ps:
        if kind != 0 or not re.search(r'\bCBufModelInstance_m0\[', text + ''.join(extra_fns)):
            return {'ok': False, 'skip': 'no-cb2', 'kind': kind}
        text = to_instanced_ps(ctx, text)
        extra_fns = [re.sub(r'\bCBufModelInstance_m0\[', 'CBufModelInstance_m0[', f) for f in extra_fns]
        if 'StructuredBuffer<S21_g_modelInst>' not in text:
            text = text.replace('\nstatic ', '\nstruct S21_g_modelInst { uint w[56]; };\nStructuredBuffer<S21_g_modelInst> g_modelInst : register(t75);\nstatic ', 1)

    used = {cb for cb in S21_CB if re.search(r'\b' + cb + r'_m0\[', text + ''.join(extra_fns))}
    decl = ''.join('cbuffer %s : register(%s)\n{\n    float4 %s_m0[%d] : packoffset(c0);\n};\n\n'
                   % (cb, S21_CB[cb][0], cb, S21_CB[cb][1]) for cb in sorted(used))

    # S30-only resources: their gates are pinned, the reads become dead
    for res, val in (('Bindless3d', 'float4(0.0f, 0.0f, 0.0f, 0.0f)'), ('ssaoTexture', 'float4(1.0f, 1.0f, 1.0f, 1.0f)'),
                     ('exposureTexture', 'float4(0.0f, 0.0f, 0.0f, 0.0f)')):
        if re.search(r'\b' + res + r'\b', text):
            text = kill_resource_calls(ctx, text, res, val)
            text = drop_decl(text, res)
            if re.search(r'\b' + res + r'\b', text):
                ctx.unmapped[(res, '<resource>')] += 1
    for smp, reg in SAMPLER_REG.items():
        text = re.sub(r'(\b' + smp + r' : register\()s\d+\)', r'\g<1>' + reg + ')', text)
    if 'S21_CubemapRow' in text:
        decl += 'StructuredBuffer<float> g_cubemapSamples : register(t72);\n'
    text = fix_signatures(ctx, text, si, so)
    if static:
        # COLOR1 is the per-instance stream (bone bases); S21 static skinned VS index bones directly
        mc = re.search(r'\buint4 (\w+) : COLOR1;', text)
        if kind == 1 and mc:
            text, n = re.subn(r'\b' + mc.group(1) + r'\.([xyzw])\b', '0u', text)
            ctx.report['static_color1_zeroed'] += n
        text = static_signature(ctx, text)

    # assemble: prelude + decls before first static/func
    ins = text.find('\nstatic ')
    if ins < 0:
        ins = text.find('\nstruct SPIRV_Cross')
    text = PRELUDE + decl + text[:ins] + '\n' + text[ins:]
    # helper functions go right before the entry function body
    fm = re.search(r'\n(void (?:frag|vert)_main\(\))', text)
    text = text[:fm.start()] + '\n' + '\n'.join(extra_fns) + text[fm.start():]
    hl = os.path.join(WORK, tag + '.s21.hlsl')
    open(hl, 'w').write(text)
    res = {'ok': False, 'kind': kind, 'report': dict(ctx.report), 'unmapped': {'%s:%s' % k: v for k, v in ctx.unmapped.items()},
           'notes': ctx.notes, 'hlsl': hl}
    if ctx.unmapped:
        res['err'] = 'unmapped'
        return res
    prof = {0: 'ps_5_0', 1: 'vs_5_0'}[kind]
    out = os.path.join(WORK, tag + '.s21.dxbc')
    rc, log = run([FXC, '/nologo', '/T', prof, '/E', 'main', '/O3', '/Fo', out, hl])
    if rc:
        res['err'] = 'fxc: ' + ' | '.join(l for l in log.splitlines() if 'error' in l)[:600]
        return res
    if static:
        b = open(out, 'rb').read()
        sigs = [r[0].upper() for fcc in ('ISGN', 'OSGN') for r in static_sig_names(b, fcc)]
        if 'SV_INSTANCEID' in sigs or (kind == 1 and 'COLOR1' in sigs):
            res['err'] = 'static entry still carries the instance id/stream'
            return res
    res['ok'] = True
    res['dxbc'] = out
    return res


def static_sig_names(b, fcc):
    n = struct.unpack_from('<I', b, 28)[0]
    for k in range(n):
        o = struct.unpack_from('<I', b, 32 + 4 * k)[0]
        if b[o:o + 4] == fcc.encode():
            base = o + 8
            out = []
            for e in range(struct.unpack_from('<I', b, base)[0]):
                no, si = struct.unpack_from('<II', b, base + 8 + 24 * e)
                out.append(('%s%d' % (b[base + no:b.index(b'\0', base + no)].decode(), si),))
            return out
    return []


CUBEMAP_FN = r'''
float4 S21_CubemapRow(uint row)
{
    if (row < 64u)
        return float4(0.0f, 0.0f, 0.0f, g_cubemapSamples[row]);
    if (row < 128u)
        return float4(0.0f, 0.0f, 0.0f, 0.0f);
    return float4(dot(CBufCommonPerCamera_m0[16u].xyz, ''' + LUM + r'''), 0.0f, 0.0f, 0.0f);
}
'''


def chunk(b, fcc):
    n = struct.unpack_from('<I', b, 28)[0]
    for k in range(n):
        o = struct.unpack_from('<I', b, 32 + 4 * k)[0]
        if b[o:o + 4] == fcc.encode():
            return b[o + 8:o + 8 + struct.unpack_from('<I', b, o + 4)[0]]
    return None


if __name__ == '__main__':
    import sys
    r = convert(sys.argv[1])
    print(json.dumps(r, indent=1))
