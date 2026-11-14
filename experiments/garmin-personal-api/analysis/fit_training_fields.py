import glob, io, json, zipfile, sys
from fitparse import FitFile
B = "/probe/cmp"
def leaves(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items(): yield from leaves(v, f"{p}.{k}" if p else k)
    elif isinstance(o, list):
        for i, v in enumerate(o): yield from leaves(v, f"{p}[{i}]")
    else: yield p, o
for aid in sys.argv[1:]:
    z = zipfile.ZipFile(f"{B}/official/activity_fit_{aid}.zip"); ff = FitFile(io.BytesIO(z.read(z.namelist()[0])))
    gcvals = []
    for name in (f"activity_{aid}", "training_status_2026-11-14", "readiness_2026-11-14"):
        try: gcvals += [(name, p, v) for p, v in leaves(json.load(open(f"{B}/gc/{name}.json")))]
        except FileNotFoundError: pass
    gcvals = [(n, p, float(v)) for n, p, v in gcvals if isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) >= 2]
    print(f"\n=== activity {aid}")
    for m in ff.get_messages():
        if m.name not in ("unknown_140", "session"): continue
        for f in m.fields:
            if not (f.name.startswith("unknown") or m.name == "unknown_140") or not isinstance(f.value, (int, float)) or f.value in (0, 255, 65535, 4294967295): continue
            v = float(f.value); hits = []
            for sc, lab in ((1, ""), (0.1, "/10"), (0.01, "/100"), (1/65536, "/65536"), (1/60, "/60"), (60, "x60"), (1/1000, "/1000"), (1/3600, "/3600")):
                for n, p, gv in gcvals:
                    if abs(v * sc - gv) <= max(0.051, abs(gv) * 0.002): hits.append(f"{n.split('_')[0]}:{p}{lab}")
            print(f"  {m.name}.{f.name} = {f.value}" + (f"   ~ {hits[:3]}" if hits else ""))
