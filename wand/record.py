import serial, threading, json, time, pygame

PORT    = "COM5"
SCALE   = 8.0        # pixels per degree
W, H    = 900, 700
MIN_PTS = 15         # strokes shorter than this are ignored

try:
    ser = serial.Serial(PORT, 115200, timeout=1)
except serial.SerialException as e:
    import serial.tools.list_ports as lp
    print(f"Cannot open {PORT}: {e}")
    print("Available ports:")
    for p in lp.comports():
        print(" ", p.device, "-", p.description)
    print("Close the Arduino Serial Monitor, then try again.")
    raise SystemExit(1)

LABELS = ["square", "triangle", "circle"]
COLORS = {"square": (120, 200, 255), "triangle": (255, 170, 120), "circle": (170, 255, 170)}

lock  = threading.Lock()
state = {"x": 0.0, "y": 0.0, "pen": 0}
cfg   = {"label": "square", "armed": True}
rec   = {k: [] for k in LABELS}
cur, last = [], []

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
            if pen and not was:
                cur = []
            if pen:
                cur.append((x, y))
            elif was:
                if len(cur) >= MIN_PTS:
                    last = cur[:]
                    if cfg["armed"]:
                        lab = cfg["label"]
                        rec[lab].append(last)
                        xs = [q[0] for q in last]; ys = [q[1] for q in last]
                        print(f"{lab:9s} #{len(rec[lab]):2d}  {len(last):3d} pts  "
                              f"{max(xs)-min(xs):.0f} x {max(ys)-min(ys):.0f} deg")
                cur = []

def save():
    n = 0
    for lab in LABELS:
        if not rec[lab]: continue
        fn = f"strokes_{lab}_{int(time.time())}.json"
        json.dump(rec[lab], open(fn, "w"))
        print(f"saved {len(rec[lab]):2d} to {fn}")
        n += len(rec[lab])
        rec[lab] = []
    print("nothing to save" if n == 0 else f"total {n} strokes written")

threading.Thread(target=reader, daemon=True).start()

pygame.init()
screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("wand canvas")
font  = pygame.font.SysFont("consolas", 18)
big   = pygame.font.SysFont("consolas", 26, bold=True)
clock = pygame.time.Clock()

def to_px(p): return (W // 2 + p[0] * SCALE, H // 2 - p[1] * SCALE)

run = True
while run:
    for e in pygame.event.get():
        if e.type == pygame.QUIT: run = False
        elif e.type == pygame.KEYDOWN:
            if   e.key == pygame.K_ESCAPE: run = False
            elif e.key == pygame.K_1: cfg["label"] = "square"
            elif e.key == pygame.K_2: cfg["label"] = "triangle"
            elif e.key == pygame.K_3: cfg["label"] = "circle"
            elif e.key == pygame.K_SPACE:
                cfg["armed"] = not cfg["armed"]
            elif e.key == pygame.K_u:
                with lock:
                    if rec[cfg["label"]]:
                        rec[cfg["label"]].pop()
                        print(f"removed one {cfg['label']}")
            elif e.key == pygame.K_s:
                with lock: save()
            elif e.key == pygame.K_z: ser.write(b"z")
            elif e.key == pygame.K_c:
                with lock: last = []; cur.clear()

    with lock:
        c, l, st = cur[:], last[:], dict(state)
        lab, armed = cfg["label"], cfg["armed"]
        counts = {k: len(rec[k]) for k in LABELS}

    screen.fill((14, 14, 22))
    pygame.draw.line(screen, (40, 40, 55), (0, H // 2), (W, H // 2))
    pygame.draw.line(screen, (40, 40, 55), (W // 2, 0), (W // 2, H))

    if len(l) > 1:
        pygame.draw.lines(screen, (70, 70, 110), False, [to_px(p) for p in l], 2)
    if len(c) > 1:
        pygame.draw.lines(screen, COLORS[lab], False, [to_px(p) for p in c], 4)

    pos = to_px((st["x"], st["y"]))
    pygame.draw.circle(screen, (255, 220, 90) if st["pen"] else (110, 110, 130),
                       pos, 9 if st["pen"] else 5)

    tag = f"REC {lab}" if armed else f"PAUSED ({lab})"
    screen.blit(big.render(tag, True, COLORS[lab] if armed else (130, 130, 140)), (12, 10))
    screen.blit(font.render(
        "  ".join(f"{k[:3]} {counts[k]}" for k in LABELS), True, (200, 200, 210)), (12, 44))
    span = ""
    ref = c if len(c) > 1 else l
    if len(ref) > 1:
        xs = [p[0] for p in ref]; ys = [p[1] for p in ref]
        span = f"{max(xs)-min(xs):.0f} x {max(ys)-min(ys):.0f} deg  {len(ref)} pts"
    screen.blit(font.render(span, True, (150, 150, 165)), (12, 68))
    screen.blit(font.render(
        "[1/2/3] shape  [space] pause  [u] undo  [s] save  [z] recenter  [c] clear",
        True, (110, 110, 130)), (12, H - 28))

    pygame.display.flip()
    clock.tick(60)

with lock: save()
pygame.quit(); ser.close()