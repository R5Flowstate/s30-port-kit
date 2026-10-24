"""
dxbc_ops.py -- SM4/SM5 instruction-level parsing and stream rewriting.

Companion to dxbc.py (chunk finding + hash). This module carries:
  - a CORRECT SM5 opcode table (ground-truthed against cmd_Decompiler
    disassembly of genuine S21 blobs: 0x55=ushr, 0x36=mov, 0x35=customdata,
    0xA7=ld_structured, 0xA2=dcl_resource_structured, 0x0D=discard);
    NOTE: dxbc.py's OPCODE_USHR/OPCODE_UBFE constants are mislabeled, do
    not use them for opcode matching.
  - a recursive operand parser that handles RELATIVE index representations
    (a relative index embeds a full nested operand -- dxbc.py's
    get_operand_size undercounts these);
  - an instruction model (opcode token + extended opcode token chain +
    operands) with read/write component analysis;
  - a SHEX stream rebuilder that can splice a modified dword list back in
    place (size-preserving) or rebuild the whole DXBC container when the
    stream grew (size-changed blobs, reported to the caller).

All ground truth for encodings was read off genuine S21 blobs with
cmd_Decompiler --disassemble-flugan and a raw token walker; see the
docstrings on LD_STRUCTURED_TEMPLATE below.
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import dxbc

# ============================================================================
# SM4/SM5 opcodes (verified subset -- the names here match the official
# D3D10/11 tokenized-format enum, cross-checked against real disassembly)
# ============================================================================

OP_ADD          = 0x00
OP_AND          = 0x01
OP_BREAK        = 0x02
OP_BREAKC       = 0x03
OP_CALL         = 0x04
OP_CALLC        = 0x05
OP_CASE         = 0x06
OP_CONTINUE     = 0x07
OP_CONTINUEC    = 0x08
OP_CUT          = 0x09
OP_DEFAULT      = 0x0A
OP_DERIV_RTX    = 0x0B
OP_DERIV_RTY    = 0x0C
OP_DISCARD      = 0x0D
OP_DIV          = 0x0E
OP_DP2          = 0x0F
OP_DP3          = 0x10
OP_DP4          = 0x11
OP_ELSE         = 0x12
OP_ENDIF        = 0x15
OP_ENDLOOP      = 0x16
OP_ENDSWITCH    = 0x17
OP_EQ           = 0x18
OP_EXP          = 0x19
OP_FRC          = 0x1A
OP_FTOI         = 0x1B
OP_FTOU         = 0x1C
OP_GE           = 0x1D
OP_IADD         = 0x1E
OP_IF           = 0x1F
OP_IEQ          = 0x20
OP_IGE          = 0x21
OP_ILT          = 0x22
OP_IMAD         = 0x23
OP_IMAX         = 0x24
OP_IMIN         = 0x25
OP_IMUL         = 0x26
OP_INE          = 0x27
OP_INEG         = 0x28
OP_ISHL         = 0x29
OP_ISHR         = 0x2A
OP_ITOF         = 0x2B
OP_LABEL        = 0x2C
OP_LD           = 0x2D
OP_LD_MS        = 0x2E
OP_LOG          = 0x2F
OP_LOOP         = 0x30
OP_LT           = 0x31
OP_MAD          = 0x32
OP_MAX          = 0x33
OP_MIN          = 0x34
OP_CUSTOMDATA   = 0x35
OP_MOV          = 0x36
OP_MOVC         = 0x37
OP_MUL          = 0x38
OP_NE           = 0x39
OP_NOP          = 0x3A
OP_NOT          = 0x3B
OP_OR           = 0x3C
OP_RESINFO      = 0x3D
OP_RET          = 0x3E
OP_RETC         = 0x3F
OP_ROUND_NE     = 0x40
OP_ROUND_NI     = 0x41
OP_ROUND_PI     = 0x42
OP_ROUND_Z      = 0x43
OP_RSQ          = 0x44
OP_SAMPLE       = 0x45
OP_SAMPLE_C     = 0x46
OP_SAMPLE_C_LZ  = 0x47
OP_SAMPLE_L     = 0x48
OP_SAMPLE_D     = 0x49
OP_SAMPLE_B     = 0x4A
OP_SQRT         = 0x4B
OP_SWITCH       = 0x4C
OP_SINCOS       = 0x4D
OP_UDIV         = 0x4E
OP_ULT          = 0x4F
OP_UGE          = 0x50
OP_UMUL         = 0x51
OP_UMAD         = 0x52
OP_UMAX         = 0x53
OP_UMIN         = 0x54
OP_USHR         = 0x55
OP_UTOF         = 0x56
OP_XOR          = 0x57
OP_DCL_RESOURCE         = 0x58
OP_DCL_CONSTANT_BUFFER  = 0x59
OP_DCL_SAMPLER          = 0x5A
OP_DCL_GLOBAL_FLAGS     = 0x6A
OP_GATHER4      = 0x6D
OP_BUFINFO      = 0x79
OP_DERIV_RTX_COARSE = 0x7A
OP_DERIV_RTX_FINE   = 0x7B
OP_DERIV_RTY_COARSE = 0x7C
OP_DERIV_RTY_FINE   = 0x7D
OP_GATHER4_C    = 0x7E
OP_GATHER4_PO   = 0x7F
OP_GATHER4_PO_C = 0x80
OP_RCP          = 0x81
OP_F32TOF16     = 0x82   # observed live in skulltown blobs (LDR packing)
OP_F16TOF32     = 0x83
OP_UADDC        = 0x84
OP_USUBB        = 0x85
OP_COUNTBITS    = 0x86
OP_FIRSTBIT_HI  = 0x87
OP_FIRSTBIT_LO  = 0x88
OP_FIRSTBIT_SHI = 0x89
OP_UBFE         = 0x8A
OP_IBFE         = 0x8B
OP_BFI          = 0x8C
OP_BFREV        = 0x8D
OP_SWAPC        = 0x8E
OP_DCL_RESOURCE_STRUCTURED = 0xA2   # verified: 040000A2 00107000 <reg> <stride>
OP_LD_UAV_TYPED = 0xA3
OP_LD_RAW       = 0xA5
OP_LD_STRUCTURED = 0xA7             # verified: 8B0000A7 + 2 ext opcode tokens

NOP_TOKEN = 0x0100003A  # opcode 0x3A, length 1, single-token instruction

# Declaration opcodes (never contain USAGE operands we rewrite; their
# trailing dwords are not all operands, so we don't operand-parse them).
# NOTE the SM5 dcl block starts at DCL_STREAM=0x8F; 0x79-0x8E are ALU ops
# (bufinfo/gather4 variants/rcp/f32tof16/f16tof32/bitfield ops/swapc) --
# an earlier draft misclassified 0x7A-0x84 as dcls, which broke def
# scanning on blobs using f32tof16/f16tof32.
DECL_OPCODES = ({OP_CUSTOMDATA}
                | set(range(0x58, 0x6B))     # dcl_resource .. dcl_globalFlags
                | set(range(0x71, 0x75))     # HS decl/phase markers
                | set(range(0x8F, 0xA3)))    # dcl_stream .. dcl_resource_structured

# Control-flow opcodes: divergence / block boundaries for liveness + tracing.
CF_OPCODES = {OP_BREAK, OP_BREAKC, OP_CALL, OP_CALLC, OP_CASE, OP_CONTINUE,
              OP_CONTINUEC, OP_DEFAULT, OP_ELSE, OP_ENDIF, OP_ENDLOOP,
              OP_ENDSWITCH, OP_IF, OP_LABEL, OP_LOOP, OP_RET, OP_RETC,
              OP_SWITCH}

# Resource-reading instruction opcodes (terminal contributors for the P4
# dither trace; the resource operand identifies which texture/buffer).
RESOURCE_LOAD_OPCODES = {OP_LD, OP_LD_MS, OP_SAMPLE, OP_SAMPLE_C,
                         OP_SAMPLE_C_LZ, OP_SAMPLE_L, OP_SAMPLE_D,
                         OP_SAMPLE_B, OP_GATHER4, OP_GATHER4_C,
                         OP_GATHER4_PO, OP_GATHER4_PO_C, OP_BUFINFO,
                         OP_LD_RAW, OP_LD_STRUCTURED, OP_LD_UAV_TYPED,
                         OP_RESINFO}

# Component-wise ALU ops: dest component c reads source swizzle[c].
COMPONENT_WISE_OPCODES = {OP_ADD, OP_AND, OP_DIV, OP_EQ, OP_EXP, OP_FRC,
                          OP_FTOI, OP_FTOU, OP_GE, OP_IADD, OP_IEQ, OP_IGE,
                          OP_ILT, OP_IMAD, OP_IMAX, OP_IMIN, OP_INE, OP_INEG,
                          OP_ISHL, OP_ISHR, OP_ITOF, OP_LT, OP_MAD, OP_MAX,
                          OP_MIN, OP_MOV, OP_MOVC, OP_MUL, OP_NE, OP_NOT,
                          OP_OR, OP_ROUND_NE, OP_ROUND_NI, OP_ROUND_PI,
                          OP_ROUND_Z, OP_RSQ, OP_SQRT, OP_ULT, OP_UGE,
                          OP_UMAD, OP_UMAX, OP_UMIN, OP_USHR, OP_UTOF,
                          OP_XOR, OP_DERIV_RTX, OP_DERIV_RTY,
                          OP_DERIV_RTX_COARSE, OP_DERIV_RTX_FINE,
                          OP_DERIV_RTY_COARSE, OP_DERIV_RTY_FINE,
                          OP_RCP, OP_F32TOF16, OP_F16TOF32, OP_COUNTBITS,
                          OP_FIRSTBIT_HI, OP_FIRSTBIT_LO, OP_FIRSTBIT_SHI,
                          OP_UBFE, OP_IBFE, OP_BFI, OP_BFREV}

# Reduction ops: every swizzle component of every source contributes to the
# (scalar-ish) result.
REDUCTION_OPCODES = {OP_DP2, OP_DP3, OP_DP4}

# Number of dest operands for opcodes where it isn't the default 1.
TWO_DEST_OPCODES = {OP_IMUL, OP_UMUL, OP_UDIV, OP_SINCOS, OP_UADDC,
                    OP_USUBB, OP_SWAPC}
OP_EMIT = 0x13
OP_EMITTHENCUT = 0x14
ZERO_DEST_OPCODES = ({OP_DISCARD, OP_NOP, OP_CUT, OP_EMIT, OP_EMITTHENCUT}
                     | CF_OPCODES | DECL_OPCODES)

# Operand types
OT_TEMP  = 0
OT_INPUT = 1
OT_OUTPUT = 2
OT_INDEXABLE_TEMP = 3
OT_IMM32 = 4
OT_IMM64 = 5
OT_RESOURCE = 7
OT_CBUFFER = 8
OT_ICB = 9


# ============================================================================
# Operand / instruction model
# ============================================================================

@dataclass
class Operand:
    start: int              # dword index of the operand token
    end: int                # one past the last dword of this operand
    tok: int
    ext_toks: List[int]
    typ: int
    idxdim: int
    dims: list              # per-dim: ('imm', v) | ('rel', Operand) | ('immrel', v, Operand)
    imm_vals: list          # for OT_IMM32/OT_IMM64

    @property
    def size(self):
        return self.end - self.start

    def comp_mode(self):
        return (self.tok >> 2) & 0x3

    def read_comps(self):
        """Set of component indices (0..3) this operand references as a
        SOURCE."""
        nc = self.tok & 0x3
        if nc == 1:          # 1-component operand: component 0
            return {0}
        if nc == 0:          # 0-component operand (e.g. resource in dcl)
            return set()
        mode = self.comp_mode()
        if mode == 0:        # mask
            m = (self.tok >> 4) & 0xF
            return {c for c in range(4) if m & (1 << c)}
        if mode == 1:        # swizzle
            sw = (self.tok >> 4) & 0xFF
            return {(sw >> (2 * c)) & 0x3 for c in range(4)}
        if mode == 2:        # select1
            return {(self.tok >> 4) & 0x3}
        return {0, 1, 2, 3}

    def write_comps(self):
        """Set of component indices written when used as a DEST (mask mode)."""
        nc = self.tok & 0x3
        if nc == 1:
            return {0}
        mode = self.comp_mode()
        if mode == 0:
            m = (self.tok >> 4) & 0xF
            return {c for c in range(4) if m & (1 << c)}
        if mode == 2:
            return {(self.tok >> 4) & 0x3}
        return {0, 1, 2, 3}

    def swizzle_map(self, dest_comp: int) -> int:
        """For component-wise ops: which source component feeds dest_comp."""
        mode = self.comp_mode()
        if mode == 1:
            sw = (self.tok >> 4) & 0xFF
            return (sw >> (2 * dest_comp)) & 0x3
        if mode == 2:
            return (self.tok >> 4) & 0x3
        # mask-mode source (unusual) or n/a: aligned
        return dest_comp

    def reg_index(self):
        """First index dim if immediate (register number for 1D operands)."""
        if self.dims and self.dims[0][0] == 'imm':
            return self.dims[0][1]
        return None

    def all_nested(self):
        out = []
        for d in self.dims:
            if d[0] == 'rel':
                out.append(d[1])
                out.extend(d[1].all_nested())
            elif d[0] == 'immrel':
                out.append(d[2])
                out.extend(d[2].all_nested())
        return out


@dataclass
class Instr:
    index: int              # instruction ordinal
    pos: int                # dword offset in SHEX (incl. version/length hdr)
    length: int             # dwords
    opcode: int
    tok: int
    ext_toks: List[int]     # extended opcode token chain
    operands: List[Operand]
    raw: List[int]
    parse_ok: bool
    is_decl: bool
    nesting: tuple = ()     # control-flow nesting path, filled by annotate_nesting

    def n_dests(self):
        if self.opcode in ZERO_DEST_OPCODES or self.is_decl:
            return 0
        if self.opcode in TWO_DEST_OPCODES:
            return 2
        return 1

    def dest_operands(self):
        n = self.n_dests()
        return self.operands[:n] if self.parse_ok else []

    def src_operands(self):
        n = self.n_dests()
        return self.operands[n:] if self.parse_ok else []


def parse_operand(dw, j, end) -> Optional[Operand]:
    if j >= end:
        return None
    tok = dw[j]
    ext_toks = []
    k = j + 1
    t = tok
    while (t >> 31) & 1:
        if k >= end:
            return None
        ext_toks.append(dw[k])
        t = dw[k]
        k += 1
    typ = (tok >> 12) & 0xFF
    dims = []
    imm_vals = []
    if typ == OT_IMM32 or typ == OT_IMM64:
        nc = tok & 0x3
        n = 4 if nc == 2 else 1
        if typ == OT_IMM64:
            n *= 2
        if k + n > end:
            return None
        imm_vals = [dw[k + i] for i in range(n)]
        k += n
        idxdim = 0
    else:
        idxdim = (tok >> 20) & 0x3
        for i in range(idxdim):
            rep = (tok >> (22 + 3 * i)) & 0x7
            if rep == 0:
                if k >= end:
                    return None
                dims.append(('imm', dw[k])); k += 1
            elif rep == 1:
                if k + 1 >= end:
                    return None
                dims.append(('imm', dw[k]))  # low dword of imm64 index
                k += 2
            elif rep == 2:
                sub = parse_operand(dw, k, end)
                if sub is None:
                    return None
                dims.append(('rel', sub)); k = sub.end
            elif rep == 3:
                if k >= end:
                    return None
                imm = dw[k]; k += 1
                sub = parse_operand(dw, k, end)
                if sub is None:
                    return None
                dims.append(('immrel', imm, sub)); k = sub.end
            else:
                return None
    return Operand(start=j, end=k, tok=tok, ext_toks=ext_toks, typ=typ,
                   idxdim=idxdim, dims=dims, imm_vals=imm_vals)


def parse_instructions(data: bytes, shex_off: int, shex_size: int) -> Tuple[List[Instr], int]:
    """Parse the whole SHEX stream into Instr objects. Returns
    (instructions, declared_dword_count). Instructions of declaration
    opcodes keep raw dwords only (their tails are not pure operand lists)."""
    dword_count = shex_size // 4
    dw = list(struct.unpack_from('<%dI' % dword_count, data, shex_off))
    declared = dw[1] if dword_count >= 2 else dword_count
    out = []
    pos = 2
    idx = 0
    while pos < dword_count:
        tok = dw[pos]
        opcode = tok & 0x7FF
        if opcode == OP_CUSTOMDATA:
            length = dw[pos + 1] if pos + 1 < dword_count else 2
            if length < 2:
                length = 2
        else:
            length = (tok >> 24) & 0x7F
            if length == 0:
                length = 1
        if pos + length > dword_count:
            break
        raw = dw[pos:pos + length]
        is_decl = opcode in DECL_OPCODES
        operands = []
        parse_ok = True
        ext_toks = []
        if not is_decl:
            k = pos + 1
            t = tok
            while (t >> 31) & 1 and k < pos + length:
                ext_toks.append(dw[k])
                t = dw[k]
                k += 1
            end = pos + length
            while k < end:
                op = parse_operand(dw, k, end)
                if op is None:
                    parse_ok = False
                    break
                operands.append(op)
                k = op.end
            if parse_ok and k != end:
                parse_ok = False
        out.append(Instr(index=idx, pos=pos, length=length, opcode=opcode,
                         tok=tok, ext_toks=ext_toks, operands=operands,
                         raw=raw, parse_ok=parse_ok, is_decl=is_decl))
        idx += 1
        pos += length
    return out, declared


def annotate_nesting(instrs: List[Instr]):
    """Fill Instr.nesting with the control-flow nesting path (tuple of
    (open_instr_index, kind, arm)). An instruction's path is the stack of
    enclosing if/loop/switch constructs; ELSE/CASE/DEFAULT bump the arm so
    the two arms of a construct never compare equal. Paths are SNAPSHOTTED
    at each instruction (deep-copied) so later arm bumps cannot alias into
    already-recorded paths."""
    stack = []

    def snap(s):
        return tuple(tuple(x) for x in s)

    for ins in instrs:
        op = ins.opcode
        if op == OP_IF:
            ins.nesting = snap(stack)
            stack.append([ins.index, 'if', 0])
        elif op == OP_LOOP:
            ins.nesting = snap(stack)
            stack.append([ins.index, 'loop', 0])
        elif op == OP_SWITCH:
            ins.nesting = snap(stack)
            stack.append([ins.index, 'switch', 0])
        elif op in (OP_ELSE, OP_CASE, OP_DEFAULT):
            if stack:
                stack[-1][2] += 1
            ins.nesting = snap(stack[:-1])
        elif op in (OP_ENDIF, OP_ENDLOOP, OP_ENDSWITCH):
            if stack:
                stack.pop()
            ins.nesting = snap(stack)
        else:
            ins.nesting = snap(stack)


def nesting_is_prefix(defn: tuple, use: tuple) -> bool:
    if len(defn) > len(use):
        return False
    return all(defn[i] == use[i] for i in range(len(defn)))


def reads_of(ins: Instr):
    """Yield (reg, comp) pairs of TEMP registers this instruction may read,
    conservatively. Unknown/unparsed instructions are handled by the caller
    (treat everything as read)."""
    if not ins.parse_ok:
        return
    n = ins.n_dests()
    for oi, op in enumerate(ins.operands):
        nested = op.all_nested()
        # nested index operands are always reads
        for sub in nested:
            if sub.typ == OT_TEMP and sub.reg_index() is not None:
                for c in sub.read_comps():
                    yield (sub.reg_index(), c)
        if oi < n:
            continue  # top-level dest: not a read
        if op.typ == OT_TEMP and op.reg_index() is not None:
            for c in op.read_comps():
                yield (op.reg_index(), c)


def writes_of(ins: Instr):
    """Yield (reg, comps_set) for TEMP dests."""
    if not ins.parse_ok:
        return
    for op in ins.dest_operands():
        if op.typ == OT_TEMP and op.reg_index() is not None:
            yield (op.reg_index(), op.write_comps())


# ============================================================================
# Stream rebuild
# ============================================================================

def get_shex_raw(data: bytes):
    """Returns (raw_chunk_offset, data_offset, size) for SHEX or SHDR."""
    for name in ("SHEX", "SHDR"):
        raw = dxbc.find_chunk_raw(data, name)
        if raw is not None:
            size = struct.unpack_from('<I', data, raw + 4)[0]
            return raw, raw + 8, size
    return None, None, 0


def splice_shex(blob: bytes, new_dwords: List[int]) -> Tuple[bytes, bool]:
    """Write new_dwords back as the SHEX payload. If the dword count is
    unchanged, splice in place (size-preserving). Otherwise rebuild the
    whole container (chunk offsets, chunk size, total size) -- the blob's
    byte length changes and the caller must handle that (pak patchers skip
    in-place writes for these).

    Returns (new_blob, size_changed). Does NOT re-sign the hash."""
    raw, doff, size = get_shex_raw(blob)
    assert raw is not None, "no SHEX/SHDR chunk"
    old_count = size // 4
    new_count = len(new_dwords)
    payload = struct.pack('<%dI' % new_count, *new_dwords)

    if new_count == old_count:
        out = bytearray(blob)
        out[doff:doff + size] = payload
        return bytes(out), False

    # --- container rebuild ---
    chunk_count = struct.unpack_from('<I', blob, 28)[0]
    offsets = [struct.unpack_from('<I', blob, 32 + i * 4)[0] for i in range(chunk_count)]
    chunks = []
    for off in offsets:
        fourcc = blob[off:off + 4]
        csize = struct.unpack_from('<I', blob, off + 4)[0]
        cdata = blob[off + 8:off + 8 + csize]
        chunks.append([fourcc, cdata])
    for ch in chunks:
        if ch[0] in (b'SHEX', b'SHDR'):
            ch[1] = payload
    header_size = 32 + 4 * chunk_count
    out = bytearray()
    out += b'DXBC'
    out += bytes(16)                    # hash placeholder
    out += struct.pack('<I', 1)         # version
    out += struct.pack('<I', 0)         # total size placeholder
    out += struct.pack('<I', chunk_count)
    cur = header_size
    offs = []
    body = bytearray()
    for fourcc, cdata in chunks:
        offs.append(cur)
        body += fourcc
        body += struct.pack('<I', len(cdata))
        body += cdata
        cur += 8 + len(cdata)
    for o in offs:
        out += struct.pack('<I', o)
    out += body
    struct.pack_into('<I', out, 24, len(out))
    return bytes(out), True


def rebuild_stream(instrs: List[Instr], header: List[int],
                   emissions: dict) -> List[int]:
    """Reassemble the SHEX dword list. emissions maps instr.index ->
    replacement dword list (already correct instruction encodings, may be
    empty to delete). Unmapped instructions are emitted verbatim."""
    out = list(header)
    for ins in instrs:
        if ins.index in emissions:
            out.extend(emissions[ins.index])
        else:
            out.extend(ins.raw)
    return out


def set_instruction_length(tok: int, length: int) -> int:
    return (tok & ~(0x7F << 24)) | ((length & 0x7F) << 24)


def validate_shex(blob: bytes) -> Tuple[bool, str]:
    """Token-walker length invariant: the instruction walk must land exactly
    on the declared dword count, and the SHEX length token must equal the
    chunk size."""
    raw, doff, size = get_shex_raw(blob)
    if raw is None:
        return False, "no SHEX chunk"
    count = size // 4
    dw = struct.unpack_from('<%dI' % count, blob, doff)
    if dw[1] != count:
        return False, f"SHEX length token {dw[1]} != chunk dwords {count}"
    pos = 2
    while pos < count:
        tok = dw[pos]
        opcode = tok & 0x7FF
        if opcode == OP_CUSTOMDATA:
            length = dw[pos + 1]
            if length < 2:
                return False, f"bad customdata length at {pos}"
        else:
            length = (tok >> 24) & 0x7F
            if length == 0:
                return False, f"zero-length instruction at {pos}"
        pos += length
    if pos != count:
        return False, f"walk landed at {pos}, declared {count}"
    return True, "ok"
