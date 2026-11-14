import json, glob, os, re, sys, collections
B = sys.argv[1]
def leaves(o, p=""):
    if isinstance(o, dict):
        for k, v in o.items(): yield from leaves(v, f"{p}.{k}" if p else k)
    elif isinstance(o, list):
        for i, v in enumerate(o): yield from leaves(v, f"{p}[{i}]")
    else: yield p, o
def load(side):
    out = {}
    for f in glob.glob(f"{B}/{side}/*.json"):
        try: out[os.path.basename(f)[:-5]] = json.load(open(f, encoding="utf-8"))
        except Exception: pass
    return out
gc, off = load("gc"), load("official")
offl = [(f, p, v) for f, d in off.items() for p, v in leaves(d)]
offkeys = collections.defaultdict(set)
for f, p, v in offl: offkeys[re.sub(r"\[\d+\]", "", p).split(".")[-1]].add(f)
num_idx = collections.defaultdict(list); str_idx = collections.defaultdict(list)
for f, p, v in offl:
    if isinstance(v, bool) or v is None: continue
    if isinstance(v, (int, float)): num_idx[round(float(v), 2)].append((f, p))
    elif isinstance(v, str) and len(v) > 5: str_idx[v].append((f, p))
FACT = {1: "", 1000: "x1000", 0.001: "/1000", 60: "x60", 1/60: "/60", 3600: "x3600", 1/3600: "/3600"}
TARGETS = ["readiness", "training_status", "endurance", "race_predictions", "ftp", "lactate_threshold", "hill_score", "max_metrics"]
def trivial(v):
    return isinstance(v, bool) or v is None or (isinstance(v, (int, float)) and (abs(v) < 2 or v > 1e11 or (float(v).is_integer() and 2000 <= v <= 2100)))
for t in TARGETS:
    files = sorted(k for k in gc if k.startswith(t))
    seen = set(); print(f"\n######## {t}  ({len(files)} gc files)")
    for fn in files:
        for p, v in leaves(gc[fn]):
            key = re.sub(r"\[\d+\]", "[]", p)
            if key in seen or trivial(v): continue
            seen.add(key)
            hits = []
            if isinstance(v, (int, float)):
                for fac, lab in FACT.items():
                    for of, op in num_idx.get(round(float(v) * fac, 2), [])[:3]:
                        hits.append(f"{of}:{op}{' '+lab if lab else ''}")
            elif isinstance(v, str) and len(v) > 5 and not re.match(r"\d{4}-\d\d-\d\d", v):
                hits = [f"{of}:{op}" for of, op in str_idx.get(v, [])[:3]]
            else: continue
            kname = key.split(".")[-1]
            kh = f" [key '{kname}' exists in: {sorted(offkeys[kname])[:3]}]" if kname in offkeys else ""
            print(f"{'MATCH' if hits else '  -  '} {key} = {str(v)[:40]}{kh}")
            for h in hits[:4]: print(f"        -> {h}")
