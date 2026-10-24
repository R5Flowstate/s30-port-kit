"""
S25+ -> S21 DXBC shader transform passes.

P1  CBufUberDynamic layout remap (newer-season 32-byte -> S21 80-byte):
    materialID/subsurfaceID usages cbN[1] -> cbN[4], dcl resize 2 -> 5
    registers, RDEF metadata honesty edits. Idempotent: blobs already at
    size 80 / materialID@64 are recognized and skipped (P2-P4 still run).

P2  Exposure neutralization: every 2D-immediate cb USAGE operand reading
    CBufUberDynamic register 0 with x-only components is replaced by an
    IMMEDIATE32 float 0.0. Genuine S21 shaders never read any
    exposure-interaction field; the newer-season counteraction block is gated on
    rate != 0, so 0.0 disables it exactly. (This REPLACES the earlier,
    semantically wrong relocation to CBufUberStatic[36].y.)

REPAIR  Blobs patched by the earlier wrong relocation contain reads of
    CBufUberStatic register 36 component y -- genuine shaders never read
    that field, so any such read is unambiguously our old rewrite. Those
    operands are replaced with IMMEDIATE32 0.0 exactly like P2.

P3  Cubemap ambient fetch: the S25+ pattern
        and  rA.c1, ID, l(3)
        ushr rA.c2, ID, l(2)
        dp4  rD, cb6[rA.c2], icb[rA.c1]     (icb = 4x4 identity)
    ID is vN.x, a temp, or cb2[7].x (S25 CBufModelInstance cubemap id).
    Rewritten to the S21 access:
        ld_structured rD, ID, l(0), t72.xxxx
    with dcl_constantbuffer cb6 replaced by dcl_resource_structured t72,
    stride 4. Encoding shape (opcode 0xA7 + two extended opcode tokens
    0x80002302/0x00199983, dcl opcode 0xA2) ground-truthed against genuine
    S21 blobs that bind g_cubemapSamples (see dxbc_ops.py). Registers and
    indices emitted are OUR blob's own; nothing is transplanted.

P5  CBufCommonPerCamera RDEF 928 -> 880: newer-season appends c_dof@880 (48B) which
    S21 moved into CBufUberDynamic. Same-GUID SHEX-identical VS differ only
    in this tail + two renamed vars (c_tuaaMipMapBias -> _cbuf_cam_unused0,
    c_overrideExposureCounteractionRate -> c_overrideExposureInteractionRate).
    Size field is patched in place; names that fit are rewritten; c_dof is
    left in the var table but sits past the new size (engine binds 880).
    SHEX reads of that tail are P7 -- P5 does not touch them.

P7  c_dof SHEX remap: 2D-imm reads of CBufCommonPerCamera[55..57] (the 48B
    at 880) rewritten to CBufUberDynamic[1..3], which is where S21 stores
    c_dof (+16, 48B). Size-preserving. Requires UberDynamic already 80B
    (P1). Ground-truthed against S21/S22 77A7: the CoC block that writes
    SV_Target[2] is byte-identical except those three cb slots.

P4  Dither-discard removal: discard instructions whose condition chain
    contains a load from the DitherTexture (t64, RDEF-resolved) are NOPed
    (newer-season-only LOD crossfade; zero genuine S21 blobs reference it). Discards
    from other chains (albedo alpha test) are preserved. Inconclusive
    traces are skipped loudly.

Slot resolution: the RDEF resource-binding table carries REAL BindPoints
(verified: CBufUberStatic=0, CBufUberDynamic=1, CBufModelInstance=2,
CBufCommonPerCamera=3, CubemapSample_s=6, DitherTexture=64) -- the earlier
claim that they are stripped was false. Slots are resolved by direct
name -> bindPoint lookup; the SHEX dcl scan is kept only as a cross-check.

Entry point: convert_blob(blob) -> (bytes, report). Applies P1, P2+REPAIR,
P3, P4 in order and re-signs the DXBC checksum once at the end. The result
is size-preserving unless P3 could not fund its one-dword growth from dead
producers (report['size_changed'] -- pak patchers must not write those in
place). remap_uberdynamic is kept as a compatibility alias.
"""

import struct
from typing import Dict, List, Optional, Tuple

import dxbc
import dxbc_ops as ops

RDEF_CBUF_DESC_SIZE = 24
RDEF_BIND_DESC_SIZE = 32
RDEF_VAR_DESC_SIZE = 40  # SM5 "RD11" var entry

SRC_UBERDYNAMIC_SIZE = 32
S21_UBERDYNAMIC_SIZE = 80
SRC_UBERDYNAMIC_REGS = 2
S21_UBERDYNAMIC_REGS = 5

SRC_CAMERA_SIZE = 928
S21_CAMERA_SIZE = 880
SRC_CAMERA_DOF_OFFSET = 880
# c_dof is 48B = 3 float4s. Camera tail starts at 880 -> cb[55].
# S21 stores the same 48B at CBufUberDynamic +16 -> cb[1].
SRC_CAMERA_DOF_REGS = (55, 56, 57)
S21_UBER_DOF_REG = {55: 1, 56: 2, 57: 3}

SRC_MATERIALID_OFFSET = 16
S21_MATERIALID_OFFSET = 64
SRC_SUBSURFACE_OFFSET = 20
S21_SUBSURFACE_OFFSET = 68

SRC_MATERIALID_REG = 1
S21_MATERIALID_REG = 4

# Old (wrong) exposure relocation target, now repaired: CBufUberStatic
# register 36 component y.
EXPOSURE_STATIC_REG = 36

CUBEMAP_T72 = 72
CUBEMAP_STRIDE = 4
DITHER_BIND_NAME = 'DitherTexture'

# S25/S26 exposure counteraction. The block samples this texture at a slot no
# S21 shader anywhere declares, so the engine never binds it and the load
# returns whatever the previous draw left in the slot.
EXPOSURE_TEX_NAME = 'exposureTexture'
EXPOSURE_TEX_SLOT = 61          # S21 binds nothing here
EXPOSURE_GATE_VAR = 'c_overrideExposureInteractionRate'   # CBufCommonPerCamera
EXPOSURE_TYPE_VAR = 'c_exposureCounteractionType'         # CBufUberStatic

# ld_structured encoding template, byte-for-byte from a genuine S21 blob
# (O_0B691798224EA129_e0: "ld_structured_indexable(structured_buffer,
# stride=4)(mixed,mixed,mixed,mixed) r11.x, v7.x, l(0), t72.xxxx" =
# 8B0000A7 80002302 00199983 <dest 2dw> <rawidx 2dw> 00004001 00000000
# 00107006 00000048). 0x80002302 = extended opcode RESOURCE_DIM
# (structured buffer, stride 4); 0x00199983 = RESOURCE_RETURN_TYPE
# (mixed x4). Only the ENCODING SHAPE is copied; dest/index registers are
# the converted blob's own.
LD_STRUCTURED_OPTOK = 0x8B0000A7
LD_STRUCTURED_EXT1 = 0x80002302
LD_STRUCTURED_EXT2 = 0x00199983
LD_STRUCTURED_IMM0 = (0x00004001, 0x00000000)
LD_STRUCTURED_RES = (0x00107006, CUBEMAP_T72)

# dcl_resource_structured t72, 4 -- from the same genuine donor:
# 040000A2 00107000 00000048 00000004 (exactly 4 dwords, same footprint as
# the dcl_constantbuffer it replaces).
DCL_STRUCTURED_T72 = [0x040000A2, 0x00107000, CUBEMAP_T72, CUBEMAP_STRIDE]

IMM32_SCALAR_TOK = 0x00004001

# S21 leaves CBufCommonPerCamera+660 unwritten -- retail RDEF names it
# _cbuf_cam_unused0. S25/S26 renamed the same slot c_tuaaMipMapBias and add it
# to a computed LOD before sample_l, so on S21 the mip level comes from stale
# constant-buffer bytes. Neutral bias is 0.
CAMERA_DEAD_VARS = ('c_tuaaMipMapBias',)

# g_gpuBones (structured, stride 32) reordered its fields after S21. Every S21
# shader reads the float3 at +4; every S25/S26 shader reads it at +0. Only that
# one field is ever loaded on either side, so the map is unambiguous -- but an
# offset outside the table is NOT guessable and must be reported, not shifted.
GPUBONES_NAME = 'g_gpuBones'
GPUBONES_STRIDE = 32
GPUBONES_OFFSET_MAP = {0: 4}
GPUBONES_S21_OFFSETS = frozenset((4,))


# ============================================================================
# RDEF parsing
# ============================================================================

def _rdef_chunk(data: bytes) -> Optional[Tuple[int, int]]:
    raw = dxbc.find_chunk_raw(data, "RDEF")
    if raw is None:
        return None
    rdef_off = raw + 8
    rdef_size = struct.unpack_from('<I', data, raw + 4)[0]
    if rdef_size < 24:
        return None
    return rdef_off, rdef_size


def _rdef_cbuffers(data: bytes, rdef_off: int, rdef_size: int) -> List[dict]:
    rdef = data[rdef_off:rdef_off + rdef_size]
    if len(rdef) < 8:
        return []
    cb_count = struct.unpack_from('<I', rdef, 0)[0]
    cb_offset = struct.unpack_from('<I', rdef, 4)[0]
    out = []
    for i in range(cb_count):
        d_off = cb_offset + i * RDEF_CBUF_DESC_SIZE
        if d_off + RDEF_CBUF_DESC_SIZE > len(rdef):
            break
        name_off, var_count, var_off, size, flags, ctype = struct.unpack_from('<6I', rdef, d_off)
        if name_off >= len(rdef):
            continue
        end = rdef.find(b'\x00', name_off)
        if end < 0:
            end = min(name_off + 256, len(rdef))
        name = rdef[name_off:end].decode('ascii', errors='replace')
        out.append({
            'name': name,
            'size': size,
            'var_count': var_count,
            'var_off': rdef_off + var_off,
            'desc_off': rdef_off + d_off,
        })
    return out


def _rdef_vars(data: bytes, rdef_off: int, rdef_size: int, cbuf: dict) -> List[dict]:
    rdef = data[rdef_off:rdef_off + rdef_size]
    out = []
    for v in range(cbuf['var_count']):
        vo_abs = cbuf['var_off'] + v * RDEF_VAR_DESC_SIZE
        vo_rel = vo_abs - rdef_off
        if vo_rel + 12 > len(rdef):
            break
        name_off, start_off, vsize = struct.unpack_from('<3I', rdef, vo_rel)
        if name_off >= len(rdef):
            continue
        end = rdef.find(b'\x00', name_off)
        if end < 0:
            end = min(name_off + 256, len(rdef))
        name = rdef[name_off:end].decode('ascii', errors='replace')
        out.append({
            'name': name,
            'start_offset': start_off,
            'size': vsize,
            'abs_start_offset_field': vo_abs + 4,
        })
    return out


def _rdef_bindings(data: bytes) -> List[dict]:
    """RDEF resource-binding table: header u32s cbufferCount@0,
    cbufferOffset@4, bindingCount@8, bindingOffset@12; each binding desc is
    8 u32s {nameOff, type, returnType, viewDim, numSamples, bindPoint,
    bindCount, flags}. type==0 -> cbuffer, type==2 -> texture, type==5 ->
    structured buffer. BindPoints are REAL on these blobs (verified against
    genuine and converted skulltown blobs)."""
    rc = _rdef_chunk(data)
    if rc is None:
        return []
    rdef_off, rdef_size = rc
    rdef = data[rdef_off:rdef_off + rdef_size]
    if len(rdef) < 16:
        return []
    bind_count = struct.unpack_from('<I', rdef, 8)[0]
    bind_off = struct.unpack_from('<I', rdef, 12)[0]
    out = []
    for i in range(bind_count):
        d = bind_off + i * RDEF_BIND_DESC_SIZE
        if d + RDEF_BIND_DESC_SIZE > len(rdef):
            break
        name_off, typ, ret, dim, ns, bp, bcnt, fl = struct.unpack_from('<8I', rdef, d)
        if name_off >= len(rdef):
            continue
        end = rdef.find(b'\x00', name_off)
        if end < 0:
            end = min(name_off + 256, len(rdef))
        name = rdef[name_off:end].decode('ascii', errors='replace')
        out.append({'name': name, 'type': typ, 'bind_point': bp, 'bind_count': bcnt})
    return out


def _binding_slot(bindings: List[dict], name: str, typ: int) -> Optional[int]:
    for b in bindings:
        if b['name'] == name and b['type'] == typ:
            return b['bind_point']
    return None


# ============================================================================
# SHEX declaration scan (cross-check only -- bindings are authoritative)
# ============================================================================

def _shex_cb_declarations(data: bytes) -> Dict[int, int]:
    shex_off, shex_size = dxbc.get_shex_data(data)
    if shex_off is None:
        return {}
    out = {}
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(data, shex_off, shex_size):
        if opcode != ops.OP_DCL_CONSTANT_BUFFER:
            continue
        token = dw[pos]
        ext = dxbc.is_extended(token)
        op_start = pos + 1 + (1 if ext else 0)
        if op_start + 2 >= pos + inst_len:
            continue
        out[dw[op_start + 1]] = dw[op_start + 2] * 16
    return out


# ============================================================================
# Report
# ============================================================================

def _new_report() -> dict:
    return {
        'patched': False,
        'size_changed': False,
        'p1': {'applied': False, 'already': False, 'usages_remapped': 0,
               'dcl_resized': 0, 'rdef_patched': 0, 'bounds_fixed': 0},
        'p2': {'zeroed': 0, 'mixed_skipped': 0},
        'repair': {'zeroed': 0, 'mixed_skipped': 0},
        'p3': {'sites': 0, 'producers_nopped': 0, 'producers_kept': 0,
               'dcl_swapped': 0, 'skipped_sites': 0, 'already': False,
               'needs_resize': False},
        'p4': {'dither_nopped': 0, 'kept': 0, 'inconclusive': 0},
        'p5': {'applied': 0, 'renamed': 0, 'blocked': 0},
        'p6': {'zeroed': 0, 'mixed_skipped': 0, 'blobs': 0, 'already': 0, 'live': 0},
        'p7': {'remapped': 0, 'already': 0, 'skipped': 0, 'dcl_resized': 0},
        'p8': {'tightened': 0, 'bytes_saved': 0, 'dynamic_skipped': 0},
        'p9': {'zeroed': 0, 'mixed_skipped': 0, 'blobs': 0},
        'p10': {'rebased': 0, 'already': 0, 'unknown': 0, 'blobs': 0},
        'reasons': [],
    }


def _log(report, msg):
    report['reasons'].append(msg)


# ============================================================================
# Operand component predicates
# ============================================================================

def _is_only_comp(tok: int, comp: int) -> Tuple[bool, bool]:
    """(is_comp_only, touches_comp) for component index comp (0=x..3=w)."""
    mode = dxbc.get_component_mode(tok)
    if mode == 0:
        mask = dxbc.get_component_mask(tok)
        return (mask == (1 << comp)), bool(mask & (1 << comp))
    if mode == 1:
        swiz = dxbc.get_swizzle(tok)
        want = 0
        for k in range(4):
            want |= (comp & 0x3) << (2 * k)
        touches = any(((swiz >> (2 * k)) & 0x3) == comp for k in range(4))
        return (swiz == want), touches
    if mode == 2:
        sel = (tok >> 4) & 0x3
        return (sel == comp), (sel == comp)
    return False, False


# ============================================================================
# PASS 1: CBufUberDynamic remap (fixed slot resolution, no exposure edit)
# ============================================================================

def _p1_remap(blob: bytes, report: dict, dyn_slot: int) -> bytes:
    """The proven in-place register remap (size-preserving). Assumes the
    predicate (RDEF CBufUberDynamic size 32) was already checked."""
    data = bytearray(blob)
    rdef_off, rdef_size = _rdef_chunk(blob)
    cbuffers = _rdef_cbuffers(blob, rdef_off, rdef_size)
    dyn = next(c for c in cbuffers if c['name'] == 'CBufUberDynamic')

    shex_off, shex_size = dxbc.get_shex_data(bytes(data))
    if shex_off is None:
        _log(report, "P1: no SHEX/SHDR chunk -- skipped")
        return blob

    dwords, _ = dxbc.get_dwords(data, shex_off, shex_size)
    r1 = report['p1']

    for pos, opcode, inst_len, dw in dxbc.iter_instructions(bytes(data), shex_off, shex_size):
        inst_end = pos + inst_len
        if opcode == ops.OP_DCL_CONSTANT_BUFFER:
            token = dw[pos]
            ext = dxbc.is_extended(token)
            idx_cb = pos + 1 + (1 if ext else 0) + 1
            idx_size = idx_cb + 1
            if idx_size < inst_end and dwords[idx_cb] == dyn_slot:
                if dwords[idx_size] < S21_UBERDYNAMIC_REGS:
                    dwords[idx_size] = S21_UBERDYNAMIC_REGS
                    r1['dcl_resized'] += 1
            continue
        if opcode in ops.DECL_OPCODES:
            continue
        j = pos + 1
        while j < inst_end:
            tok = dw[j]
            if dxbc.get_operand_type(tok) != dxbc.OPERAND_TYPE_CONSTANT_BUFFER \
                    or dxbc.get_index_dimension(tok) != 2:
                j += 1
                continue
            rep0 = (tok >> 22) & 0x7
            rep1 = (tok >> 25) & 0x7
            ext = dxbc.is_extended(tok)
            idx0 = j + 1 + (1 if ext else 0)
            if rep0 != 0 or idx0 >= inst_end:
                j += 1
                continue
            if rep1 != 0:
                if dw[idx0] == dyn_slot:
                    _log(report, f"P1: non-IMMEDIATE32 register index on cb{dyn_slot} "
                                 f"at inst_offset={pos} dword={j} -- skipped")
                j += 1
                continue
            idx1 = idx0 + 1
            if idx1 >= inst_end:
                j += 1
                continue
            if dwords[idx0] == dyn_slot and dwords[idx1] == SRC_MATERIALID_REG:
                dwords[idx1] = S21_MATERIALID_REG
                r1['usages_remapped'] += 1
            j += 1

    # bounds invariant: dcl size must exceed the max accessed register
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(bytes(data), shex_off, shex_size):
        if opcode != ops.OP_DCL_CONSTANT_BUFFER:
            continue
        token = dw[pos]
        ext = dxbc.is_extended(token)
        idx_cb = pos + 1 + (1 if ext else 0) + 1
        idx_size = idx_cb + 1
        if idx_size < pos + inst_len and dwords[idx_cb] == dyn_slot \
                and dwords[idx_size] < S21_UBERDYNAMIC_REGS:
            dwords[idx_size] = S21_UBERDYNAMIC_REGS
            r1['bounds_fixed'] += 1

    # self-check: no residual UberDynamic access to registers 1..3 (S21 dof)
    residual = []
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(bytes(data), shex_off, shex_size):
        if opcode in ops.DECL_OPCODES:
            continue
        inst_end = pos + inst_len
        j = pos + 1
        while j < inst_end:
            tok = dw[j]
            if dxbc.get_operand_type(tok) == dxbc.OPERAND_TYPE_CONSTANT_BUFFER \
                    and dxbc.get_index_dimension(tok) == 2 \
                    and ((tok >> 22) & 0x7) == 0 and ((tok >> 25) & 0x7) == 0:
                ext = dxbc.is_extended(tok)
                idx0 = j + 1 + (1 if ext else 0)
                idx1 = idx0 + 1
                if idx1 < inst_end and dw[idx0] == dyn_slot and dw[idx1] in (1, 2, 3):
                    residual.append(j)
            j += 1
    if residual:
        raise AssertionError(
            f"post-remap residual access to CBufUberDynamic (cb{dyn_slot}) registers 1-3 "
            f"at dword offsets {residual}")

    # RDEF metadata honesty edits
    struct.pack_into('<I', data, dyn['desc_off'] + 12, S21_UBERDYNAMIC_SIZE)
    r1['rdef_patched'] += 1
    for v in _rdef_vars(bytes(data), rdef_off, rdef_size, dyn):
        if v['name'] == 'c_materialID' and v['start_offset'] == SRC_MATERIALID_OFFSET:
            struct.pack_into('<I', data, v['abs_start_offset_field'], S21_MATERIALID_OFFSET)
            r1['rdef_patched'] += 1
        elif v['name'] == 'c_subsurfaceMaterialID' and v['start_offset'] == SRC_SUBSURFACE_OFFSET:
            struct.pack_into('<I', data, v['abs_start_offset_field'], S21_SUBSURFACE_OFFSET)
            r1['rdef_patched'] += 1
    old = b'c_exposureCounteractionRate\x00'
    new = b'c_blendShapeVertCount\x00'
    idx = data.find(old)
    if idx >= 0:
        data[idx:idx + len(old)] = new + b'\x00' * (len(old) - len(new))
        r1['rdef_patched'] += 1

    r1['applied'] = True
    return bytes(data)


# ============================================================================
# PASS 2 / REPAIR: zero-immediate replacement of cb operands
# ============================================================================

def _zero_cb_operands(blob: bytes, slot: int, reg: int, comp: int,
                      report: dict, key: str) -> bytes:
    """Replace every 2D-immediate cb USAGE operand reading cb<slot>[reg]
    with ONLY component <comp> by an IMMEDIATE32 float 0.0 operand
    (extended operand tokens preserved). The instruction shrinks; the gap
    is closed with standalone NOP instructions (opcode 0x3A, length 1)
    immediately after it, keeping the total SHEX dword count constant."""
    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return blob
    instrs, declared = ops.parse_instructions(blob, shex_off, shex_size)
    dw = struct.unpack_from('<2I', blob, shex_off)
    header = [dw[0], dw[1]]

    emissions = {}
    rep = report[key]
    for ins in instrs:
        if ins.is_decl or ins.opcode == ops.OP_NOP or not ins.parse_ok:
            continue
        repl = {}
        for oi, op in enumerate(ins.operands):
            if op.typ != ops.OT_CBUFFER or op.idxdim != 2:
                continue
            if len(op.dims) != 2 or op.dims[0][0] != 'imm' or op.dims[1][0] != 'imm':
                continue
            if op.dims[0][1] != slot or op.dims[1][1] != reg:
                continue
            only, touches = _is_only_comp(op.tok, comp)
            if only:
                tok = IMM32_SCALAR_TOK | (0x80000000 if op.ext_toks else 0)
                repl[oi] = [tok] + list(op.ext_toks) + [0x00000000]
            elif touches:
                rep['mixed_skipped'] += 1
                _log(report, f"{key}: cb{slot}[{reg}] operand at shex dword {op.start} "
                             f"mixes component {comp} with others -- left unpatched")
        if not repl:
            continue
        body = []
        for oi, op in enumerate(ins.operands):
            if oi in repl:
                body.extend(repl[oi])
            else:
                body.extend(ins.raw[op.start - ins.pos:op.end - ins.pos])
        new_len = 1 + len(ins.ext_toks) + len(body)
        assert new_len <= ins.length, "operand replacement grew the instruction"
        new_tok = ops.set_instruction_length(ins.tok, new_len)
        emissions[ins.index] = [new_tok] + list(ins.ext_toks) + body \
            + [ops.NOP_TOKEN] * (ins.length - new_len)
        rep['zeroed'] += len(repl)

    if not emissions:
        return blob
    new_dwords = ops.rebuild_stream(instrs, header, emissions)
    assert len(new_dwords) == shex_size // 4, "P2/REPAIR must be size-preserving"
    out, changed = ops.splice_shex(blob, new_dwords)
    assert not changed
    return out


def _cb_var_slot(blob, rdef_off, rdef_size, cbuffers, bindings, cbuf_name, var_name):
    """(slot, reg, comp) for a named variable in a named cbuffer, or None."""
    cb = next((c for c in cbuffers if c['name'] == cbuf_name), None)
    if cb is None:
        return None
    slot = _binding_slot(bindings, cbuf_name, 0)
    if slot is None:
        return None
    for v in _rdef_vars(blob, rdef_off, rdef_size, cb):
        if v['name'] == var_name:
            return slot, v['start_offset'] // 16, (v['start_offset'] % 16) // 4
    return None


def _const_temp(instrs, upto, reg, comp, depth=0):
    """Value of (reg, comp) at instruction `upto` when it is statically
    determined by immediates alone, else None. Only the opcodes the exposure
    gate is built from are folded -- anything else is treated as unknown."""
    if depth > 6:
        return None
    for i in range(upto - 1, -1, -1):
        cur = instrs[i]
        if cur.is_decl or not cur.parse_ok:
            continue
        if not any(r == reg and comp in c for r, c in ops.writes_of(cur)):
            continue
        vals = []
        for s in cur.operands[1:]:
            if s.typ == ops.OT_IMM32:
                vals.append(s.imm_vals[0] if len(s.imm_vals) == 1
                            else s.imm_vals[min(comp, len(s.imm_vals) - 1)])
                continue
            if s.typ == ops.OT_TEMP:
                cs = s.read_comps()
                sub = _const_temp(instrs, i, s.reg_index(),
                                  next(iter(cs)) if len(cs) == 1 else comp, depth + 1)
                if sub is None:
                    return None
                vals.append(sub)
                continue
            return None
        f = lambda v: struct.unpack('<f', struct.pack('<I', v & 0xFFFFFFFF))[0]
        if cur.opcode == ops.OP_MOV:
            return vals[0]
        if cur.opcode == ops.OP_MOVC:
            return vals[1] if vals[0] else vals[2]
        if cur.opcode == ops.OP_NE:
            return 0xFFFFFFFF if f(vals[0]) != f(vals[1]) else 0
        if cur.opcode == ops.OP_IEQ:
            return 0xFFFFFFFF if vals[0] == vals[1] else 0
        if cur.opcode == ops.OP_GE:
            return 0xFFFFFFFF if f(vals[0]) >= f(vals[1]) else 0
        if cur.opcode == ops.OP_LT:
            return 0xFFFFFFFF if f(vals[0]) < f(vals[1]) else 0
        return None
    return None


def _exposure_block_is_dead(blob) -> bool:
    """True when every exposureTexture load sits under a branch whose predicate
    folds to a constant that does not take it. Neutralization is size
    preserving, so the load instructions always survive -- their presence is
    not evidence the block runs, and their absence is not required."""
    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return False
    instrs, _ = ops.parse_instructions(blob, shex_off, shex_size)
    ops.annotate_nesting(instrs)
    for n, ins in enumerate(instrs):
        if ins.is_decl or not ins.parse_ok:
            continue
        if not any(op.typ == ops.OT_RESOURCE and op.dims
                   and op.dims[0][0] == 'imm' and op.dims[0][1] == EXPOSURE_TEX_SLOT
                   for op in ins.operands):
            continue
        nest = getattr(ins, 'nesting', ()) or ()
        if not nest:
            return False
        outer_idx, kind, branch = nest[0]
        pred = instrs[outer_idx].operands[0]
        if pred.typ != ops.OT_TEMP:
            return False
        cs = pred.read_comps()
        v = _const_temp(instrs, outer_idx, pred.reg_index(),
                        next(iter(cs)) if len(cs) == 1 else 0)
        if v is None:
            return False
        taken = (v != 0)
        if kind == 'if' and branch == 1:
            taken = not taken
        if taken:
            return False
    return True


def _p6_exposure_s25(blob, rdef_off, rdef_size, cbuffers, bindings, report):
    """Kill the S25/S26 exposure-counteraction block.

    S21 compiles this block out entirely -- no shader in the retail build
    declares `exposureTexture`, so the engine never binds that slot and the
    block's load returns stale data from whatever bound it last, scaling albedo
    by exp2(-rate * garbage). Zeroing both the outer rate gate and the inner
    type selector makes the whole block unreachable, size-preserving.

    P2/REPAIR do not cover this: their newer-season predicate keys on
    c_exposureCounteractionRate at CBufUberDynamic offset 0, which S25/S26
    shaders do not have.
    """
    if _binding_slot(bindings, EXPOSURE_TEX_NAME, 2) is None:
        return blob

    report['p6']['blobs'] += 1
    data = blob
    located = False
    for cbuf_name, var_name in (('CBufCommonPerCamera', EXPOSURE_GATE_VAR),
                                ('CBufUberStatic', EXPOSURE_TYPE_VAR)):
        loc = _cb_var_slot(data, rdef_off, rdef_size, cbuffers, bindings, cbuf_name, var_name)
        if loc is None:
            continue
        located = True
        slot, reg, comp = loc
        data = _zero_cb_operands(data, slot, reg, comp, report, 'p6')

    if not located:
        _log(report, f"P6: {EXPOSURE_TEX_NAME} is bound but neither {EXPOSURE_GATE_VAR} "
                     f"nor {EXPOSURE_TYPE_VAR} is declared -- the gate is unknown and the "
                     f"load survives, do not ship this blob")
    elif data == blob:
        # An unchanged blob means EITHER the gate operands are already zero
        # immediates (dead, the idempotent case) OR there were no gate operands
        # to find at all (gate unknown, block still live). Those are opposite
        # verdicts, so prove reachability instead of assuming the first.
        if _exposure_block_is_dead(data):
            report['p6']['already'] += 1
        else:
            report['p6']['live'] += 1
            _log(report, f"P6: {EXPOSURE_TEX_NAME} is bound and no gate operand was "
                         f"found to zero, but the t{EXPOSURE_TEX_SLOT} load is not "
                         f"provably unreachable -- do not ship this blob")
    return data


# ============================================================================
# PASS 3: cubemap cb6 -> g_cubemapSamples t72
# ============================================================================

def _find_def(instrs, use_idx, reg, comp):
    """Backward scan for the defining write of (reg, comp) before use_idx,
    stopping at control flow (same-basic-block requirement for the P3
    pattern). Returns Instr or None."""
    for i in range(use_idx - 1, -1, -1):
        ins = instrs[i]
        if ins.opcode in ops.CF_OPCODES:
            return None
        if ins.is_decl:
            return None  # reached the declaration section
        if not ins.parse_ok:
            return None
        for wreg, wcomps in ops.writes_of(ins):
            if wreg == reg and comp in wcomps:
                return ins
    return None


def _imm_scalar_value(op) -> Optional[int]:
    if op.typ == ops.OT_IMM32 and len(op.imm_vals) == 1:
        return op.imm_vals[0]
    return None


def _single_comp(op) -> Optional[int]:
    comps = op.read_comps()
    if len(comps) == 1:
        return next(iter(comps))
    return None


def _follow_movs(instrs, use_idx, reg, comp, depth=4):
    """Resolve the def of (reg, comp), following simple single-component
    mov chains. Returns the final defining Instr or None."""
    ins = _find_def(instrs, use_idx, reg, comp)
    if ins is None:
        return None
    if ins.opcode == ops.OP_MOV and depth > 0 and len(ins.operands) == 2:
        src = ins.operands[1]
        if src.typ == ops.OT_TEMP and src.reg_index() is not None:
            sc = src.swizzle_map(comp)
            return _follow_movs(instrs, ins.index, src.reg_index(), sc, depth - 1)
    return ins


def _identity_icb_present(instrs) -> bool:
    for ins in instrs:
        if ins.opcode != ops.OP_CUSTOMDATA:
            continue
        if (ins.raw[0] >> 11) != 3:  # DCL_IMMEDIATE_CONSTANT_BUFFER class
            continue
        if len(ins.raw) < 18:
            continue
        floats = ins.raw[2:18]
        ident = []
        for r in range(4):
            for c in range(4):
                ident.append(0x3F800000 if r == c else 0)
        if floats == ident:
            return True
    return False


def _producer_dead(instrs, prod, cons_idx, replaced_sites, allowed):
    """Conservative liveness: True only when the producer's result is
    provably dead once the consumer dp4 is replaced. Straight-line rules:
    no control flow between producer/consumer or before the kill-write."""
    dests = list(ops.writes_of(prod))
    if len(dests) != 1:
        return False
    reg, wcomps = dests[0]
    for i in range(prod.index + 1, cons_idx):
        ins = instrs[i]
        if i in allowed:
            continue
        if ins.opcode in ops.CF_OPCODES:
            return False
        if not ins.parse_ok and not ins.is_decl:
            return False
        for rreg, rcomp in ops.reads_of(ins):
            if rreg == reg and rcomp in wcomps:
                return False
    cons = instrs[cons_idx]
    remaining = set(wcomps)
    for wreg, wc in ops.writes_of(cons):
        if wreg == reg:
            remaining -= wc
    i = cons_idx + 1
    while remaining and i < len(instrs):
        ins = instrs[i]
        if ins.opcode == ops.OP_RET and len(ins.nesting) == 0:
            return True  # true end of program, never read
        if ins.opcode in ops.CF_OPCODES:
            return False
        if not ins.parse_ok and not ins.is_decl:
            return False
        if i not in replaced_sites:
            for rreg, rcomp in ops.reads_of(ins):
                if rreg == reg and rcomp in remaining:
                    return False
        for wreg, wc in ops.writes_of(ins):
            if wreg == reg:
                remaining -= wc
        i += 1
    return not remaining


def _p3_cubemap(blob: bytes, report: dict) -> Tuple[bytes, bool]:
    """Returns (blob, size_changed)."""
    r3 = report['p3']
    bindings = _rdef_bindings(blob)
    cm_slot = _binding_slot(bindings, 'CubemapSample_s', 0)
    if cm_slot is None:
        return blob, False

    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return blob, False
    instrs, declared = ops.parse_instructions(blob, shex_off, shex_size)
    ops.annotate_nesting(instrs)
    dw = struct.unpack_from('<2I', blob, shex_off)
    header = [dw[0], dw[1]]

    # already converted? (idempotency)
    has_cb_dcl = any(i.opcode == ops.OP_DCL_CONSTANT_BUFFER and len(i.raw) >= 4
                     and i.raw[2] == cm_slot for i in instrs)
    has_t72_dcl = any(i.opcode == ops.OP_DCL_RESOURCE_STRUCTURED and len(i.raw) >= 4
                      and i.raw[2] == CUBEMAP_T72 for i in instrs)
    if not has_cb_dcl and has_t72_dcl:
        r3['already'] = True
        return blob, False
    if not has_cb_dcl:
        _log(report, f"P3: CubemapSample_s bound at b{cm_slot} but no SHEX dcl -- skipped")
        return blob, False

    if not _identity_icb_present(instrs):
        _log(report, "P3: icb is not the 4x4 identity -- blob SKIPPED for P3 (loud)")
        r3['skipped_sites'] += 1
        return blob, False

    # locate dp4 extraction sites
    sites = []
    for ins in instrs:
        if ins.opcode != ops.OP_DP4 or not ins.parse_ok or len(ins.operands) != 3:
            continue
        dest, s0, s1 = ins.operands
        cb_op = icb_op = None
        for op in (s0, s1):
            if op.typ == ops.OT_CBUFFER and op.idxdim == 2 and len(op.dims) == 2 \
                    and op.dims[0][0] == 'imm' and op.dims[0][1] == cm_slot \
                    and op.dims[1][0] in ('rel', 'immrel'):
                cb_op = op
            elif op.typ == ops.OT_ICB and op.dims and op.dims[-1][0] in ('rel', 'immrel'):
                icb_op = op
        if cb_op is None or icb_op is None:
            continue
        sites.append((ins, dest, cb_op, icb_op))

    if not sites:
        _log(report, f"P3: CubemapSample_s bound (b{cm_slot}, dcl present) but no "
                     f"dp4 extraction pattern found -- SKIPPED (loud)")
        r3['skipped_sites'] += 1
        return blob, False

    def nested_of(op):
        d = op.dims[-1]
        if d[0] == 'rel':
            return d[1], 0
        if d[0] == 'immrel':
            return d[2], d[1]
        return None, None

    emissions = {}
    site_plans = []
    replaced_sites = set()
    local_skipped = 0
    for ins, dest, cb_op, icb_op in sites:
        sub_row, off_row = nested_of(cb_op)
        sub_sel, off_sel = nested_of(icb_op)
        if sub_row is None or sub_sel is None or off_row or off_sel:
            _log(report, f"P3: site at instr {ins.index}: relative index has non-zero "
                         f"immediate offset -- SKIPPED (loud)")
            r3['skipped_sites'] += 1
            local_skipped += 1
            continue
        if ins.tok & 0x2000:  # saturate on dp4 -- can't carry onto ld_structured
            _log(report, f"P3: site at instr {ins.index}: dp4 has _sat -- SKIPPED (loud)")
            r3['skipped_sites'] += 1
            local_skipped += 1
            continue
        rrow, crow = sub_row.reg_index(), _single_comp(sub_row)
        rsel, csel = sub_sel.reg_index(), _single_comp(sub_sel)
        if sub_row.typ != ops.OT_TEMP or sub_sel.typ != ops.OT_TEMP \
                or rrow is None or rsel is None or crow is None or csel is None:
            _log(report, f"P3: site at instr {ins.index}: nested index not a simple "
                         f"temp component -- SKIPPED (loud)")
            r3['skipped_sites'] += 1
            local_skipped += 1
            continue

        ushr = _follow_movs(instrs, ins.index, rrow, crow)
        andi = _follow_movs(instrs, ins.index, rsel, csel)

        def match_shift(d, want_op, want_imm):
            """Match `<op> dest, SRC.c, l(imm)` and return (src_operand,
            src_instr, src_type, src_reg, src_comp). SRC is the raw
            CUBEMAP_ID carrier: either a vertex input (vN.x) or a temp
            (instanced-geometry blobs load the id from a per-instance
            structured buffer into a temp first -- genuine S21 uses temp
            indices on its own structured loads, so the encoding is
            donor-proven either way)."""
            if d is None or d.opcode != want_op or len(d.operands) != 3:
                return None
            a, b = d.operands[1], d.operands[2]
            if _imm_scalar_value(b) == want_imm:
                src = a
            elif want_op == ops.OP_AND and _imm_scalar_value(a) == want_imm:
                src = b  # and is commutative
            else:
                return None
            # S21 ld_structured index is vN.x, a temp, or cb2[7].x
            # (CBufModelInstance cubemap id). S25 uses the same cb2[7].x
            # as the and/ushr source.
            if src.typ not in (ops.OT_INPUT, ops.OT_TEMP, ops.OT_CBUFFER):
                return None
            if src.typ == ops.OT_CBUFFER:
                if src.idxdim != 2 or len(src.dims) != 2:
                    return None
                if src.dims[0][0] != 'imm' or src.dims[1][0] != 'imm':
                    return None
                src_key = (src.typ, src.dims[0][1], src.dims[1][1])
            else:
                if src.reg_index() is None:
                    return None
                src_key = (src.typ, src.reg_index(), None)
            c = _single_comp(src)
            if c is None:
                return None
            return (src, d, src_key, c)

        m_row = match_shift(ushr, ops.OP_USHR, 2)
        m_sel = match_shift(andi, ops.OP_AND, 3)
        if m_row is None or m_sel is None:
            _log(report, f"P3: site at instr {ins.index}: index producers do not match "
                         f"the and/ushr CUBEMAP_ID pattern -- SKIPPED (loud)")
            r3['skipped_sites'] += 1
            local_skipped += 1
            continue
        if (m_row[2], m_row[3]) != (m_sel[2], m_sel[3]):
            _log(report, f"P3: site at instr {ins.index}: and/ushr raw sources differ "
                         f"-- SKIPPED (loud)")
            r3['skipped_sites'] += 1
            local_skipped += 1
            continue

        # Raw-source stability: the replacement moves the read of the raw
        # id from the producers down to the dp4 position, so its value must
        # be unchanged in between. Inputs are read-only; for a TEMP source
        # verify no write covers it between the earliest producer and the
        # dp4 (a write there would also mean the original dp4 wasn't
        # reading ambient[id] cleanly -- skip loudly).
        if m_row[2][0] == ops.OT_TEMP:
            sreg, scomp = m_row[2][1], m_row[3]
            first_idx = min(m_row[1].index, m_sel[1].index)
            clobbered = False
            for i in range(first_idx + 1, ins.index):
                for wreg, wc in ops.writes_of(instrs[i]):
                    if wreg == sreg and scomp in wc:
                        clobbered = True
            if clobbered:
                _log(report, f"P3: site at instr {ins.index}: temp raw source "
                             f"r{sreg}.{scomp} rewritten between producers and dp4 "
                             f"-- SKIPPED (loud)")
                r3['skipped_sites'] += 1
                local_skipped += 1
                continue

        raw_idx_op, ushr_ins = m_row[0], m_row[1]
        raw_idx_dwords = ushr_ins.raw[raw_idx_op.start - ushr_ins.pos:
                                      raw_idx_op.end - ushr_ins.pos]
        dest_dwords = ins.raw[dest.start - ins.pos:dest.end - ins.pos]
        if len(dest_dwords) != 2 or len(raw_idx_dwords) not in (2, 3):
            _log(report, f"P3: site at instr {ins.index}: dest/index operand size "
                         f"dest={len(dest_dwords)} idx={len(raw_idx_dwords)} -- SKIPPED (loud)")
            r3['skipped_sites'] += 1
            local_skipped += 1
            continue

        ld_len = 3 + len(dest_dwords) + len(raw_idx_dwords) + 2 + 2
        ld_tok = 0x800000A7 | (ld_len << 24)
        ld = [ld_tok, LD_STRUCTURED_EXT1, LD_STRUCTURED_EXT2] \
            + list(dest_dwords) + list(raw_idx_dwords) \
            + list(LD_STRUCTURED_IMM0) + list(LD_STRUCTURED_RES)
        site_plans.append({'dp4': ins, 'ld': ld, 'ushr': ushr_ins, 'and': m_sel[1]})
        replaced_sites.add(ins.index)

    if not site_plans:
        return blob, False

    if local_skipped:
        # All-or-nothing per blob: converting only some sites would require
        # swapping the cb6 dcl for t72 while a surviving dp4 still reads
        # cb6 -- invalid either way. Leave the blob untouched, loudly.
        _log(report, f"P3: {local_skipped} site(s) skipped alongside "
                     f"{len(site_plans)} convertible site(s) -- blob left "
                     f"UNCONVERTED for P3 (all-or-nothing)")
        return blob, False

    size_changed = False
    for plan in site_plans:
        ins = plan['dp4']
        deficit = len(plan['ld']) - ins.length  # 11 - 10 = 1
        allowed = {plan['ushr'].index, plan['and'].index} | replaced_sites
        dead_producers = []
        for prod in (plan['and'], plan['ushr']):
            if prod.index in emissions:
                continue  # shared producer, already NOPed by another site
            if _producer_dead(instrs, prod, ins.index, replaced_sites, allowed):
                dead_producers.append(prod)
            else:
                r3['producers_kept'] += 1
        funded = 0
        for prod in dead_producers:
            pad = prod.length
            if funded < deficit:
                take = min(deficit - funded, pad - 1)
                pad -= take
                funded += take
            emissions[prod.index] = [ops.NOP_TOKEN] * pad
            r3['producers_nopped'] += 1
        emissions[ins.index] = plan['ld']
        if funded < deficit:
            size_changed = True
            r3['needs_resize'] = True
            _log(report, f"P3: site at instr {ins.index}: producers still live, "
                         f"blob grows by {deficit - funded} dword(s) -- needs pak-level rebuild")
        r3['sites'] += 1

    # dcl swap: cb<slot> -> dcl_resource_structured t72, stride 4
    for ins in instrs:
        if ins.opcode == ops.OP_DCL_CONSTANT_BUFFER and len(ins.raw) == 4 \
                and ins.raw[2] == cm_slot:
            emissions[ins.index] = list(DCL_STRUCTURED_T72)
            r3['dcl_swapped'] += 1

    new_dwords = ops.rebuild_stream(instrs, header, emissions)
    if len(new_dwords) != shex_size // 4:
        new_dwords[1] = len(new_dwords)
    out, changed = ops.splice_shex(blob, new_dwords)
    out = _p3_rdef_t72(out, report)
    return out, changed


def _p3_rdef_t72(blob: bytes, report: dict) -> bytes:
    """Point the CubemapSample_s RDEF bind at t72 structured, matching S21."""
    rc = _rdef_chunk(blob)
    if rc is None:
        return blob
    rdef_off, rdef_size = rc
    rdef = blob[rdef_off:rdef_off + rdef_size]
    if len(rdef) < 16:
        return blob
    bind_count = struct.unpack_from('<I', rdef, 8)[0]
    bind_off = struct.unpack_from('<I', rdef, 12)[0]
    data = bytearray(blob)
    n = 0
    for i in range(bind_count):
        d = bind_off + i * RDEF_BIND_DESC_SIZE
        if d + RDEF_BIND_DESC_SIZE > len(rdef):
            break
        name_off, typ, ret, dim, ns, bp, bcnt, fl = struct.unpack_from('<8I', rdef, d)
        if name_off >= len(rdef):
            continue
        end = rdef.find(b'\x00', name_off)
        if end < 0:
            continue
        name = rdef[name_off:end].decode('ascii', errors='replace')
        if name != 'CubemapSample_s' or typ != 0 or bp != 6:
            continue
        off = rdef_off + d
        struct.pack_into('<5I', data, off + 4, 5, 6, 1, 4, 72)
        n += 1
    if n:
        report['p3']['rdef_t72'] = n
    return bytes(data)


# ============================================================================
# PASS 4: dither-discard removal
# ============================================================================

def _enclosing_loop_ends(instrs):
    """For each instruction index: ENDLOOP indices of every enclosing loop
    (for the previous-iteration wraparound hazard check)."""
    stack = []
    loop_spans = []
    for ins in instrs:
        if ins.opcode == ops.OP_LOOP:
            stack.append(ins.index)
        elif ins.opcode == ops.OP_ENDLOOP and stack:
            loop_spans.append((stack.pop(), ins.index))
    ends = {}
    for i in range(len(instrs)):
        ends[i] = [e for (s, e) in loop_spans if s < i < e]
    return ends


def _memo(memo, key, val):
    memo[key] = val
    return val


def _trace_condition(instrs, loop_ends, use_idx, reg, comp, memo, depth=0):
    """Backward def-chain trace for the P4 discard classifier. Returns
    ('ok', resource_regs) or ('inconclusive', set())."""
    key = (use_idx, reg, comp)
    if key in memo:
        return memo[key]
    if depth > 64:
        return ('inconclusive', set())
    use = instrs[use_idx]

    # previous-iteration hazard: a write after the use inside an enclosing
    # loop reaches this read on the next iteration
    for end_idx in loop_ends[use_idx]:
        for i in range(use_idx + 1, end_idx):
            ins = instrs[i]
            if not ins.parse_ok and not ins.is_decl:
                return _memo(memo, key, ('inconclusive', set()))
            for wreg, wc in ops.writes_of(ins):
                if wreg == reg and comp in wc:
                    return _memo(memo, key, ('inconclusive', set()))

    # last write before use
    defn = None
    for i in range(use_idx - 1, -1, -1):
        ins = instrs[i]
        if ins.is_decl:
            break
        if not ins.parse_ok:
            return _memo(memo, key, ('inconclusive', set()))
        for wreg, wc in ops.writes_of(ins):
            if wreg == reg and comp in wc:
                defn = ins
                break
        if defn is not None:
            break
    if defn is None:
        return _memo(memo, key, ('inconclusive', set()))
    # divergence rule: the def must dominate the use (its nesting path is a
    # prefix of the use's); a def inside a deeper/other branch is divergent
    if not ops.nesting_is_prefix(defn.nesting, use.nesting):
        return _memo(memo, key, ('inconclusive', set()))

    op = defn.opcode
    resources = set()

    if op in ops.RESOURCE_LOAD_OPCODES:
        for o in defn.src_operands():
            if o.typ == ops.OT_RESOURCE and o.reg_index() is not None:
                resources.add(o.reg_index())
        return _memo(memo, key, ('ok', resources))

    if op in ops.COMPONENT_WISE_OPCODES:
        for o in defn.src_operands():
            if o.typ == ops.OT_TEMP and o.reg_index() is not None:
                sc = o.swizzle_map(comp)
                st, res = _trace_condition(instrs, loop_ends, defn.index,
                                           o.reg_index(), sc, memo, depth + 1)
                if st != 'ok':
                    return _memo(memo, key, ('inconclusive', set()))
                resources |= res
            elif o.typ == ops.OT_INDEXABLE_TEMP:
                return _memo(memo, key, ('inconclusive', set()))
            # cb/imm/input/icb sources: terminal non-resource contributors
        return _memo(memo, key, ('ok', resources))

    if op in ops.REDUCTION_OPCODES:
        for o in defn.src_operands():
            if o.typ == ops.OT_TEMP and o.reg_index() is not None:
                for sc in o.read_comps():
                    st, res = _trace_condition(instrs, loop_ends, defn.index,
                                               o.reg_index(), sc, memo, depth + 1)
                    if st != 'ok':
                        return _memo(memo, key, ('inconclusive', set()))
                    resources |= res
            elif o.typ == ops.OT_INDEXABLE_TEMP:
                return _memo(memo, key, ('inconclusive', set()))
        return _memo(memo, key, ('ok', resources))

    return _memo(memo, key, ('inconclusive', set()))


def _p4_dither(blob: bytes, report: dict) -> bytes:
    r4 = report['p4']
    bindings = _rdef_bindings(blob)
    dither_reg = _binding_slot(bindings, DITHER_BIND_NAME, 2)
    if dither_reg is None:
        return blob

    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return blob
    instrs, declared = ops.parse_instructions(blob, shex_off, shex_size)
    ops.annotate_nesting(instrs)
    loop_ends = _enclosing_loop_ends(instrs)
    dw = struct.unpack_from('<2I', blob, shex_off)
    header = [dw[0], dw[1]]

    emissions = {}
    memo = {}
    for ins in instrs:
        if ins.opcode != ops.OP_DISCARD:
            continue
        if not ins.parse_ok or len(ins.operands) != 1:
            r4['inconclusive'] += 1
            _log(report, f"P4: discard at instr {ins.index} unparseable -- SKIPPED (loud)")
            continue
        cond = ins.operands[0]
        if cond.typ != ops.OT_TEMP or cond.reg_index() is None:
            r4['inconclusive'] += 1
            _log(report, f"P4: discard at instr {ins.index}: condition is not a temp "
                         f"register -- SKIPPED (loud)")
            continue
        status = 'ok'
        resources = set()
        for c in cond.read_comps():
            st, res = _trace_condition(instrs, loop_ends, ins.index,
                                       cond.reg_index(), c, memo)
            if st != 'ok':
                status = 'inconclusive'
                break
            resources |= res
        if status != 'ok':
            r4['inconclusive'] += 1
            _log(report, f"P4: discard at instr {ins.index}: condition chain "
                         f"inconclusive (divergent/unknown def) -- SKIPPED (loud)")
            continue
        if dither_reg in resources:
            emissions[ins.index] = [ops.NOP_TOKEN] * ins.length
            r4['dither_nopped'] += 1
        else:
            r4['kept'] += 1

    if not emissions:
        return blob
    new_dwords = ops.rebuild_stream(instrs, header, emissions)
    assert len(new_dwords) == shex_size // 4
    out, changed = ops.splice_shex(blob, new_dwords)
    assert not changed
    return out


# ============================================================================
# PASS 5: CBufCommonPerCamera 928 -> 880
# ============================================================================

_CAM_RENAMES = (
    (b'c_overrideExposureCounteractionRate\x00', b'c_overrideExposureInteractionRate\x00'),
)


def _reads_camera_dof_tail(blob: bytes, cam_slot) -> bool:
    """True when SHEX reads CBufCommonPerCamera[55..57] through a 2D immediate
    index -- the 48 bytes at 880 that S21 does not bind."""
    if cam_slot is None:
        return False
    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return False
    instrs, _ = ops.parse_instructions(blob, shex_off, shex_size)
    for ins in instrs:
        if ins.is_decl or not ins.parse_ok:
            continue
        for op in ins.operands:
            if op.typ != ops.OT_CBUFFER or op.idxdim != 2 or len(op.dims) != 2:
                continue
            if op.dims[0][0] != 'imm' or op.dims[0][1] != cam_slot:
                continue
            if op.dims[1][0] == 'imm' and op.dims[1][1] in S21_UBER_DOF_REG:
                return True
    return False


def _p5_camera(blob: bytes, report: dict) -> bytes:
    rc = _rdef_chunk(blob)
    if rc is None:
        return blob
    rdef_off, rdef_size = rc
    cbuffers = _rdef_cbuffers(blob, rdef_off, rdef_size)
    cam = next((c for c in cbuffers if c['name'] == 'CBufCommonPerCamera'), None)
    if cam is None or cam['size'] != SRC_CAMERA_SIZE:
        return blob
    has_dof = any(v['name'] == 'c_dof' and v['start_offset'] == SRC_CAMERA_DOF_OFFSET
                  for v in _rdef_vars(blob, rdef_off, rdef_size, cam))
    if not has_dof:
        return blob
    # Shrinking to 880 is only safe if P7 can relocate the reads of the tail it
    # unbinds. P7 needs an S21-sized CBufUberDynamic to move them into; without
    # one the shader is left reading bytes 880..927 of an 880-byte buffer, which
    # D3D answers with zeros. That is strictly worse than leaving the camera at
    # 928, so leave it and say so.
    bindings = _rdef_bindings(blob)
    dyn = next((c for c in cbuffers if c['name'] == 'CBufUberDynamic'), None)
    if _reads_camera_dof_tail(blob, _binding_slot(bindings, 'CBufCommonPerCamera', 0)) \
            and (dyn is None or dyn['size'] != S21_UBERDYNAMIC_SIZE
                 or _binding_slot(bindings, 'CBufUberDynamic', 0) is None):
        report['p5']['blocked'] += 1
        _log(report, "P5: camera reads c_dof at %d but there is no S21-sized "
                     "CBufUberDynamic for P7 to relocate them into -- camera left "
                     "at %d rather than unbinding a tail the shader still reads"
                     % (SRC_CAMERA_DOF_OFFSET, SRC_CAMERA_SIZE))
        return blob
    data = bytearray(blob)
    struct.pack_into('<I', data, cam['desc_off'] + 12, S21_CAMERA_SIZE)
    report['p5']['applied'] += 1
    for old, new in _CAM_RENAMES:
        idx = data.find(old)
        if idx < 0:
            continue
        data[idx:idx + len(old)] = new + b'\x00' * (len(old) - len(new))
        report['p5']['renamed'] += 1
    return bytes(data)


# ============================================================================
# PASS 7: c_dof camera[55..57] -> UberDynamic[1..3]
# ============================================================================

def _p7_dof_to_uber(blob: bytes, report: dict) -> bytes:
    """Rewrite 2D-imm CBufCommonPerCamera[55..57] usages to CBufUberDynamic
    [1..3]. S21 binds an 880-byte camera and stores c_dof in the 80-byte
    uber at +16; S25/S26 SHEX still reads the camera tail P5 unbound."""
    r7 = report['p7']
    rc = _rdef_chunk(blob)
    if rc is None:
        return blob
    rdef_off, rdef_size = rc
    cbuffers = _rdef_cbuffers(blob, rdef_off, rdef_size)
    bindings = _rdef_bindings(blob)
    dyn = next((c for c in cbuffers if c['name'] == 'CBufUberDynamic'), None)
    cam_slot = _binding_slot(bindings, 'CBufCommonPerCamera', 0)
    dyn_slot = _binding_slot(bindings, 'CBufUberDynamic', 0)
    if cam_slot is None:
        return blob

    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return blob

    hits = 0
    relative = 0
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(blob, shex_off, shex_size):
        if opcode in ops.DECL_OPCODES:
            continue
        inst_end = pos + inst_len
        j = pos + 1
        while j < inst_end:
            tok = dw[j]
            if dxbc.get_operand_type(tok) != dxbc.OPERAND_TYPE_CONSTANT_BUFFER \
                    or dxbc.get_index_dimension(tok) != 2:
                j += 1
                continue
            rep0 = (tok >> 22) & 0x7
            rep1 = (tok >> 25) & 0x7
            ext = dxbc.is_extended(tok)
            idx0 = j + 1 + (1 if ext else 0)
            if rep0 != 0 or idx0 >= inst_end:
                j += 1
                continue
            if dw[idx0] != cam_slot:
                j += 1
                continue
            if rep1 != 0:
                relative += 1
                j += 1
                continue
            idx1 = idx0 + 1
            if idx1 < inst_end and int(dw[idx1]) in S21_UBER_DOF_REG:
                hits += 1
            j += 1

    if hits == 0:
        r7['already'] += 1
        return blob
    if dyn is None or dyn_slot is None or dyn['size'] != S21_UBERDYNAMIC_SIZE:
        r7['skipped'] += 1
        _log(report, f"P7: {hits} camera[55..57] reads but UberDynamic "
                     f"size={None if dyn is None else dyn['size']} slot={dyn_slot} -- SKIPPED")
        return blob
    if relative:
        _log(report, f"P7: {relative} relative camera index operand(s) left in place")

    data = bytearray(blob)
    dwords, _ = dxbc.get_dwords(data, shex_off, shex_size)
    n = 0
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(bytes(data), shex_off, shex_size):
        if opcode in ops.DECL_OPCODES:
            continue
        inst_end = pos + inst_len
        j = pos + 1
        while j < inst_end:
            tok = dw[j]
            if dxbc.get_operand_type(tok) != dxbc.OPERAND_TYPE_CONSTANT_BUFFER \
                    or dxbc.get_index_dimension(tok) != 2:
                j += 1
                continue
            rep0 = (tok >> 22) & 0x7
            rep1 = (tok >> 25) & 0x7
            ext = dxbc.is_extended(tok)
            idx0 = j + 1 + (1 if ext else 0)
            if rep0 != 0 or rep1 != 0 or idx0 + 1 >= inst_end:
                j += 1
                continue
            reg = int(dwords[idx0 + 1])
            if int(dwords[idx0]) == cam_slot and reg in S21_UBER_DOF_REG:
                dwords[idx0] = dyn_slot
                dwords[idx0 + 1] = S21_UBER_DOF_REG[reg]
                n += 1
            j += 1

    if n:
        for pos, opcode, inst_len, dw in dxbc.iter_instructions(bytes(data), shex_off, shex_size):
            if opcode != ops.OP_DCL_CONSTANT_BUFFER:
                continue
            token = dw[pos]
            ext = dxbc.is_extended(token)
            idx_cb = pos + 1 + (1 if ext else 0) + 1
            idx_size = idx_cb + 1
            if idx_size < pos + inst_len and dwords[idx_cb] == dyn_slot \
                    and dwords[idx_size] < S21_UBERDYNAMIC_REGS:
                dwords[idx_size] = S21_UBERDYNAMIC_REGS
                r7['dcl_resized'] += 1

    r7['remapped'] += n
    return bytes(data)


# ============================================================================
# Orchestrator
# ============================================================================

# ============================================================================
# PASS 8: tighten every dcl_constantbuffer to the registers actually read
# ============================================================================

def _cb_access(blob: bytes) -> Tuple[Dict[int, int], set]:
    """(max 2D-immediate register read per cb slot, set of slots reached with
    a dynamic index)."""
    raw, doff, size = ops.get_shex_raw(blob)
    if raw is None:
        return {}, set()
    instrs, _ = ops.parse_instructions(blob, doff, size)
    mx: Dict[int, int] = {}
    dyn = set()
    for ins in instrs:
        if not ins.parse_ok or ins.is_decl:
            continue
        for op in ins.operands:
            if op.typ != ops.OT_CBUFFER or len(op.dims) < 2:
                continue
            if op.dims[0][0] != 'imm':
                continue
            slot = op.dims[0][1]
            if op.dims[1][0] != 'imm':
                dyn.add(slot)
                continue
            mx[slot] = max(mx.get(slot, -1), op.dims[1][1])
    return mx, dyn


def _p8_tighten_dcl(blob: bytes, report: dict) -> bytes:
    """S21's compiler declares each constant buffer at exactly one more
    register than the shader reads; S25/S26 declare the full CPU-side struct.
    An over-declared buffer is what the driver validates the bound resource
    against, so shrink each dcl to the real high-water mark. Never grows one,
    and skips any slot reached through a dynamic index."""
    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return blob
    mx, dyn = _cb_access(blob)
    data = bytearray(blob)
    changed = False
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(blob, shex_off, shex_size):
        if opcode != ops.OP_DCL_CONSTANT_BUFFER:
            continue
        op_start = pos + 1 + (1 if dxbc.is_extended(dw[pos]) else 0)
        if op_start + 2 >= pos + inst_len:
            continue
        slot, declared = dw[op_start + 1], dw[op_start + 2]
        if slot in dyn:
            report['p8']['dynamic_skipped'] += 1
            continue
        want = mx.get(slot, -1) + 1
        if want <= 0 or want >= declared:
            continue
        struct.pack_into('<I', data, shex_off + (op_start + 2) * 4, want)
        report['p8']['tightened'] += 1
        report['p8']['bytes_saved'] += (declared - want) * 16
        changed = True
    return bytes(data) if changed else blob


# ============================================================================
# PASS 9: neutralize camera slots S21 never writes
# ============================================================================

def _p9_camera_dead_vars(blob, rdef_off, rdef_size, cbuffers, bindings, report):
    cam = next((c for c in cbuffers if c['name'] == 'CBufCommonPerCamera'), None)
    if cam is None:
        return blob
    slot = _binding_slot(bindings, 'CBufCommonPerCamera', 0)
    if slot is None:
        return blob
    hit = False
    for v in _rdef_vars(blob, rdef_off, rdef_size, cam):
        if v['name'] not in CAMERA_DEAD_VARS:
            continue
        before = report['p9']['zeroed']
        blob = _zero_cb_operands(blob, slot, v['start_offset'] // 16,
                                 (v['start_offset'] % 16) // 4, report, 'p9')
        hit = hit or report['p9']['zeroed'] > before
    if hit:
        report['p9']['blobs'] += 1
    return blob


# ============================================================================
# PASS 10: rebase g_gpuBones field offsets to the S21 struct layout
# ============================================================================

def _p10_gpubones(blob: bytes, report: dict) -> bytes:
    """S21 and S25/S26 disagree on where the bone origin sits inside the
    32-byte g_gpuBones element. Foliage wind reads it as the sway pivot, so a
    port reads (scale, pos.x, pos.y) where S21 stores (pos.x, pos.y, pos.z)
    and the displaced vertices leave the frustum. Rewrites the immediate byte
    offset of every ld_structured against that buffer."""
    slot = _binding_slot(_rdef_bindings(blob), GPUBONES_NAME, 5)
    if slot is None:
        return blob
    shex_off, shex_size = dxbc.get_shex_data(blob)
    if shex_off is None:
        return blob
    stride = None
    for pos, opcode, inst_len, dw in dxbc.iter_instructions(blob, shex_off, shex_size):
        if opcode != ops.OP_DCL_RESOURCE_STRUCTURED:
            continue
        o = pos + 1 + (1 if dxbc.is_extended(dw[pos]) else 0)
        if dw[o + 1] == slot:
            stride = dw[o + 2]
    if stride != GPUBONES_STRIDE:
        return blob

    raw, doff, size = ops.get_shex_raw(blob)
    if raw is None:
        return blob
    instrs, _ = ops.parse_instructions(blob, doff, size)
    data = bytearray(blob)
    changed = False
    for ins in instrs:
        if not ins.parse_ok or ins.opcode != ops.OP_LD_STRUCTURED or len(ins.operands) < 4:
            continue
        res, bo = ins.operands[3], ins.operands[2]
        if res.typ != ops.OT_RESOURCE or not res.dims or res.dims[0][1] != slot:
            continue
        if bo.typ != ops.OT_IMM32 or not bo.imm_vals:
            report['p10']['unknown'] += 1
            _log(report, f"P10: {GPUBONES_NAME} load with a non-immediate byte offset")
            continue
        cur = bo.imm_vals[0]
        if cur in GPUBONES_S21_OFFSETS:
            report['p10']['already'] += 1
            continue
        if cur not in GPUBONES_OFFSET_MAP:
            report['p10']['unknown'] += 1
            _log(report, f"P10: {GPUBONES_NAME} field offset {cur} has no known S21 "
                         f"counterpart -- left alone")
            continue
        struct.pack_into('<I', data, shex_off + (bo.start + 1) * 4,
                         GPUBONES_OFFSET_MAP[cur])
        report['p10']['rebased'] += 1
        changed = True
    if changed:
        report['p10']['blobs'] += 1
    return bytes(data) if changed else blob


def convert_blob(blob: bytes) -> Tuple[bytes, dict]:
    """Apply P1 (idempotent), P2 + REPAIR, P3, P4 to one DXBC blob and
    re-sign the checksum once. Returns (bytes, report).

    NOTE: P2/REPAIR only run on newer-season blobs, identified by the
    CBufUberDynamic RDEF var 'c_exposureCounteractionRate' at offset 0
    (present both pre- and post-P1; genuine S21 blobs carry
    c_blendShapeVertCount there and legitimately read register 0.x, which
    must never be zeroed)."""
    report = _new_report()

    if len(blob) < 32 or blob[:4] != b'DXBC':
        _log(report, "not a DXBC blob")
        return blob, report
    rc = _rdef_chunk(blob)
    if rc is None:
        _log(report, "no RDEF chunk")
        return blob, report
    rdef_off, rdef_size = rc
    cbuffers = _rdef_cbuffers(blob, rdef_off, rdef_size)
    bindings = _rdef_bindings(blob)

    dyn = next((c for c in cbuffers if c['name'] == 'CBufUberDynamic'), None)
    dyn_slot = _binding_slot(bindings, 'CBufUberDynamic', 0)
    static_slot = _binding_slot(bindings, 'CBufUberStatic', 0)

    newer_origin = False
    if dyn is not None:
        for v in _rdef_vars(blob, rdef_off, rdef_size, dyn):
            if v['name'] == 'c_exposureCounteractionRate' and v['start_offset'] == 0:
                newer_origin = True
                break

    data = blob

    # ---- P1 ----
    if dyn is not None and dyn_slot is not None:
        if dyn_slot not in _shex_cb_declarations(blob):
            _log(report, f"P1: warning -- binding table says CBufUberDynamic=b{dyn_slot} "
                         f"but no SHEX dcl_constantbuffer for that slot")
        if dyn['size'] == SRC_UBERDYNAMIC_SIZE:
            data = _p1_remap(data, report, dyn_slot)
        elif dyn['size'] == S21_UBERDYNAMIC_SIZE:
            mat64 = any(v['name'] == 'c_materialID' and v['start_offset'] == S21_MATERIALID_OFFSET
                        for v in _rdef_vars(blob, rdef_off, rdef_size, dyn))
            if mat64:
                report['p1']['already'] = True
            else:
                _log(report, "P1: CBufUberDynamic size 80 but c_materialID not at 64 -- "
                             "unexpected shape, P1 skipped")
        else:
            _log(report, f"P1: CBufUberDynamic size={dyn['size']} (neither 32 nor 80) -- "
                         f"P1 skipped")
    elif dyn is not None and dyn_slot is None:
        _log(report, "P1: CBufUberDynamic in RDEF but not in binding table -- P1 skipped")

    # ---- P2 + REPAIR (newer-season blobs only) ----
    if newer_origin and dyn_slot is not None:
        data = _zero_cb_operands(data, dyn_slot, 0, 0, report, 'p2')
    if newer_origin and static_slot is not None:
        data = _zero_cb_operands(data, static_slot, EXPOSURE_STATIC_REG, 1,
                                 report, 'repair')

    # ---- P6 (S25/S26 exposure counteraction) ----
    data = _p6_exposure_s25(data, rdef_off, rdef_size, cbuffers, bindings, report)

    # ---- P3 ----
    data, resized = _p3_cubemap(data, report)
    if resized:
        report['size_changed'] = True

    # P4 dither NOP is off: S21 same-GUID originals still bind DitherTexture
    # and keep those discards. Dropping them made us less like the reference.

    # ---- P5 ----
    data = _p5_camera(data, report)

    # ---- P7 (c_dof camera tail -> UberDynamic[1..3]) ----
    data = _p7_dof_to_uber(data, report)

    # ---- P10 (g_gpuBones field layout) ----
    data = _p10_gpubones(data, report)

    # ---- P9 (camera slots S21 leaves unwritten) ----
    data = _p9_camera_dead_vars(data, rdef_off, rdef_size, cbuffers, bindings, report)

    # ---- P8 (tighten dcl_constantbuffer; must run last) ----
    data = _p8_tighten_dcl(data, report)

    if data != blob:
        buf = bytearray(data)
        dxbc.update_hash(buf)
        data = bytes(buf)
        report['patched'] = True
        okay, msg = ops.validate_shex(data)
        assert okay, f"post-transform SHEX walk invariant failed: {msg}"
        if not report['size_changed']:
            assert len(data) == len(blob), "unexpected size change"

    return data, report


def remap_uberdynamic(blob: bytes) -> Tuple[bytes, dict]:
    """Compatibility alias: the historical P1-only entry now runs the full
    P1-P4 pipeline."""
    return convert_blob(blob)
