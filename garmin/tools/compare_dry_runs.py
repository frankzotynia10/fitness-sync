"""Row-by-row diff of two GARMIN_DRY_RUN outputs: python tools/compare_dry_runs.py a.json b.json"""
import json, sys, collections
KEYS = {"garmin_daily": ("date",), "garmin_activity_summary": ("activity_id",), "garmin_activity_gps": ("activity_id", "recorded_at"),
        "garmin_ftp": ("date",), "garmin_race_predictions": ("date",)}
def index(path):
    out = collections.defaultdict(dict)
    for w in json.load(open(path, encoding="utf-8")):
        t = w.get("table"); row = {k: v for k, v in (w.get("row") or {}).items() if k != "updated_at"}
        key = tuple(row.get(k) for k in KEYS.get(t, ("recorded_at",)))
        out[t][key] = row
    return out
a, b = index(sys.argv[1]), index(sys.argv[2])
for t in sorted(set(a) | set(b)):
    A, B = a.get(t, {}), b.get(t, {})
    same = sum(1 for k in A if k in B and A[k] == B[k])
    print(f"\n## {t}: A {len(A)} rows, B {len(B)} rows, identical {same}, only-A {len(set(A)-set(B))}, only-B {len(set(B)-set(A))}")
    diffs = collections.defaultdict(list)
    for k in sorted(set(A) & set(B), key=str):
        for col in sorted(set(A[k]) | set(B[k])):
            va, vb = A[k].get(col, "<absent>"), B[k].get(col, "<absent>")
            if va != vb:
                try:
                    if abs(float(va) - float(vb)) < 1e-6: continue
                except (TypeError, ValueError): pass
                diffs[col].append((k[0] if len(k) == 1 else k, va, vb))
    for col, ds in sorted(diffs.items()):
        print(f"   {col}: {len(ds)} diffs  e.g. " + "; ".join(f"{k}: {va} -> {vb}" for k, va, vb in ds[:3]))
