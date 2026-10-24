import kitpaths as KP
import json, re, sys

P = (KP.DEPLOY + r"\platform\localization\_custom_catalog_en.json")
b = open(P, "rb").read()
t = b.decode("utf-8")
K = sys.argv[1]
if '"%s"' % K in t:
    raise SystemExit("already present")
nl = "\r\n" if "\r\n" in t else "\n"

# order list: '    "mp_rr_desertlands_mu5",' followed by aimtrainer
t = t.replace('    "mp_rr_desertlands_mu5",' + nl, '    "mp_rr_desertlands_mu5",' + nl + '    "%s",' % K + nl, 1)
# entries dict
t = t.replace('    "mp_rr_desertlands_mu5": "World\'s Edge",' + nl,
              '    "mp_rr_desertlands_mu5": "World\'s Edge",' + nl + '    "%s": "%s",' % (K, sys.argv[2]) + nl, 1)
# section list: mu5 is the last element
t, n = re.subn(r'(      "mp_rr_desertlands_mu5")(\r?\n    \])', r'\1,' + nl.replace("\\", "\\\\") + '      "%s"\\2' % K, t, count=1)
assert n == 1, "section anchor"
j = json.loads(t)
assert K in j["order"] and K in j["entries"], "insert failed"
old = j["count"]
t = t.replace('"count": %d' % old, '"count": %d' % len(j["order"]), 1)
j = json.loads(t)
assert j["count"] == len(j["order"]) == len(j["entries"]), (j["count"], len(j["order"]), len(j["entries"]))
open(P, "wb").write(t.encode("utf-8"))
print("catalog count", old, "->", j["count"])
