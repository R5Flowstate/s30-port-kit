import struct, numpy as np, sys
def props(path):
    g = open(path, "rb").read()
    cnt = struct.unpack_from("<i", g, 0)[0]
    for k in range(cnt):
        gid, _, ver, fo, fl = struct.unpack_from("<iHHii", g, 4 + 16 * k)
        if gid == 0x73707270:
            # unpacked sidecar: offset relative to the lump file start = header size
            start = 4 + 16 * cnt
            d = g[start:start + fl]
            nd = struct.unpack_from("<i", d, 0)[0]
            names = [d[4 + 128 * i:4 + 128 * (i + 1)].split(b"\0")[0].decode() for i in range(nd)]
            o = 4 + 128 * nd
            n = struct.unpack_from("<I", d, o)[0]
            return names, d, o + 4, n, ver
if __name__ == "__main__":
    names, d, o, n, ver = props(sys.argv[1])
    print("ver", ver, "dict", len(names), "props", n, "remaining", len(d) - o, "per prop", (len(d) - o) / max(n, 1))
    print(d[o:o + 128].hex(" ", 4))
