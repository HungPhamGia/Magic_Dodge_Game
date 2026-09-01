import serial, threading, glob, time, sys, pygame
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent   # so `python wand_test/live_test.py`
if str(ROOT) not in sys.path:                   # works, not only `-m` from root
    sys.path.insert(0, str(ROOT))

from wand.dollar import load_templates, classify, normalize, score
from wand.wifi import Link

PORT    = "COM5"
SCALE   = 8.0
W, H    = 900, 700
MIN_PTS = 15
REJECT  = 60.0        # tune from test.py output

tpl = load_templates(str(ROOT / "strokes_*.json"))
if not tpl:
    print("no strokes_*.json found. record templates first."); raise SystemExit(1)
labels = sorted({lab for lab, _ in tpl})
print(f"loaded {len(tpl)} templates: " + ", ".join(f"{l} {sum(1 for a,_ in tpl if a==l)}" for l in labels))

try:
    ser = Link()  # serial.Serial(PORT, 115200, timeout=0.1)
except serial.SerialException as e:
    print(f"Cannot open {PORT}: {e}\nClose the Arduino Serial Monitor.")
    raise SystemExit(1)

COLORS = {"square": (120, 200, 255), "triangle": (255, 170, 120), "circle": (170, 255, 170)}
lock   = threading.Lock()
state  = {"x": 0.0, "y": 0.0, "pen": 0}
result = {"name": None, "score": 0.0, "all": {}, "t": 0.0, "ms": 0.0}
cur, last = [], []

def per_label(stroke):
    c = normalize(stroke)
    out = {}
    for lab, t in tpl:
        s = score(c, t)
        if lab not in out or s < out[lab]: out[lab] = s
    return out

def reader():
    global cur, last
    while True:
        line = ser.readline().decode(errors="ignore").strip()
        if not line.startswith("P,"):
            if line.startswith("#"): print(line)
            continue
        try:
            _, a, b, p = line.split(",")
            x, y, pen = float(a), float(b), int(p)
        except ValueError:
            continue
        with lock:
            was = state["pen"]
            state.update(x=x, y=y, pen=pen)
            if pen and not was: cur = []
            if pen: cur.append((x, y))
            elif was and len(cur) >= MIN_PTS:
                stroke = cur[:]
                cur = []
                t0 = time.perf_counter()
                name, sc = classify(stroke, tpl, reject=REJECT)
                ms = (time.perf_counter() - t0) * 1000
                allsc = per_label(stroke)
                last = stroke
                result.update(name=name, score=sc, all=allsc, t=time.time(), ms=ms)
                tag = f"SPELL {name}" if name else "rejected"
                print(f"{tag:18s} best {sc:6.1f}  " +
                      "  ".join(f"{k[:3]} {v:5.1f}" for k, v in sorted(allsc.items())) +
                      f"   {ms:.1f} ms")
            elif was:
                cur = []

threading.Thread(target=reader, daemon=True).start()

pygame.init()
screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("wand live test")
font  = pygame.font.SysFont("consolas", 18)
huge  = pygame.font.SysFont("consolas", 64, bold=True)
clock = pygame.time.Clock()

def to_px(p): return (W // 2 + p[0] * SCALE, H // 2 - p[1] * SCALE)

run = True
while run:
    for e in pygame.event.get():
        if e.type == pygame.QUIT: run = False
        elif e.type == pygame.KEYDOWN:
            if   e.key == pygame.K_ESCAPE: run = False
            elif e.key == pygame.K_z: ser.write(b"z")
            elif e.key == pygame.K_c:
                with lock: last = []; result.update(name=None, all={}, t=0.0)

    with lock:
        c, l, st, r = cur[:], last[:], dict(state), dict(result)

    screen.fill((14, 14, 22))
    pygame.draw.line(screen, (40, 40, 55), (0, H // 2), (W, H // 2))
    pygame.draw.line(screen, (40, 40, 55), (W // 2, 0), (W // 2, H))

    if len(l) > 1:
        col = COLORS.get(r["name"], (80, 80, 100))
        pygame.draw.lines(screen, col, False, [to_px(p) for p in l], 3)
    if len(c) > 1:
        pygame.draw.lines(screen, (200, 200, 220), False, [to_px(p) for p in c], 4)

    pos = to_px((st["x"], st["y"]))
    pygame.draw.circle(screen, (255, 220, 90) if st["pen"] else (110, 110, 130),
                       pos, 9 if st["pen"] else 5)

    fresh = time.time() - r["t"] < 2.0
    if fresh:
        txt = r["name"].upper() if r["name"] else "?"
        col = COLORS.get(r["name"], (150, 90, 90))
        surf = huge.render(txt, True, col)
        screen.blit(surf, (W // 2 - surf.get_width() // 2, 24))
        screen.blit(font.render(f"score {r['score']:.1f}   {r['ms']:.1f} ms",
                                True, (150, 150, 165)), (W // 2 - 70, 96))

    y0 = H - 130
    for i, lab in enumerate(labels):
        v = r["all"].get(lab)
        screen.blit(font.render(f"{lab:9s}", True, (170, 170, 185)), (16, y0 + i * 26))
        if v is not None:
            w = int(max(0, 300 - v * 2.5))
            pygame.draw.rect(screen, (45, 45, 60), (110, y0 + i * 26 + 4, 300, 14))
            pygame.draw.rect(screen, COLORS.get(lab, (120, 120, 140)), (110, y0 + i * 26 + 4, w, 14))
            screen.blit(font.render(f"{v:5.1f}", True, (150, 150, 165)), (424, y0 + i * 26))
    pygame.draw.line(screen, (200, 80, 80),
                     (110 + int(max(0, 300 - REJECT * 2.5)), y0),
                     (110 + int(max(0, 300 - REJECT * 2.5)), y0 + len(labels) * 26), 1)

    screen.blit(font.render(f"reject {REJECT:.0f}   [z] recenter  [c] clear  [esc] quit",
                            True, (110, 110, 130)), (12, H - 28))
    pygame.display.flip()
    clock.tick(60)

pygame.quit(); ser.close()