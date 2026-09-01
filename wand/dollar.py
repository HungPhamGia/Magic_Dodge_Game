import math, json, glob, os

N = 64
SIZE = 250.0

def d(a, b): return math.hypot(b[0] - a[0], b[1] - a[1])
def plen(p): return sum(d(p[i - 1], p[i]) for i in range(1, len(p)))

def resample(p, n=N):
    p = [tuple(q) for q in p]
    total = plen(p)
    if total == 0: return [p[0]] * n
    I = total / (n - 1)
    out = [p[0]]; acc = 0.0; i = 1
    while i < len(p):
        s = d(p[i - 1], p[i])
        if s > 0 and acc + s >= I:
            t = (I - acc) / s
            q = (p[i - 1][0] + t * (p[i][0] - p[i - 1][0]),
                 p[i - 1][1] + t * (p[i][1] - p[i - 1][1]))
            out.append(q); p.insert(i, q); acc = 0.0
        else:
            acc += s
        i += 1
    while len(out) < n: out.append(p[-1])
    return out[:n]

def normalize(p):
    """Resample, scale to a fixed box, center at origin. Rotation is NOT removed."""
    p = resample(p)
    xs = [q[0] for q in p]; ys = [q[1] for q in p]
    w = max(xs) - min(xs) or 1e-6
    h = max(ys) - min(ys) or 1e-6
    s = SIZE / max(w, h)                      # uniform scale keeps aspect ratio
    p = [(q[0] * s, q[1] * s) for q in p]
    cx = sum(q[0] for q in p) / len(p)
    cy = sum(q[1] for q in p) / len(p)
    return [(q[0] - cx, q[1] - cy) for q in p]

def score(a, b):
    """Mean point distance, trying every start point of b. Handles strokes
    that begin at a different corner or run the opposite direction."""
    best = 1e18
    rb = b[::-1]
    for cand in (b, rb):
        for off in range(0, N, 2):
            rot = cand[off:] + cand[:off]
            s = sum(d(a[i], rot[i]) for i in range(N)) / N
            if s < best: best = s
    return best

def load_templates(pattern="strokes_*.json"):
    tpl = []
    for fn in glob.glob(pattern):
        label = os.path.basename(fn).split("_")[1]   # basename: an absolute
                                                     # pattern would otherwise
                                                     # split on gPBL_game first
        for s in json.load(open(fn)):
            if len(s) >= 15: tpl.append((label, normalize(s)))
    return tpl

def smooth(p, k=3):
    out = []
    for i in range(len(p)):
        lo, hi = max(0, i - k), min(len(p), i + k + 1)
        w = p[lo:hi]
        out.append((sum(q[0] for q in w) / len(w), sum(q[1] for q in w) / len(w)))
    return out

def turn_profile(p, step=3):
    """Turning angle at each point, in degrees."""
    ang = []
    for i in range(step, len(p) - step):
        a = math.atan2(p[i][1] - p[i-step][1], p[i][0] - p[i-step][0])
        b = math.atan2(p[i+step][1] - p[i][1], p[i+step][0] - p[i][0])
        t = math.degrees(b - a)
        while t > 180: t -= 360
        while t < -180: t += 360
        ang.append(t)
    return ang

def count_corners(stroke, th=38, gap=6):
    """Number of sharp turns. Circle 0 to 1, triangle 3, square 4."""
    p = smooth(resample(stroke), k=2)
    ang = turn_profile(p)
    n, last = 0, -99
    for i, t in enumerate(ang):
        if abs(t) > th and i - last > gap:
            n += 1; last = i
    return n

def classify(stroke, templates, reject=70.0):
    if len(stroke) < 15 or plen(stroke) < 20:
        return None, 999.0
    nc = count_corners(stroke)
    if   nc <= 1: allowed = {"circle"}
    elif nc == 2: allowed = {"circle", "triangle"}
    elif nc == 3: allowed = {"triangle", "square"}
    else:         allowed = {"square"}

    c = normalize(stroke)
    best, name = 1e18, None
    for label, t in templates:
        if label not in allowed: continue
        s = score(c, t)
        if s < best: best, name = s, label
    if name is None:                      # corner count matched nothing
        return None, 999.0
    return (name if best < reject else None), best