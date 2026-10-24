"""Rewrite shaderset MSWs with the S30 v15 counts.

S30 shds v15 (80 B) keeps textureInputCounts[2] / numSamplers / firstResourceBindPoint /
numResources at +0x10..+0x17 (S21 v12: +0x18..+0x1F); VS/PS guids at +0x40/+0x48 in both.
textureInputCounts[1] is stored folded (VS + PS), as in v12.
"""
import kitpaths as KP
import os, sys, glob, collections

import pak_msw as P

ROOT = (KP.MAPS + r'\mp_rr_desertlands_mu5')
MU = os.path.join(ROOT, r'inv\mp_rr_desertlands_mu5.rpak.dec.rpak')
SRC = os.path.join(ROOT, r'extract\shaderset')
OUT = os.path.join(ROOT, r'extract\shaderset_s21')
S21 = glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak'))


def read_v15(pak, g):
    d, pages, _, _, _ = P.load(pak)
    ao = P.find_asset(pak, g, P.FCC_SHDS)
    ho = P._res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
    t0, t1 = P.u16(d, ho + 0x10), P.u16(d, ho + 0x12)
    return {'guid': g, 'vertexShaderGuid': P.u64(d, ho + 0x40), 'pixelShaderGuid': P.u64(d, ho + 0x48),
            'numPixelShaderTextures': t0, 'numVertexShaderTextures': t1 - t0,
            'numSamplers': P.u16(d, ho + 0x14), 'firstResourceBindPoint': d[ho + 0x16], 'numResources': d[ho + 0x17]}


def main():
    os.makedirs(OUT, exist_ok=True)
    c = collections.Counter()
    keys = ('vertexShaderGuid', 'pixelShaderGuid', 'numPixelShaderTextures', 'numVertexShaderTextures',
            'numSamplers', 'firstResourceBindPoint', 'numResources')
    for f in sorted(glob.glob(os.path.join(SRC, '*.msw'))):
        name = os.path.basename(f)
        g = int(name[:-4], 16)
        ss = read_v15(MU, g)
        b = open(f, 'rb').read()
        assert P.u64(b, 5) == ss['pixelShaderGuid'] and P.u64(b, 13) == ss['vertexShaderGuid'], name
        for pak in S21:
            t = P.read_shaderset(pak, g)
            if t:
                c['twin'] += 1
                diff = [k for k in keys if t[k] != ss[k]]
                c['twin_exact' if not diff else 'twin_diff:' + ','.join(diff)] += 1
                break
        P.write_shaderset_msw(ss, os.path.join(OUT, name))
        c['written'] += 1
        c['tex_ps>0'] += ss['numPixelShaderTextures'] > 0
        c['samplers>0'] += ss['numSamplers'] > 0
    print(dict(c))


if __name__ == '__main__':
    main()
