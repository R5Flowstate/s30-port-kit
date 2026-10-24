"""Machine-local roots for the kit, read from kit.local.toml (or S30KIT_<NAME> env vars).

Importing this also puts every lib/ and tools/ folder on sys.path, so any tool can
import its siblings and the shared helpers.
"""
import os, sys, tomllib

KIT = os.path.dirname(os.path.abspath(__file__))
_CFG = os.path.join(KIT, "kit.local.toml")
_vals = {}
if os.path.exists(_CFG):
    with open(_CFG, "rb") as f:
        _vals = {k.upper(): str(v) for k, v in tomllib.load(f).get("paths", {}).items()}

def __getattr__(name):
    v = os.environ.get("S30KIT_" + name) or _vals.get(name)
    if not v:
        raise RuntimeError("kitpaths: '%s' is not set -- add it under [paths] in %s (see kit.example.toml)" % (name.lower(), _CFG))
    return v.rstrip("\\/")

for _top in ("lib", "tools"):
    for _root, _dirs, _files in os.walk(os.path.join(KIT, _top)):
        _dirs[:] = [d for d in _dirs if d != "__pycache__"]
        if _root not in sys.path:
            sys.path.append(_root)
