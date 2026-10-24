"""Disassemble DXBC entries of a shader (from an S21 decompressed pak or an MSW) via d3dcompiler_47 D3DDisassemble."""
import kitpaths as KP
import ctypes, sys, glob, os

import pak_msw as P, msw
d3d = ctypes.WinDLL("d3dcompiler_47.dll")
class Blob(ctypes.Structure): pass
def dis(code):
    out = ctypes.c_void_p()
    hr = d3d.D3DDisassemble(ctypes.c_char_p(code), ctypes.c_size_t(len(code)), 0, None, ctypes.byref(out))
    if hr != 0: return "D3DDisassemble hr=%x" % (hr & 0xffffffff)
    vt = ctypes.cast(out, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ptr = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vt[3])(out)
    size = ctypes.CFUNCTYPE(ctypes.c_size_t, ctypes.c_void_p)(vt[4])(out)
    return ctypes.string_at(ptr, size).decode("latin1")
def entries(guid):
    for pak in glob.glob((KP.WORK + r"\paks_dec\*.dec.rpak")):
        s = P.read_shader(pak, guid)
        if s and s["type"] <= 5:
            return [e["buf"] for e in s["entries"]], pak
    return None, None
if __name__ == "__main__":
    g = int(sys.argv[1], 16); k = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if len(sys.argv) > 3:
        m = msw.read_msw(sys.argv[3]); ents = [bytes(e.data) if e.data else None for e in m.entries]; src = sys.argv[3]
    else:
        ents, src = entries(g)
    print("//", src, "entries", len(ents))
    live = [i for i, e in enumerate(ents) if e]
    print("// live", live)
    print(dis(bytes(ents[live[k]])))
