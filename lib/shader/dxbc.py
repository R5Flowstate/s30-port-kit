"""
DXBC (DirectX Bytecode) parser and manipulation.

Handles chunk finding, instruction iteration, operand parsing,
and the custom DXBC hash algorithm used by DirectX shader containers.
"""

import struct
import ctypes
from typing import Optional

# ============================================================================
# DXBC Opcodes
# ============================================================================

OPCODE_ADD           = 0x00
OPCODE_AND           = 0x01
OPCODE_DIV           = 0x0E
OPCODE_DP3           = 0x10
OPCODE_DP4           = 0x11
OPCODE_ELSE          = 0x12
OPCODE_ENDIF         = 0x15
OPCODE_ENDLOOP       = 0x16
OPCODE_IF            = 0x1F
OPCODE_LD_INDEXABLE   = 0x2D
OPCODE_LOOP          = 0x30
OPCODE_LT            = 0x31
OPCODE_MAD           = 0x32
OPCODE_MAX           = 0x33
OPCODE_MOV           = 0x36
OPCODE_MOVC          = 0x37
OPCODE_MUL           = 0x38
OPCODE_NOP           = 0x3A  # Actually not a real DXBC NOP; we use ADD identity
OPCODE_RCP           = 0x42
OPCODE_SAMPLE_INDEXABLE = 0x45
OPCODE_CUSTOMDATA    = 0x35
OPCODE_UBFE          = 0x55
OPCODE_IBFE          = 0x56
OPCODE_DCL_CONSTANT_BUFFER = 0x59
OPCODE_DCL_RESOURCE  = 0x58
OPCODE_USHR          = 0x5E
OPCODE_UTOF          = 0x5F
OPCODE_SWAPC         = 0xCE

# Declaration opcodes (don't modify these for output redirects)
OPCODE_DCL_OUTPUT     = 0x5F  # NOTE: collides with UTOF in opcode space but context differs
OPCODE_DCL_OUTPUT_SIV = 0x65
OPCODE_DCL_OUTPUT_SGV = 0x66

# Operand types
OPERAND_TYPE_TEMP             = 0
OPERAND_TYPE_INPUT            = 1
OPERAND_TYPE_OUTPUT           = 2
OPERAND_TYPE_IMMEDIATE32      = 4
OPERAND_TYPE_RESOURCE          = 7
OPERAND_TYPE_CONSTANT_BUFFER  = 8

# Shader types (from SHEX/SHDR version token bits 16-19)
SHADER_TYPE_PIXEL    = 0
SHADER_TYPE_VERTEX   = 1
SHADER_TYPE_GEOMETRY = 2
SHADER_TYPE_HULL     = 3
SHADER_TYPE_DOMAIN   = 4
SHADER_TYPE_COMPUTE  = 5

SHADER_TYPE_NAMES = {
    0: "PS", 1: "VS", 2: "GS", 3: "HS", 4: "DS", 5: "CS"
}

# NOP instruction: opcode 0x3A does NOT exist in real DXBC -- AMD rejects it.
# Zero S7 shaders use it. We avoid NOPs entirely by setting the replacement MOV
# instruction's length field to cover the full original instruction span.
# The D3D runtime reads exactly `length` dwords for each instruction, so
# trailing zeros after the MOV operands are ignored (they're within the MOV's
# declared length). We keep this constant for the _nop_fill helper which now
# fills with zeros (safe padding within an oversized instruction).
NOP_DWORD = 0x00000000


# ============================================================================
# DXBC Header Layout
# ============================================================================
# Offset  Size  Field
# 0       4     Magic "DXBC"
# 4       16    Hash (custom MD5-like)
# 20      4     Version (always 1)
# 24      4     Total size
# 28      4     Chunk count
# 32+     4*N   Chunk offsets
# ============================================================================


def get_opcode(token: int) -> int:
    """Extract opcode from instruction token (bits 0-10)."""
    return token & 0x7FF


def get_instruction_length(token: int) -> int:
    """Extract instruction length from token (bits 24-30, 7 bits)."""
    return (token >> 24) & 0x7F


def is_extended(token: int) -> bool:
    """Check if instruction/operand has extended token (bit 31)."""
    return bool((token >> 31) & 1)


def get_operand_type(token: int) -> int:
    """Extract operand type from operand token (bits 12-19)."""
    return (token >> 12) & 0xFF


def get_operand_type_4bit(token: int) -> int:
    """Extract operand type from operand token (bits 12-15 only, for type < 16)."""
    return (token >> 12) & 0xF


def get_component_mode(token: int) -> int:
    """Get component selection mode (bits 2-3): 0=mask, 1=swizzle, 2=select1."""
    return (token >> 2) & 0x3


def get_component_mask(token: int) -> int:
    """Get 4-bit component mask (bits 4-7): x=1, y=2, z=4, w=8."""
    return (token >> 4) & 0xF


def get_swizzle(token: int) -> int:
    """Get 8-bit swizzle (bits 4-11)."""
    return (token >> 4) & 0xFF


def get_index_dimension(token: int) -> int:
    """Get operand index dimension (bits 20-21): 0=none, 1=1D, 2=2D, 3=3D."""
    return (token >> 20) & 0x3


def is_w_component(token: int) -> bool:
    """Check if operand selects/masks the W component."""
    mode = get_component_mode(token)
    if mode == 0:  # mask
        return get_component_mask(token) == 0x8
    elif mode == 1:  # swizzle
        swiz = get_swizzle(token)
        first = swiz & 0x3
        return swiz == 0xFF or first == 3
    elif mode == 2:  # select1
        return ((token >> 4) & 0x3) == 3
    return False


def get_operand_size(token: int) -> int:
    """Calculate operand size in DWORDs from the operand token."""
    size = 1  # base token

    # Extended operand
    if is_extended(token):
        size += 1

    op_type = get_operand_type(token)

    # Immediate operands (type 4)
    if op_type == OPERAND_TYPE_IMMEDIATE32:
        comp_bits = token & 0x3
        if comp_bits == 2:  # 4-component
            size += 4
        else:  # 0 or 1 = scalar
            size += 1
        return size

    # Index dimensions
    idx_dim = get_index_dimension(token)
    for i in range(idx_dim):
        idx_rep = (token >> (22 + i * 3)) & 0x7
        if idx_rep == 0:    # immediate 32-bit
            size += 1
        elif idx_rep == 1:  # immediate 64-bit
            size += 2
        elif idx_rep == 2:  # relative
            size += 1
        elif idx_rep == 3:  # immediate + relative
            size += 2

    return size


def skip_extended_tokens(dwords: list, pos: int, inst_end: int) -> int:
    """Skip past extended opcode tokens to reach the first operand."""
    cur = pos + 1
    if is_extended(dwords[pos]):
        while cur < inst_end and is_extended(dwords[cur]):
            cur += 1
        cur += 1  # skip last extended (without bit 31)
    return cur


# ============================================================================
# Chunk Operations
# ============================================================================

def find_chunk(data: bytes, fourcc: str) -> Optional[int]:
    """Find a DXBC chunk by FourCC, returns offset to chunk data (after header).
    Returns None if not found."""
    if len(data) < 32 or data[:4] != b'DXBC':
        return None

    chunk_count = struct.unpack_from('<I', data, 28)[0]
    fourcc_bytes = fourcc.encode('ascii')

    for i in range(chunk_count):
        if 32 + i * 4 + 4 > len(data):
            break
        offset = struct.unpack_from('<I', data, 32 + i * 4)[0]
        if offset + 8 > len(data):
            continue
        if data[offset:offset + 4] == fourcc_bytes:
            return offset + 8  # skip fourcc + size
    return None


def find_chunk_raw(data: bytes, fourcc: str) -> Optional[int]:
    """Find chunk, returns offset to the fourcc itself (not data)."""
    if len(data) < 32 or data[:4] != b'DXBC':
        return None

    chunk_count = struct.unpack_from('<I', data, 28)[0]
    fourcc_bytes = fourcc.encode('ascii')

    for i in range(chunk_count):
        if 32 + i * 4 + 4 > len(data):
            break
        offset = struct.unpack_from('<I', data, 32 + i * 4)[0]
        if offset + 8 > len(data):
            continue
        if data[offset:offset + 4] == fourcc_bytes:
            return offset
    return None


def get_chunk_size(data: bytes, fourcc: str) -> int:
    """Get size of a DXBC chunk's data."""
    raw = find_chunk_raw(data, fourcc)
    if raw is None:
        return 0
    return struct.unpack_from('<I', data, raw + 4)[0]


def get_shex_data(data: bytes):
    """Get SHEX or SHDR chunk offset and size. Returns (offset, size) or (None, 0)."""
    for name in ("SHEX", "SHDR"):
        off = find_chunk(data, name)
        sz = get_chunk_size(data, name)
        if off is not None and sz >= 8:
            return off, sz
    return None, 0


def get_shader_type(data: bytes) -> int:
    """Detect shader type from SHEX/SHDR version token. Returns SHADER_TYPE_* or -1."""
    off, sz = get_shex_data(data)
    if off is None:
        return -1
    version_token = struct.unpack_from('<I', data, off)[0]
    return (version_token >> 16) & 0xF


def get_shader_type_name(data: bytes) -> str:
    """Get shader type as string (PS/VS/GS/etc)."""
    return SHADER_TYPE_NAMES.get(get_shader_type(data), "??")


# ============================================================================
# Instruction Iterator
# ============================================================================

def iter_instructions(data: bytes, shex_offset: int, shex_size: int):
    """Iterate over SHEX/SHDR instructions.

    Yields (pos, opcode, inst_length, dwords_view) where:
        pos = instruction position in dwords (relative to shex start)
        opcode = instruction opcode
        inst_length = total instruction length in dwords
        dwords_view = memoryview of all shex dwords (as uint32 array)
    """
    dword_count = shex_size // 4
    # Build a mutable uint32 array view
    dwords = (ctypes.c_uint32 * dword_count).from_buffer_copy(
        data[shex_offset:shex_offset + dword_count * 4]
    )

    pos = 2  # skip version + length tokens
    while pos < dword_count:
        token = dwords[pos]
        opcode = get_opcode(token)

        if opcode == OPCODE_CUSTOMDATA:
            inst_len = dwords[pos + 1] if pos + 1 < dword_count else 2
            if inst_len == 0:
                inst_len = 2
        else:
            inst_len = get_instruction_length(token)
            if inst_len == 0:
                inst_len = 1

        if pos + inst_len > dword_count:
            break

        yield pos, opcode, inst_len, dwords

        pos += inst_len


def get_dwords(data: bytearray, shex_offset: int, shex_size: int):
    """Get mutable uint32 view into SHEX data. Returns (array, count)."""
    dword_count = shex_size // 4
    # Create a ctypes array backed by the bytearray
    ArrayType = ctypes.c_uint32 * dword_count
    arr = ArrayType.from_buffer(data, shex_offset)
    return arr, dword_count


# ============================================================================
# DXBC Hash Algorithm
# Custom MD5-like hash used by DirectX Bytecode containers.
# Ported from HLSLDecompiler/Assembler.cpp via the C++ MSWUnPacker.
# ============================================================================

def _rotl32(x: int, n: int) -> int:
    """32-bit left rotate."""
    x &= 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _rotr32(x: int, n: int) -> int:
    """32-bit right rotate."""
    x &= 0xFFFFFFFF
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def compute_hash(input_bytes: bytes) -> bytes:
    """Compute DXBC hash over the given data. Returns 16 bytes."""
    size = len(input_bytes)
    size_hash = size & 0x3F
    size_hash_56 = size_hash >= 56
    rest_size = (120 - 56) if size_hash_56 else (56 - size_hash)
    loop_size = (size + 8 + rest_size) >> 6

    # Padding data: starts with 0x80, rest zeros
    pad_data = bytearray(64)
    pad_data[0] = 0x80

    loop_size_2 = loop_size - (2 if size_hash_56 else 1)

    # Convert input to uint32 array (little-endian, padded)
    padded = input_bytes + b'\x00' * 256
    src_offset = 0
    processed = 0

    h = [0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476]

    for i in range(loop_size):
        if i == loop_size_2:
            dst = [0] * 16
            if not size_hash_56:
                dst[0] = _u32(size << 3)
                rem = size - processed
                # Copy remaining input
                rem_data = input_bytes[processed:processed + rem]
                for j in range(rem // 4):
                    dst[1 + j] = struct.unpack_from('<I', rem_data, j * 4)[0]
                # Handle partial dword
                partial_off = (rem // 4) * 4
                if partial_off < rem:
                    partial = rem_data[partial_off:] + b'\x00' * 4
                    dst[1 + rem // 4] = struct.unpack_from('<I', partial, 0)[0]
                # Copy padding
                pad_off = 0
                for j in range((1 + (rem + 3) // 4), 15):
                    if pad_off < len(pad_data):
                        dst[j] = struct.unpack_from('<I', pad_data, pad_off)[0] if pad_off + 4 <= len(pad_data) else 0
                        pad_off += 4
                # Overwrite with padding starting right after data
                rest_start = 1 + (rem + 3) // 4
                pad_idx = rem % 4
                if pad_idx == 0:
                    dst[rest_start] = 0x80
                else:
                    # Merge partial byte with 0x80
                    existing = dst[1 + rem // 4]
                    existing |= 0x80 << (pad_idx * 8)
                    dst[1 + rem // 4] = _u32(existing)
                dst[15] = _u32((size * 2) | 1)
                pSrc = dst
            else:
                rem = size - processed
                rem_data = input_bytes[processed:processed + rem]
                for j in range(rem // 4):
                    dst[j] = struct.unpack_from('<I', rem_data, j * 4)[0]
                partial_off = (rem // 4) * 4
                if partial_off < rem:
                    partial = rem_data[partial_off:] + b'\x00' * 4
                    dst[rem // 4] = struct.unpack_from('<I', partial, 0)[0]
                rest_start = (rem + 3) // 4
                pad_idx = rem % 4
                if pad_idx == 0:
                    dst[rest_start] = 0x80
                else:
                    existing = dst[rem // 4]
                    existing |= 0x80 << (pad_idx * 8)
                    dst[rem // 4] = _u32(existing)
                pSrc = dst
        elif i > loop_size_2:
            dst = [0] * 16
            dst[0] = _u32(size << 3)
            dst[15] = _u32((size * 2) | 1)
            pSrc = dst
        else:
            # Normal block from input
            pSrc = []
            for j in range(16):
                off = src_offset + j * 4
                pSrc.append(struct.unpack_from('<I', padded, off)[0])
            src_offset += 64

        # MD5-like rounds
        edx, ebx, edi, esi = h[0], h[1], h[2], h[3]

        # Round 1
        edx = _u32(_rotl32(_u32((_u32(~ebx) & esi | ebx & edi) + pSrc[0] + 0xD76AA478 + edx), 7) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edx) & edi | edx & ebx) + pSrc[1] + 0xE8C7B756 + esi), 12) + edx)
        edi = _u32(_rotr32(_u32((_u32(~esi) & ebx | esi & edx) + pSrc[2] + 0x242070DB + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edi) & edx | edi & esi) + pSrc[3] + 0xC1BDCEEE + ebx), 10) + edi)
        edx = _u32(_rotl32(_u32((_u32(~ebx) & esi | ebx & edi) + pSrc[4] + 0xF57C0FAF + edx), 7) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edx) & edi | ebx & edx) + pSrc[5] + 0x4787C62A + esi), 12) + edx)
        edi = _u32(_rotr32(_u32((_u32(~esi) & ebx | esi & edx) + pSrc[6] + 0xA8304613 + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edi) & edx | edi & esi) + pSrc[7] + 0xFD469501 + ebx), 10) + edi)
        edx = _u32(_rotl32(_u32((_u32(~ebx) & esi | ebx & edi) + pSrc[8] + 0x698098D8 + edx), 7) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edx) & edi | ebx & edx) + pSrc[9] + 0x8B44F7AF + esi), 12) + edx)
        edi = _u32(_rotr32(_u32((_u32(~esi) & ebx | esi & edx) + pSrc[10] + 0xFFFF5BB1 + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edi) & edx | edi & esi) + pSrc[11] + 0x895CD7BE + ebx), 10) + edi)
        edx = _u32(_rotl32(_u32((_u32(~ebx) & esi | ebx & edi) + pSrc[12] + 0x6B901122 + edx), 7) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edx) & edi | ebx & edx) + pSrc[13] + 0xFD987193 + esi), 12) + edx)
        edi = _u32(_rotr32(_u32((_u32(~esi) & ebx | esi & edx) + pSrc[14] + 0xA679438E + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edi) & edx | edi & esi) + pSrc[15] + 0x49B40821 + ebx), 10) + edi)

        # Round 2
        edx = _u32(_rotl32(_u32((_u32(~esi) & edi | esi & ebx) + pSrc[1] + 0xF61E2562 + edx), 5) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edi) & ebx | edi & edx) + pSrc[6] + 0xC040B340 + esi), 9) + edx)
        edi = _u32(_rotl32(_u32((_u32(~ebx) & edx | ebx & esi) + pSrc[11] + 0x265E5A51 + edi), 14) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edx) & esi | edx & edi) + pSrc[0] + 0xE9B6C7AA + ebx), 12) + edi)
        edx = _u32(_rotl32(_u32((_u32(~esi) & edi | esi & ebx) + pSrc[5] + 0xD62F105D + edx), 5) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edi) & ebx | edi & edx) + pSrc[10] + 0x02441453 + esi), 9) + edx)
        edi = _u32(_rotl32(_u32((_u32(~ebx) & edx | ebx & esi) + pSrc[15] + 0xD8A1E681 + edi), 14) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edx) & esi | edx & edi) + pSrc[4] + 0xE7D3FBC8 + ebx), 12) + edi)
        edx = _u32(_rotl32(_u32((_u32(~esi) & edi | esi & ebx) + pSrc[9] + 0x21E1CDE6 + edx), 5) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edi) & ebx | edi & edx) + pSrc[14] + 0xC33707D6 + esi), 9) + edx)
        edi = _u32(_rotl32(_u32((_u32(~ebx) & edx | ebx & esi) + pSrc[3] + 0xF4D50D87 + edi), 14) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edx) & esi | edx & edi) + pSrc[8] + 0x455A14ED + ebx), 12) + edi)
        edx = _u32(_rotl32(_u32((_u32(~esi) & edi | esi & ebx) + pSrc[13] + 0xA9E3E905 + edx), 5) + ebx)
        esi = _u32(_rotl32(_u32((_u32(~edi) & ebx | edi & edx) + pSrc[2] + 0xFCEFA3F8 + esi), 9) + edx)
        edi = _u32(_rotl32(_u32((_u32(~ebx) & edx | ebx & esi) + pSrc[7] + 0x676F02D9 + edi), 14) + esi)
        ebx = _u32(_rotr32(_u32((_u32(~edx) & esi | edx & edi) + pSrc[12] + 0x8D2A4C8A + ebx), 12) + edi)

        # Round 3
        edx = _u32(_rotl32(_u32((esi ^ edi ^ ebx) + pSrc[5] + 0xFFFA3942 + edx), 4) + ebx)
        esi = _u32(_rotl32(_u32((edi ^ ebx ^ edx) + pSrc[8] + 0x8771F681 + esi), 11) + edx)
        edi = _u32(_rotl32(_u32((ebx ^ edx ^ esi) + pSrc[11] + 0x6D9D6122 + edi), 16) + esi)
        ebx = _u32(_rotr32(_u32((edx ^ esi ^ edi) + pSrc[14] + 0xFDE5380C + ebx), 9) + edi)
        edx = _u32(_rotl32(_u32((esi ^ edi ^ ebx) + pSrc[1] + 0xA4BEEA44 + edx), 4) + ebx)
        esi = _u32(_rotl32(_u32((edi ^ ebx ^ edx) + pSrc[4] + 0x4BDECFA9 + esi), 11) + edx)
        edi = _u32(_rotl32(_u32((ebx ^ edx ^ esi) + pSrc[7] + 0xF6BB4B60 + edi), 16) + esi)
        ebx = _u32(_rotr32(_u32((edx ^ esi ^ edi) + pSrc[10] + 0xBEBFBC70 + ebx), 9) + edi)
        edx = _u32(_rotl32(_u32((esi ^ edi ^ ebx) + pSrc[13] + 0x289B7EC6 + edx), 4) + ebx)
        esi = _u32(_rotl32(_u32((edi ^ ebx ^ edx) + pSrc[0] + 0xEAA127FA + esi), 11) + edx)
        edi = _u32(_rotl32(_u32((ebx ^ edx ^ esi) + pSrc[3] + 0xD4EF3085 + edi), 16) + esi)
        ebx = _u32(_rotr32(_u32((edx ^ esi ^ edi) + pSrc[6] + 0x04881D05 + ebx), 9) + edi)
        edx = _u32(_rotl32(_u32((esi ^ edi ^ ebx) + pSrc[9] + 0xD9D4D039 + edx), 4) + ebx)
        esi = _u32(_rotl32(_u32((edi ^ ebx ^ edx) + pSrc[12] + 0xE6DB99E5 + esi), 11) + edx)
        edi = _u32(_rotl32(_u32((ebx ^ edx ^ esi) + pSrc[15] + 0x1FA27CF8 + edi), 16) + esi)
        ebx = _u32(_rotr32(_u32((edx ^ esi ^ edi) + pSrc[2] + 0xC4AC5665 + ebx), 9) + edi)

        # Round 4
        edx = _u32(_rotl32(_u32(((_u32(~esi) | ebx) ^ edi) + pSrc[0] + 0xF4292244 + edx), 6) + ebx)
        esi = _u32(_rotl32(_u32(((_u32(~edi) | edx) ^ ebx) + pSrc[7] + 0x432AFF97 + esi), 10) + edx)
        edi = _u32(_rotl32(_u32(((_u32(~ebx) | esi) ^ edx) + pSrc[14] + 0xAB9423A7 + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32(((_u32(~edx) | edi) ^ esi) + pSrc[5] + 0xFC93A039 + ebx), 11) + edi)
        edx = _u32(_rotl32(_u32(((_u32(~esi) | ebx) ^ edi) + pSrc[12] + 0x655B59C3 + edx), 6) + ebx)
        esi = _u32(_rotl32(_u32(((_u32(~edi) | edx) ^ ebx) + pSrc[3] + 0x8F0CCC92 + esi), 10) + edx)
        edi = _u32(_rotl32(_u32(((_u32(~ebx) | esi) ^ edx) + pSrc[10] + 0xFFEFF47D + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32(((_u32(~edx) | edi) ^ esi) + pSrc[1] + 0x85845DD1 + ebx), 11) + edi)
        edx = _u32(_rotl32(_u32(((_u32(~esi) | ebx) ^ edi) + pSrc[8] + 0x6FA87E4F + edx), 6) + ebx)
        esi = _u32(_rotl32(_u32(((_u32(~edi) | edx) ^ ebx) + pSrc[15] + 0xFE2CE6E0 + esi), 10) + edx)
        edi = _u32(_rotl32(_u32(((_u32(~ebx) | esi) ^ edx) + pSrc[6] + 0xA3014314 + edi), 15) + esi)
        ebx = _u32(_rotr32(_u32(((_u32(~edx) | edi) ^ esi) + pSrc[13] + 0x4E0811A1 + ebx), 11) + edi)
        edx = _u32(_rotl32(_u32(((_u32(~esi) | ebx) ^ edi) + pSrc[4] + 0xF7537E82 + edx), 6) + ebx)
        h[0] = _u32(h[0] + edx)
        esi = _u32(_rotl32(_u32(((_u32(~edi) | edx) ^ ebx) + pSrc[11] + 0xBD3AF235 + esi), 10) + edx)
        h[3] = _u32(h[3] + esi)
        edi = _u32(_rotl32(_u32(((_u32(~ebx) | esi) ^ edx) + pSrc[2] + 0x2AD7D2BB + edi), 15) + esi)
        h[2] = _u32(h[2] + edi)
        ebx = _u32(_rotr32(_u32(((_u32(~edx) | edi) ^ esi) + pSrc[9] + 0xEB86D391 + ebx), 11) + edi)
        h[1] = _u32(h[1] + ebx)

        processed += 0x40

    return struct.pack('<4I', h[0], h[1], h[2], h[3])


def update_hash(data: bytearray) -> None:
    """Recalculate and write DXBC hash in-place."""
    if len(data) < 20:
        return
    h = compute_hash(bytes(data[20:]))
    data[4:20] = h


def verify_hash(data: bytes) -> bool:
    """Verify the DXBC hash is correct."""
    if len(data) < 20:
        return False
    expected = compute_hash(data[20:])
    return data[4:20] == expected
