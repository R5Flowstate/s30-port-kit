import kitpaths as KP
import ctypes
_oo = ctypes.WinDLL((KP.REPAK + r"/bin/Release/oo2core_8_win64.dll"))
_fn = _oo.OodleLZ_Decompress; _fn.restype = ctypes.c_int64
_fn.argtypes = [ctypes.c_char_p, ctypes.c_int64, ctypes.c_char_p, ctypes.c_int64] + [ctypes.c_int] * 3 + [ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int]
def decompress(comp, raw_len):
    out = ctypes.create_string_buffer(raw_len)
    n = _fn(comp, len(comp), out, raw_len, 1, 0, 0, None, 0, None, None, None, 0, 3)
    return out.raw if n == raw_len else None
