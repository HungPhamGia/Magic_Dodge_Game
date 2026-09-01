import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # so `python wand_test/test.py`
if str(ROOT) not in sys.path:                   # works, not only `-m` from root
    sys.path.insert(0, str(ROOT))

from wand.dollar import normalize, classify

files = sorted(ROOT.glob("strokes_*.json"))     # at the root, wherever cwd is
data = [(fn.name.split("_")[1], s) for fn in files for s in json.load(open(fn)) if len(s) >= 15]

ok = 0; scores = {"hit": [], "miss": []}
for i, (truth, stroke) in enumerate(data):
    tpl = [(lab, normalize(s)) for j, (lab, s) in enumerate(data) if j != i]   # leave one out
    pred, sc = classify(stroke, tpl, reject=1e9)
    good = pred == truth
    ok += good
    scores["hit" if good else "miss"].append(sc)
    print(f"{truth:9s} -> {str(pred):9s}  score {sc:6.1f}  {'' if good else 'WRONG'}")

print(f"\naccuracy {ok}/{len(data)} = {100*ok/len(data):.0f}%")
if scores["hit"]:  print(f"correct  scores: {min(scores['hit']):.0f} to {max(scores['hit']):.0f}")
if scores["miss"]: print(f"wrong    scores: {min(scores['miss']):.0f} to {max(scores['miss']):.0f}")