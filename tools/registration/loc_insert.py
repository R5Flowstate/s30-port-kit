import kitpaths as KP
import glob, sys

ANCHOR = b'\t"mp_rr_desertlands_mu5" "World\'s Edge"'
NEW = ('\t"%s" "%s"' % (sys.argv[1], sys.argv[2])).encode()
for p in sorted(glob.glob((KP.DEPLOY + r"\platform\localization\localization_*.txt"))):
    b = open(p, "rb").read()
    if NEW in b:
        print("skip", p)
        continue
    i = b.find(ANCHOR)
    if i < 0:
        print("NO ANCHOR", p)
        continue
    eol = b.find(b"\n", i)
    nl = b"\r\n" if b[eol - 1:eol] == b"\r" else b"\n"
    b = b[:eol + 1] + NEW + nl + b[eol + 1:]
    open(p, "wb").write(b)
    print("ok", p)
