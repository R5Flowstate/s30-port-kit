"""Drop effects whose asset refs resolve in neither the S21 oracle, the built main pak (built.csv) nor the map's own
effects; rewrites allow.json. Usage: refgate.py <workspace>"""
import csv, glob, json, os, struct, sys

W = sys.argv[1]  # map workspace; run from the dir holding built.csv, allow.json, out/
HERE = os.getcwd()
have = set()
for r in csv.DictReader(open(W + "/inv/s21_oracle.csv", encoding="utf-8", errors="replace")):
    if not r["file_name"].lower().startswith("mp_lobby"):
        have.add(int(r["guid"], 16))
for r in csv.DictReader(open(HERE + "/built.csv", encoding="utf-8", errors="replace")):
    have.add(int(r["guid"], 16))
effects = {int(os.path.basename(f)[:-len(".efct_assetrefs")], 16): f
           for f in glob.glob(HERE + "/out/effect/*.efct_assetrefs")}
have |= set(effects)
bad = []
for g, f in sorted(effects.items()):
    b = open(f, "rb").read()
    refs = struct.unpack("<%dQ" % (len(b) // 8), b)
    miss = [r for r in refs if r and r not in have]
    if miss:
        bad.append(g)
        print("0x%016X missing %s" % (g, ["0x%016X" % m for m in miss]))
allow = json.load(open(HERE + "/allow.json"))
keep = [a for a in allow if int(a, 16) not in bad]
json.dump(keep, open(HERE + "/allow.json", "w"))
print("effects", len(effects), "bad", len(bad), "allow", len(allow), "->", len(keep))
