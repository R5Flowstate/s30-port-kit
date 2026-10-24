"""
MSW (MultiShaderWrapper) binary format reader/writer.

Format: MSW v3 (as defined in r5sdk/rsx multishader.h)

Shader file layout:
  [Header 5B]  magic(3) + version(1) + fileType(1)
  [Shader_t 16B]  shaderFeatures(7B:56bit) + numDescriptors(1B:8bit) + nameLength(4B) + nameOffset(4B)
  [ShaderDesc_t[] 24B each]  {bufferLength(4) + bufferOffset(4)} or {bufferIndex(4) + 0(4)} + inputFlags(16)
  [Name string]  null-terminated shader name
  [DXBC blobs]  shader bytecode data
"""

import struct
import os
from dataclasses import dataclass, field
from typing import List, Optional


MSW_MAGIC = b'MSW'
MSW_VERSION = 3

# File types
FILETYPE_SHADER = 0
FILETYPE_SHADERSET = 1


@dataclass
class ShaderEntry:
    """A single shader descriptor entry within an MSW."""
    flags: tuple  # (inputFlags[0], inputFlags[1]) - two uint64s
    data: Optional[bytearray] = None  # DXBC bytecode (None if reference)
    ref_index: int = 0xFFFF  # Reference to another entry's data (0xFFFF = not a ref)

    @property
    def is_reference(self) -> bool:
        return self.data is None and self.ref_index != 0xFFFF


@dataclass
class ShaderMSW:
    """Parsed MSW shader file."""
    # Header
    version: int = MSW_VERSION
    file_type: int = FILETYPE_SHADER

    # Shader metadata
    shader_type: int = 0xFF  # 0=PS, 1=VS, ..., 0xFF=invalid
    features: bytes = b'\x00' * 7  # 7 feature bytes
    name: str = ""

    # Entries
    entries: List[ShaderEntry] = field(default_factory=list)


def read_msw(filepath: str) -> ShaderMSW:
    """Read an MSW file from disk. Returns parsed ShaderMSW."""
    with open(filepath, 'rb') as f:
        raw = f.read()
    return parse_msw(raw)


def parse_msw(raw: bytes) -> ShaderMSW:
    """Parse MSW binary data."""
    msw = ShaderMSW()

    if len(raw) < 5:
        raise ValueError(f"MSW too small: {len(raw)} bytes")

    # Header: magic(3) + version(1) + fileType(1)
    if raw[0:3] != MSW_MAGIC:
        raise ValueError(f"Bad MSW magic: {raw[0:3]}")

    msw.version = raw[3]
    msw.file_type = raw[4]

    if msw.file_type != FILETYPE_SHADER:
        raise ValueError(f"Only shader MSW supported, got fileType={msw.file_type}")

    # Shader_t at offset 5:
    # shaderFeatures(56bit) + numDescriptors(8bit) = 8 bytes total (packed as uint64 LE)
    # nameLength(4B) + nameOffset(4B)
    if len(raw) < 5 + 16:
        raise ValueError("MSW too small for Shader_t header")

    features_and_count = struct.unpack_from('<Q', raw, 5)[0]
    features_56bit = features_and_count & 0x00FFFFFFFFFFFFFF
    num_descriptors = (features_and_count >> 56) & 0xFF

    # Extract 7 feature bytes from the 56-bit field
    msw.features = struct.pack('<Q', features_56bit)[:7]

    name_length = struct.unpack_from('<I', raw, 13)[0]
    name_offset = struct.unpack_from('<I', raw, 17)[0]

    # Read name
    name_abs = 5 + name_offset  # name_offset is relative to Shader_t start (offset 5)
    if name_abs + name_length <= len(raw) and name_length > 0:
        msw.name = raw[name_abs:name_abs + name_length].rstrip(b'\x00').decode('ascii', errors='replace')

    # ShaderDesc_t array starts at offset 5 + 16 = 21
    DESC_SIZE = 24  # 4+4+8+8 = bufferLength/Index(4) + bufferOffset/reserved(4) + flags[0](8) + flags[1](8)
    desc_base = 21

    for i in range(num_descriptors):
        desc_off = desc_base + i * DESC_SIZE
        if desc_off + DESC_SIZE > len(raw):
            break

        dword0 = struct.unpack_from('<I', raw, desc_off)[0]      # bufferLength or bufferIndex
        dword1 = struct.unpack_from('<I', raw, desc_off + 4)[0]   # bufferOffset or 0 (reserved)
        flags0 = struct.unpack_from('<Q', raw, desc_off + 8)[0]
        flags1 = struct.unpack_from('<Q', raw, desc_off + 16)[0]

        entry = ShaderEntry(flags=(flags0, flags1))

        # Reference detection: reserved DWORD is 0, OR the computed offset is
        # clearly invalid (buffer would extend past file, or offset wraps around)
        buf_abs = desc_off + dword1
        buf_len = dword0
        is_ref = (dword1 == 0) or (buf_abs > len(raw)) or (buf_abs + buf_len > len(raw))

        if is_ref:
            # Reference shader: bufferIndex in dword0
            entry.ref_index = dword0
            entry.data = None
        else:
            # Standard shader: bufferLength in dword0, bufferOffset in dword1
            # bufferOffset is relative to the ShaderDesc_t instance position
            entry.data = bytearray(raw[buf_abs:buf_abs + buf_len])

        msw.entries.append(entry)

    # Infer shader type from first DXBC entry
    for e in msw.entries:
        if e.data and len(e.data) >= 32 and e.data[:4] == b'DXBC':
            from dxbc import get_shader_type
            st = get_shader_type(bytes(e.data))
            if st >= 0:
                msw.shader_type = st
            break

    return msw


def write_msw(msw: ShaderMSW, filepath: str):
    """Write an MSW shader file to disk."""
    raw = build_msw(msw)
    os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
    with open(filepath, 'wb') as f:
        f.write(raw)


def build_msw(msw: ShaderMSW) -> bytes:
    """Build MSW binary from a ShaderMSW structure."""
    num_desc = len(msw.entries)
    name_bytes = msw.name.encode('ascii') + b'\x00'

    # Calculate layout:
    # Header: 5 bytes
    # Shader_t: 16 bytes (at offset 5)
    # Descriptors: num_desc * 24 bytes (at offset 21)
    # Name: len(name_bytes) (after descriptors)
    # DXBC blobs: after name

    DESC_SIZE = 24
    desc_base = 21
    name_start = desc_base + num_desc * DESC_SIZE
    data_start = name_start + len(name_bytes)

    # Build features+count uint64
    features_56 = int.from_bytes(msw.features[:7] + b'\x00', 'little') & 0x00FFFFFFFFFFFFFF
    features_and_count = features_56 | (num_desc << 56)

    # Name offset is relative to Shader_t start (offset 5)
    name_offset = name_start - 5

    # Pre-calculate buffer positions
    # Each standard entry's bufferOffset is relative to its own ShaderDesc_t position
    current_data_offset = data_start
    entry_buffers = []  # (abs_offset, data_bytes) for standard entries

    for i, entry in enumerate(msw.entries):
        if entry.is_reference:
            entry_buffers.append(None)
        elif entry.data is not None:
            entry_buffers.append((current_data_offset, bytes(entry.data)))
            current_data_offset += len(entry.data)
        else:
            entry_buffers.append(None)

    total_size = current_data_offset

    # Build output
    out = bytearray(total_size)

    # Header
    out[0:3] = MSW_MAGIC
    out[3] = msw.version
    out[4] = msw.file_type

    # Shader_t
    struct.pack_into('<Q', out, 5, features_and_count)
    struct.pack_into('<I', out, 13, len(name_bytes) - 1)  # nameLength (excluding null)
    struct.pack_into('<I', out, 17, name_offset)

    # Descriptors
    for i, entry in enumerate(msw.entries):
        desc_off = desc_base + i * DESC_SIZE

        if entry.is_reference:
            struct.pack_into('<I', out, desc_off, entry.ref_index)
            struct.pack_into('<I', out, desc_off + 4, 0)  # reserved = 0
        elif entry_buffers[i] is not None:
            abs_off, buf_data = entry_buffers[i]
            rel_off = abs_off - desc_off  # relative to descriptor position
            struct.pack_into('<I', out, desc_off, len(buf_data))
            struct.pack_into('<I', out, desc_off + 4, rel_off)
        else:
            # Empty entry
            struct.pack_into('<I', out, desc_off, 0)
            struct.pack_into('<I', out, desc_off + 4, 0)

        struct.pack_into('<Q', out, desc_off + 8, entry.flags[0])
        struct.pack_into('<Q', out, desc_off + 16, entry.flags[1])

    # Name
    out[name_start:name_start + len(name_bytes)] = name_bytes

    # DXBC data
    for buf_info in entry_buffers:
        if buf_info is not None:
            abs_off, buf_data = buf_info
            out[abs_off:abs_off + len(buf_data)] = buf_data

    return bytes(out)
