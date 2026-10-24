"""Clone the kit's tools for another S30 map.

The scripts are the mu5 working copies (proven end to end on mp_rr_desertlands_mu5). This rewrites, in a COPY:
  mp_rr_desertlands_mu5 -> <map>          (workspace, pak, BSP, wrap, datatable, overview names)
  mp_rr_desertlands_hu  -> <twin>         (the S21 retail twin used as material/ground/bake oracle)
  <WORK>/mu5_{terrain,dedi,lm2,fix2} -> <WORK>/<short>_{...}
  <WORK>/desertlands_mu5 -> <WORK>/<map minus mp_rr_>
  splat ID texture guid 0xFA20A4BD02632A22 -> --splat-ids (terrain_inputs.py prints it)

    py retarget.py <map> <twin> <short> <out dir> [--splat-ids 0x...]
Then grep the output for any leftover 'mu5' / 'desertlands' and review it before running anything."""
import argparse, os, re, shutil

HERE = os.path.dirname(os.path.abspath(__file__))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map"); ap.add_argument("twin"); ap.add_argument("short"); ap.add_argument("out")
    ap.add_argument("--splat-ids", default=None)
    a = ap.parse_args()
    stem = a.map[len("mp_rr_"):] if a.map.startswith("mp_rr_") else a.map
    subs = [("mp_rr_desertlands_mu5", a.map), ("mp_rr_desertlands_hu", a.twin),
            ('"/desertlands_mu5', '"/' + stem), ('"\\desertlands_mu5', '"\\' + stem),
            ("pc_all_desertlands_mu5", "pc_all_" + stem)]
    for k in ("terrain", "dedi", "lm2", "fix2"):
        subs.append(("mu5_" + k, "%s_%s" % (a.short, k)))
    if a.splat_ids:
        subs.append(("0xFA20A4BD02632A22", "0x%X" % int(a.splat_ids, 16)))
    if os.path.exists(a.out):
        raise SystemExit("output %s exists; pick a new directory" % a.out)
    left = []
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".py"): continue
            src = os.path.join(root, f); rel = os.path.relpath(src, HERE)
            dst = os.path.join(a.out, rel); os.makedirs(os.path.dirname(dst), exist_ok=True)
            t = open(src, encoding="utf-8", errors="surrogateescape").read()
            for o, n in subs: t = t.replace(o, n)
            open(dst, "w", encoding="utf-8", errors="surrogateescape").write(t)
            for i, line in enumerate(t.split("\n"), 1):
                if re.search(r"mu5|desertlands|FA20A4BD02632A22", line, re.I) and rel != "retarget.py":
                    left.append("%s:%d: %s" % (rel, i, line.strip()[:140]))
    print("wrote toolkit to", a.out)
    print("%d lines still mention mu5/desertlands/the mu5 splat guid -- review each:" % len(left))
    for l in left: print("  ", l)

if __name__ == "__main__":
    main()
