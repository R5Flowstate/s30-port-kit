import kitpaths as KP
import glob, json, os, re, subprocess, sys

REPO = KP.SCRIPTS_REPO
ANCHOR = b'\t"mp_rr_desertlands_mu5" "World\'s Edge"'
NEW = ('\t"%s" "%s"' % (sys.argv[1], sys.argv[2])).encode()
K = sys.argv[1]


def git(*a, inp=None):
    return subprocess.run(["git", "-C", REPO] + list(a), input=inp, capture_output=True, check=True).stdout


def stage(path, data):
    blob = git("hash-object", "-w", "--stdin", inp=data).decode().strip()
    git("update-index", "--cacheinfo", "100644,%s,%s" % (blob, path))
    print("staged", path)


for p in sorted(glob.glob(os.path.join(REPO, "localization", "localization_*.txt"))):
    rel = "localization/" + os.path.basename(p)
    b = git("show", "HEAD:" + rel)
    if NEW in b:
        print("already in HEAD", rel)
        continue
    i = b.find(ANCHOR)
    assert i >= 0, rel
    eol = b.find(b"\n", i)
    nl = b"\r\n" if b[eol - 1:eol] == b"\r" else b"\n"
    stage(rel, b[:eol + 1] + NEW + nl + b[eol + 1:])

rel = "localization/_custom_catalog_en.json"
t = git("show", "HEAD:" + rel).decode("utf-8")
nl = "\r\n" if "\r\n" in t else "\n"
t = t.replace('    "mp_rr_desertlands_mu5",' + nl, '    "mp_rr_desertlands_mu5",' + nl + '    "%s",' % K + nl, 1)
t = t.replace('    "mp_rr_desertlands_mu5": "World\'s Edge",' + nl,
              '    "mp_rr_desertlands_mu5": "World\'s Edge",' + nl + '    "%s": "%s",' % (K, sys.argv[2]) + nl, 1)
t, n = re.subn(r'(      "mp_rr_desertlands_mu5")(\r?\n    \])', lambda m: m.group(1) + ',' + nl + '      "%s"' % K + m.group(2), t, count=1)
assert n == 1
j = json.loads(t)
t = t.replace('"count": %d' % j["count"], '"count": %d' % len(j["order"]), 1)
j = json.loads(t)
assert j["count"] == len(j["order"]) == len(j["entries"]) and K in j["entries"]
stage(rel, t.encode("utf-8"))
