"""Terrain pixel shader = the converted S30 two-layer world blend PS with its surface inputs replaced by the S30
runtime-virtual-texture composite (splat decode, height-stacked layer weights, per-layer albedo/normal/gloss/ao).

Lighting, lightmaps, cubemaps, shadows and the G-buffer writes are the converted shader's own code; only the
surface terms change. Slot blend is forced to 0 so the slot-0 path carries the composite.
Material texture slots: t0 albedo array, t1 normal array, t2 gloss array, t3 ao array, t4 height array,
t5 splat ids (uint4 per 4x4 texel block), t6 splat weights (BC5), t7 per-layer params (row 0: ramp, mid; row 1: spec).
"""
import kitpaths as KP
import os, re, subprocess, sys

DX = (KP.WORK + r"/dxil2dxbc")

import refl

WORK = os.path.join(DX, "work")
OUT = (KP.WORK + r"/mu5_s30blend/rvt_ps")
SRC_PS = "0x8C913B24CC7DFD6"

COMPOSITE = r'''
Texture2DArray<float4> rvtAlbedo : register(t0);
Texture2DArray<float4> rvtNormal : register(t1);
Texture2DArray<float4> rvtGloss : register(t2);
Texture2DArray<float4> rvtAo : register(t3);
Texture2DArray<float4> rvtHeight : register(t4);
Texture2D<uint4> rvtSplatIds : register(t5);
Texture2D<float4> rvtSplatW : register(t6);
Texture2D<float4> rvtParams : register(t7);

struct RvtSurface { float3 albedo; float2 nrm; float gloss; float ao; float3 spec; };

static const float RVT_ORIGIN = -45500.0f;
static const float RVT_SIZE = 91000.0f;
static const float RVT_TEXELS = 4096.0f;
static const float RVT_TILE = 256.0f;

// splat texel -> up to 3 (layer, weight) in ascending palette slot order
void RvtTexel(int2 t, out uint lay[3], out float w[3], out uint cnt)
{
    t = clamp(t, int2(0, 0), int2(4095, 4095));
    uint4 blk = rvtSplatIds.Load(int3(t >> 2, 0));
    uint k = ((uint(t.y) & 3u) << 2) | (uint(t.x) & 3u);
    uint word = (k < 6u) ? blk.x : ((k < 12u) ? blk.y : blk.z);
    uint mask = (word >> ((k % 6u) * 5u)) & 31u;
    float2 rg = rvtSplatW.Load(int3(t, 0)).xy;
    cnt = 0u;
    lay[0] = 255u; lay[1] = 255u; lay[2] = 255u;
    [unroll] for (uint s = 0u; s < 4u; s++)
    {
        uint pal = (blk.w >> (s * 8u)) & 255u;
        if (((mask >> s) & 1u) != 0u && pal != 255u && cnt < 3u)
        {
            lay[cnt] = pal;
            cnt++;
        }
    }
    w[0] = (cnt == 1u) ? 1.0f : rg.x;
    w[1] = (cnt >= 2u) ? rg.y : 0.0f;
    w[2] = (cnt == 3u) ? saturate(1.0f - rg.x - rg.y) : 0.0f;
}

RvtSurface RvtComposite(float2 wp)
{
    float2 u = (wp - RVT_ORIGIN) / RVT_SIZE * RVT_TEXELS - 0.5f;
    float2 fl = floor(u);
    float2 f = u - fl;
    int2 t0 = int2(fl);
    float2 uv = float2((wp.x - RVT_ORIGIN) / RVT_TILE, (RVT_ORIGIN - wp.y) / RVT_TILE);

    uint ids[8];
    float wt[8];
    int prio[8];
    uint n = 0u;
    [unroll] for (uint i = 0u; i < 8u; i++) { ids[i] = 255u; wt[i] = 0.0f; prio[i] = -1; }

    // nearest texel: its palette order is the stacking order (top = highest slot)
    uint nl[3]; float nw[3]; uint nc;
    RvtTexel(int2(floor(u + 0.5f)), nl, nw, nc);

    [unroll] for (uint c = 0u; c < 4u; c++)
    {
        int2 o = int2(c & 1u, c >> 1);
        float cw = (o.x ? f.x : 1.0f - f.x) * (o.y ? f.y : 1.0f - f.y);
        uint tl[3]; float tw[3]; uint tc;
        RvtTexel(t0 + o, tl, tw, tc);
        [unroll] for (uint j = 0u; j < 3u; j++)
        {
            if (j < tc)
            {
                uint slot = 8u;
                [unroll] for (uint q = 0u; q < 8u; q++) { if (q < n && ids[q] == tl[j]) slot = q; }
                if (slot == 8u && n < 8u) { slot = n; ids[n] = tl[j]; n++; }
                if (slot < 8u) wt[slot] += cw * tw[j];
            }
        }
    }
    [unroll] for (uint i2 = 0u; i2 < 8u; i2++)
    {
        [unroll] for (uint j2 = 0u; j2 < 3u; j2++) { if (j2 < nc && ids[i2] == nl[j2]) prio[i2] = int(j2); }
    }

    // stacking from the top palette slot down, then the layers only present in neighbouring texels
    float outw[8];
    [unroll] for (uint i3 = 0u; i3 < 8u; i3++) outw[i3] = 0.0f;
    float rem = 1.0f;
    [unroll] for (int p = 2; p >= -1; p--)
    {
        [unroll] for (uint i4 = 0u; i4 < 8u; i4++)
        {
            if (i4 < n && prio[i4] == p)
            {
                float w = wt[i4];
                float4 prm = rvtParams.Load(int3(ids[i4], 0, 0));
                float h = rvtHeight.Sample(allSamplers[2u], float3(uv, float(ids[i4]))).x;
                float fade = (w < 0.25f) ? (1.0f - pow(saturate(1.0f - 4.0f * w), 4.0f)) : 1.0f;
                float wq = saturate(((w - prm.y) + fade * h) * prm.x + w) * rem;
                rem = saturate(rem - wq);
                outw[i4] = wq;
            }
        }
    }
    float tot = 0.0f;
    [unroll] for (uint i5 = 0u; i5 < 8u; i5++) tot += outw[i5];
    tot = max(tot, 1e-6f);

    RvtSurface s;
    s.albedo = 0.0f; s.nrm = 0.0f; s.gloss = 0.0f; s.ao = 0.0f; s.spec = 0.0f;
    [unroll] for (uint i6 = 0u; i6 < 8u; i6++)
    {
        float w = outw[i6] / tot;
        if (w > 0.002f)
        {
            float3 c3 = float3(uv, float(ids[i6]));
            s.albedo += w * rvtAlbedo.Sample(allSamplers[2u], c3).xyz;
            s.nrm += w * ((rvtNormal.Sample(allSamplers[2u], c3).xy - 0.501960813999176025390625f) * 2.8676605224609375f);
            s.gloss += w * rvtGloss.Sample(allSamplers[2u], c3).x;
            s.ao += w * rvtAo.Sample(allSamplers[2u], c3).x;
            s.spec += w * rvtParams.Load(int3(ids[i6], 1, 0)).xyz;
        }
    }
    return s;
}
'''


def patch(text):
    lines = text.split("\n")
    # move the original material textures off t0..t10 (dead after the overrides)
    for i, l in enumerate(lines):
        m = re.match(r"^(Texture2D<float4> \w+ : register\(t)(\d+)(\);)$", l)
        if m and int(m.group(2)) <= 10:
            lines[i] = m.group(1) + str(100 + int(m.group(2))) + m.group(3)
    src = "\n".join(lines)
    # composite helpers go right before frag_main (after allSamplers and the cbuffers are declared)
    src = src.replace("void frag_main()", COMPOSITE + "\nvoid frag_main()", 1)
    lines = src.split("\n")
    wpos = []
    out = []
    blend = None
    pending = {}
    i = 0
    while i < len(lines):
        l = lines[i]
        m = re.match(r"^\s*float (_\d+) = CBufCommonPerCamera_m0\[0u\]\.[yzw] \+ TEXCOORD_4\.[xyz];$", l)
        if m and len(wpos) < 3:
            wpos.append(m.group(1))
        m = re.match(r"^(\s*)float (_\d+) = clamp\(\(TEXCOORD_3\.w - CBufUberStatic_m0\[8u\]\.w\).*$", l)
        if m and blend is None:
            blend = m.group(2)
            out.append("%sfloat %s = 0.0f;" % (m.group(1), blend))
            out.append("%sRvtSurface rvt = RvtComposite(float2(%s, %s));" % (m.group(1), wpos[0], wpos[1]))
            i += 1
            continue
        out.append(l)
        m = re.match(r"^(\s*)float (_\d+) = \(\(_\d+ \+ _\d+\) \* 2\.8676605224609375f\) \+ \(-2\.87890625f\);$", l)
        if m:
            k = sum(1 for x in out if "rvt.nrm" in x)
            out.append("%s%s = rvt.nrm.%s;" % (m.group(1), m.group(2), "xy"[k]))
        m = re.match(r"^(\s*)float (_\d+) = \(_\d+ \* _\d+\) \* CBufUberStatic_m0\[22u\]\.([yzw]);$", l)
        if m:
            out.append("%s%s = rvt.albedo.%s;" % (m.group(1), m.group(2), {"y": "x", "z": "y", "w": "z"}[m.group(3)]))
        m = re.match(r"^(\s*)float (_\d+) = \(\(CBufUberStatic_m0\[27u\]\.([xyz]) - CBufUberStatic_m0\[24u\]\.[yzw]\) \* _\d+\) \+ CBufUberStatic_m0\[24u\]\.[yzw];$", l)
        if m:
            out.append("%s%s = rvt.spec.%s;" % (m.group(1), m.group(2), m.group(3)))
        m = re.match(r"^\s*(_\d+) = (gloss|ao)Texture\.Sample\(.*\)\.x;$", l)
        if m:
            pending[m.group(1)] = "rvt.gloss" if m.group(2) == "gloss" else "rvt.ao"
        # after the if/else that assigns a pending var, override it before its first other use
        for var, expr in list(pending.items()):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if re.match(r"^    \S", nxt) and not re.match(r"^    (else|\{|\})", nxt) and re.search(r"\b%s\b" % var, nxt) \
                    and not re.match(r"^\s*%s = " % var, nxt):
                out.append("    %s = %s;" % (var, expr))
                del pending[var]
        i += 1
    res = "\n".join(out)
    checks = {"world pos": len(wpos) == 3, "blend": blend is not None, "nrm": res.count("rvt.nrm.") == 2,
              "albedo": res.count("rvt.albedo.") == 3, "spec": res.count("rvt.spec.") == 3,
              "gloss": "= rvt.gloss;" in res, "ao": "= rvt.ao;" in res}
    bad = [k for k, v in checks.items() if not v]
    if bad:
        raise SystemExit("patch points missing: %s" % bad)
    return res


def main():
    os.makedirs(OUT, exist_ok=True)
    for e in (0, 1):
        src = open(os.path.join(WORK, "%s_%d.s21.hlsl" % (SRC_PS, e))).read()
        hl = os.path.join(OUT, "rvt_%d.hlsl" % e)
        open(hl, "w").write(patch(src))
        dx = os.path.join(OUT, "rvt_%d.dxbc" % e)
        r = subprocess.run([refl.FXC, "/nologo", "/T", "ps_5_0", "/E", "main", "/O3", "/Fo", dx, hl], capture_output=True, text=True)
        if r.returncode:
            sys.exit("fxc entry %d: %s" % (e, (r.stdout + r.stderr)[-1500:]))
        print("entry", e, "ok", os.path.getsize(dx))


if __name__ == "__main__":
    main()
