"""Overwrite the cubemap ambient txtx payload in a built (uncompressed) map rpak: old blob must occur exactly once."""
import sys
pak, old_path, new_path = sys.argv[1], sys.argv[2], sys.argv[3]
old = open(old_path, "rb").read()[16:]; new = open(new_path, "rb").read()[16:]
assert len(old) == len(new)
d = bytearray(open(pak, "rb").read())
assert d[6] & 0x01 == 0 and d[:4] == b"RPak", "pak must be uncompressed"
hits = []; i = d.find(old)
while i >= 0: hits.append(i); i = d.find(old, i + 1)
if len(hits) != 1: sys.exit("expected 1 occurrence, found %d" % len(hits))
d[hits[0]:hits[0] + len(new)] = new
open(pak, "wb").write(d); print("patched", pak, "at", hex(hits[0]))
