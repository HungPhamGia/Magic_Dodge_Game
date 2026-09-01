import serial, threading, pygame

PORT  = "COM5"      # Linux or Mac: /dev/ttyUSB0 or /dev/cu.usbserial-XXXX
SCALE = 8.0         # pixels per degree
W, H  = 900, 700

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

lock = threading.Lock()
state = {"x": 0.0, "y": 0.0, "pen": 0}
cur, done = [], []

def reader():
    global cur
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
            elif was and len(cur) > 3:
                done.append(cur)
                if len(done) > 3: done.pop(0)
                cur = []

threading.Thread(target=reader, daemon=True).start()

pygame.init()
screen = pygame.display.set_mode((W, H))
pygame.display.set_caption("wand canvas")
font = pygame.font.SysFont("consolas", 18)
clock = pygame.time.Clock()

def to_px(p): return (W // 2 + p[0] * SCALE, H // 2 - p[1] * SCALE)

run = True
while run:
    for e in pygame.event.get():
        if e.type == pygame.QUIT: run = False
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_c:
                with lock: done.clear(); cur.clear()
            elif e.key == pygame.K_z:
                ser.write(b"z")
            elif e.key == pygame.K_ESCAPE: run = False

    with lock:
        c, dn, st = cur[:], [s[:] for s in done], dict(state)

    screen.fill((14, 14, 22))
    pygame.draw.line(screen, (40, 40, 55), (0, H // 2), (W, H // 2))
    pygame.draw.line(screen, (40, 40, 55), (W // 2, 0), (W // 2, H))

    for s in dn:
        if len(s) > 1: pygame.draw.lines(screen, (70, 70, 110), False, [to_px(p) for p in s], 2)
    if len(c) > 1:
        pygame.draw.lines(screen, (120, 200, 255), False, [to_px(p) for p in c], 4)

    pos = to_px((st["x"], st["y"]))
    pygame.draw.circle(screen, (255, 220, 90) if st["pen"] else (110, 110, 130), pos, 9 if st["pen"] else 5)

    span = ""
    if len(c) > 1:
        xs = [p[0] for p in c]; ys = [p[1] for p in c]
        span = f"  {max(xs)-min(xs):.0f} x {max(ys)-min(ys):.0f} deg  {len(c)} pts"
    screen.blit(font.render(f"pen {'DOWN' if st['pen'] else 'up'}{span}   [c] clear  [z] recenter", True, (200, 200, 210)), (12, 12))
    pygame.display.flip()
    clock.tick(60)

pygame.quit(); ser.close()