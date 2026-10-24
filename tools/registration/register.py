r"""Add a map to the survival_dev + map_editor playlists and r5f_map_names.txt (after the mu5 line).
Usage: register.py <map> "<launcher name>"  -- edits <DEPLOY>\platform, back the files up first."""
import kitpaths as KP
import re, sys

P = (KP.DEPLOY + r"\platform\playlists_r5_patch.txt")
t = open(P, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in t else "\n"
lines = t.split(nl)
MAP, NAME = sys.argv[1], sys.argv[2]
if MAP in t:
    raise SystemExit("already registered")


def block_end_after(name):
    start = next(i for i, l in enumerate(lines) if l.strip() == name)
    mi = next(i for i in range(start, len(lines)) if "gamemodes { survival { maps {" in lines[i])
    end = next(i for i in range(mi, len(lines)) if lines[i].strip() == "} } }")
    indent = re.match(r"\s*", lines[mi + 1]).group(0)
    return end, indent


for name in ("survival_dev", "map_editor"):
    end, indent = block_end_after(name)
    lines.insert(end, indent + MAP + " 1")
    print("added to", name, "at line", end + 1)
open(P, "w", encoding="utf-8", newline="").write(nl.join(lines))

N = (KP.DEPLOY + r"\platform\r5f_map_names.txt")
t = open(N, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in t else "\n"
anchor = next(l for l in t.split(nl) if l.startswith("mp_rr_desertlands_mu5"))
t = t.replace(anchor, anchor + nl + MAP.ljust(34) + "= " + NAME, 1)
open(N, "w", encoding="utf-8", newline="").write(t)
print("map name added")
