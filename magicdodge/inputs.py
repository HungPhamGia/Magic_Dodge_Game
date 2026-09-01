"""Where control comes from.

Three sources, all interchangeable: the keyboard, a camera that moves you by
reading your body, and an IMU wand you draw shapes with. Nothing outside this
file knows which ones it got.

Movement and casting are separate devices on purpose. The camera only ever
moves you; the wand only ever casts. main.py polls them all and joins the lists.
"""

import collections
import threading
import time
from pathlib import Path

import pygame

from .config import (
    CAM_CONFIDENCE,
    CAM_DEADZONE_PX,
    CAM_GRACE_S,
    CAM_H,
    CAM_ID,
    CAM_W,
    CAST_COOLDOWN_MS,
    LANES,
    WAND_BAUD,
    WAND_CALIBRATE_S,
    WAND_MIN_PTS,
    WAND_PORT,
    WAND_REJECT,
    WAND_TEMPLATES,
    WAND_WARMUP_S,
    WAND_WIFI,
)

# The events, FEEDBACK_STATES and the InputSource protocol all live in game.py,
# next to the code that consumes them. A source implements poll / send_feedback
# / close.
from .game import CastResolved, CastStarted, InputEvent, LaneChange


# --- keyboard ----------------------------------------------------------------

SHAPE_KEYS = {pygame.K_j: "triangle", pygame.K_k: "circle", pygame.K_l: "square"}
LEFT_KEYS = (pygame.K_LEFT, pygame.K_a)
RIGHT_KEYS = (pygame.K_RIGHT, pygame.K_d)


class KeyboardSource:
    """Arrows or A/D move, J/K/L cast, Esc quits, R restarts, Z recenters.

    quit, restart and recenter are window level intents rather than game input,
    so they are flags here instead of InputEvents. main.py acts on them.
    """

    def __init__(self, lane: int = 1):
        self.lane = lane
        self.quit = False
        self.restart = False
        self.recenter = False
        self.t_ms = 0.0            # driven by dt, not by the pygame clock
        self.cool_from = 0.0
        self.cool_until = 0.0

    def poll(self, dt: float) -> list[InputEvent]:
        self.t_ms += dt * 1000.0
        events: list[InputEvent] = []
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.quit = True
            elif event.type == pygame.KEYDOWN:
                self._keydown(event.key, events)
        return events

    def send_feedback(self, state: str) -> None:
        """No op. A keyboard has no LED to light. See WandSource."""

    def close(self) -> None:
        pass

    # --- extras draw.py and main.py use, not part of the Protocol ------------

    def cooldown(self) -> float | None:
        """Progress through the cast cooldown, 0.0 to 1.0, or None when ready."""
        span = self.cool_until - self.cool_from
        if span <= 0 or self.t_ms >= self.cool_until:
            return None
        return 1.0 - (self.cool_until - self.t_ms) / span

    def hint(self) -> str:
        """On screen controls, derived so rebinding the keys updates it."""
        keys = " ".join(pygame.key.name(key).upper() for key in SHAPE_KEYS)
        return f"ARROWS  move        {keys}  cast"

    def reset(self, lane: int = 1) -> None:
        self.lane = lane
        self.cool_until = self.t_ms
        self.restart = False

    def _keydown(self, key: int, events: list) -> None:
        if key == pygame.K_ESCAPE:
            self.quit = True
        elif key == pygame.K_r:
            self.restart = True
        elif key == pygame.K_z:
            # The wand integrates gyro, so it drifts. Same key the recording
            # scripts use, so the muscle memory carries over.
            self.recenter = True
        elif key in LEFT_KEYS:
            self.lane = max(0, self.lane - 1)
            events.append(LaneChange(self.lane))
        elif key in RIGHT_KEYS:
            self.lane = min(LANES - 1, self.lane + 1)
            events.append(LaneChange(self.lane))
        elif key in SHAPE_KEYS and self.t_ms >= self.cool_until:
            # One key, one bolt. The cooldown is the only cost.
            self.cool_from = self.t_ms
            self.cool_until = self.t_ms + CAST_COOLDOWN_MS
            events.append(CastResolved(SHAPE_KEYS[key], 1.0, 0, True))


# --- the wand ----------------------------------------------------------------
#
# Firmware: drawing_wand_mpu6050/drawing_wand_mpu6050.ino. It streams
# "P,<x>,<y>,<pen>" at 100 Hz and takes one command, "z", which zeroes the
# position. Recognition is dollar.py, trained on the strokes_*.json that
# record.py wrote. Both are read as they are; neither is edited here.


def wand_confidence(score: float, reject: float) -> float:
    """Classifier score to a 0..1 confidence. Lower score is a better match."""
    return max(0.0, min(1.0, 1.0 - score / reject))


def detrend(stroke):
    """Take the gyro drift out of a stroke by closing it.

    All three spells are closed shapes: the recorded ones come back to within
    4-6.5% of their own size. So whatever gap is left between the last point
    and the first is drift, not drawing, and spreading it back across the
    stroke removes it exactly.

    This is what makes the wand usable while the gyro walks. Measured on the
    recorded strokes with a leftward ramp added, classified against detrended
    templates:

        drift deg/s     0    3    5   10   20
        without      17/17 16/17 14/17 5/17 0/17
        with         16/17 16/17 16/17 16/17 16/17

    The one it costs is a sloppy recorded square that reads as a triangle.
    Worth it: without this, 10 deg/s of drift stops the wand working at all.
    """
    (x0, y0), (x1, y1) = stroke[0], stroke[-1]
    n = len(stroke) - 1
    if n <= 0:
        return [list(p) for p in stroke]
    return [[x - (x1 - x0) * i / n, y - (y1 - y0) * i / n]
            for i, (x, y) in enumerate(stroke)]


def load_templates(root, normalize, min_pts: int = WAND_MIN_PTS):
    """The recorded strokes, as (label, normalized points).

    Not dollar.load_templates: that reads the label with fn.split("_")[1], which
    only holds for a glob relative to the working directory. Given an absolute
    path it finds the underscore in gPBL_game first and labels everything
    "game\\strokes". The game has to run from anywhere, so the label comes off
    the basename here. dollar.normalize still does the actual work.
    """
    import json

    templates = []
    for path in sorted(Path(root).glob(WAND_TEMPLATES)):
        label = path.name.split("_")[1]
        for stroke in json.loads(path.read_text(encoding="utf-8")):
            if len(stroke) >= min_pts:
                templates.append((label, normalize(detrend(stroke))))
    return templates


class StrokeReader:
    """Serial lines in, cast events out. Owns no port, so it is testable.

    The pen edges come from the firmware, which drops the pen after 100ms of
    stillness. A shape therefore has to be drawn in one continuous motion, and
    every cast resolves 100ms after you stop moving.
    """

    def __init__(self, templates, classify, min_pts=WAND_MIN_PTS,
                 reject=WAND_REJECT):
        self.templates = templates
        self.classify = classify
        self.min_pts = min_pts
        self.reject = reject
        self.ready = False        # the board calibrates for ~2s on connect
        self.pen = 0
        self.point = (0.0, 0.0)
        self.stroke: list[tuple[float, float]] = []
        self.started = False      # a CastStarted is outstanding
        self.start_s = 0.0
        self.last: list[tuple[float, float]] = []
        self.last_name: str | None = None
        self.last_at = 0.0

    def feed(self, line: str) -> list[InputEvent]:
        line = line.strip()
        if line.startswith("#"):
            # Firmware chatter: the calibration banner and the bias readout.
            print(line)
            return []
        if not line.startswith("P,"):
            return []
        try:
            _, x, y, pen = line.split(",")
            point, pen = (float(x), float(y)), int(pen)
        except ValueError:
            return []

        # A P line proves calibration is over: the firmware only prints them
        # from loop(), which runs after setup() returns. Waiting for the
        # "# ready" banner instead would latch off forever whenever the board
        # was already running before the port was opened, which is most of the
        # time on a CH340 -- and then nothing ever casts.
        self.ready = True
        self.point = point
        was, self.pen = self.pen, pen

        if pen:
            if not was:
                self.stroke = []
            self.stroke.append(point)
            # Start the cast once the stroke is long enough to be a real
            # attempt, not on the pen edge. A twitch would otherwise open a
            # cast that never closes, and Game.channeling would stick on.
            if not self.started and len(self.stroke) >= self.min_pts:
                self.started = True
                self.start_s = time.perf_counter()
                return [CastStarted()]
        elif was:
            return self._release()
        return []

    def _release(self) -> list[InputEvent]:
        stroke, self.stroke = self.stroke, []
        if not self.started:
            return []             # too short to have started; stays invisible
        self.started = False

        duration_ms = int((time.perf_counter() - self.start_s) * 1000)
        stroke = detrend(stroke)
        name, score = self.classify(stroke, self.templates, reject=self.reject)
        # The preview shows the detrended stroke: that is what was classified,
        # so a shape that missed looks like the shape the game actually saw.
        self.last, self.last_name = stroke, name
        self.last_at = time.perf_counter()
        return [
            CastResolved(
                shape=name,
                confidence=wand_confidence(score, self.reject),
                duration_ms=duration_ms,
                ok=name is not None,
            )
        ]


def open_link(port: str, baud: int = WAND_BAUD):
    """The wand's transport. A COM port, or WAND_WIFI for its access point.

    wifi.Link speaks the same three methods WandSource needs -- readline,
    write, close -- so the rest of this file cannot tell them apart. The only
    difference that matters is calibration: see WandSource.calibrate.

    The firmware is a softAP, so "over Wi-Fi" means this PC has joined the
    wand's own network. That adapter has no internet while it is joined.
    """
    if port == WAND_WIFI:
        from wand import wifi       # sibling package, same as dollar

        return wifi.Link()
    import serial

    return serial.Serial(port, baud, timeout=1)


class WandSource:
    """Draw a shape in the air and it casts. Emits CastStarted / CastResolved.

    A daemon thread owns the port: readline blocks and classifying a stroke
    costs tens of milliseconds, neither of which belongs on a 60fps loop. Same
    pattern as CameraSource.

    Lane changes are not this device's job; see CameraSource.
    """

    def __init__(self, port: str = WAND_PORT, baud: int = WAND_BAUD,
                 reject: float = WAND_REJECT):
        # Deferred on purpose: keyboard-only play must not need pyserial, and a
        # missing wand must not stop the game. wand is a sibling package, so
        # the repo root has to be on sys.path: run from there with -m.
        from wand import dollar

        root = Path(__file__).resolve().parent.parent
        templates = load_templates(root, dollar.normalize)
        if not templates:
            raise RuntimeError(f"no {WAND_TEMPLATES} in {root}; run wand/record.py first")

        self.reader = StrokeReader(templates, dollar.classify, reject=reject)
        self.link = open_link(port, baud)
        labels = sorted({label for label, _ in templates})
        print(f"Wand on {port}: {len(templates)} templates ({', '.join(labels)})")
        if WAND_CALIBRATE_S:
            self.calibrate()

        self.error = ""            # set by the thread if it gives up
        self.opened_at = time.perf_counter()
        self._events: collections.deque = collections.deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    # --- the InputSource protocol --------------------------------------------

    def poll(self, dt: float) -> list[InputEvent]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
        return events

    def send_feedback(self, state: str) -> None:
        """No op. The firmware accepts only "z" and drives its own pen LED.

        Lighting the wand on kill or damage needs a firmware command first. The
        eight FEEDBACK_STATES are already named for it when it exists.
        """

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.link.close()

    # --- extras main.py and draw.py use, not part of the Protocol ------------

    def calibrate(self, timeout: float = WAND_CALIBRATE_S) -> bool:
        """Make the wand re-measure its gyro bias. Blocks a couple of seconds.

        Two ways in, because the two transports differ:

        Wi-Fi: send "b". The firmware calibrates on demand and answers with its
        bias line. This is the good path -- it works with the wand on battery,
        across the room, with no cable.

        Serial: there is no "b" over the wire (the firmware only reads UDP), so
        reboot the board instead and let setup() calibrate. The ESP32 auto-reset
        circuit pulls EN low when RTS is asserted while DTR is not, which is
        what esptool does; with DTR left low the chip boots the sketch rather
        than the bootloader.

        Either way, a bias measured while the wand was moving poisons every
        stroke for the whole session, so it is worth the two seconds.

        Called before the reader thread starts, so it owns the link alone.
        """
        print("Put the wand down and hold it still, calibrating...")
        if hasattr(self.link, "rts"):
            self.link.dtr = False
            self.link.rts = True            # EN low: held in reset
            time.sleep(0.15)
            self.link.reset_input_buffer()  # drop the pre-reboot stream
            self.link.rts = False           # EN high: boot the sketch
        else:
            self.link.write(b"b")

        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            line = self.link.readline().decode(errors="ignore").strip()
            if not line.startswith("#"):
                continue                    # P lines, stale or already running
            print("  " + line)
            # A reboot ends with "# ready"; a "b" only answers with the bias.
            # Either proves the gyro was just averaged, which is the point.
            if "ready" in line or "bias" in line:
                return True
        print("  no answer in time. Keeping whatever bias the wand powered on"
              " with. Over Wi-Fi, check this PC is joined to the wand network.")
        return False

    def recenter(self) -> None:
        """Zero the integrated position. It drifts; this is the cure."""
        self.link.write(b"z")

    def snapshot(self):
        """(live stroke, last stroke, last shape, seconds since it, status).

        Read without the lock. The thread only appends to the live stroke or
        rebinds it, so the worst case is a preview one point behind, and taking
        the lock here would put the game thread back behind classify().
        """
        reader = self.reader
        return (
            list(reader.stroke),
            list(reader.last),
            reader.last_name,
            time.perf_counter() - reader.last_at if reader.last_at else 999.0,
            self.status(),
        )

    def status(self) -> str:
        """Empty when the wand is usable, otherwise why it is not."""
        if self.error:
            return "WAND ERROR"
        if self.reader.ready:
            return ""
        # No P line yet. For the first few seconds that is the firmware
        # calibrating; after that the port is open but nothing is talking,
        # which means the wrong board or the wrong baud rate.
        if time.perf_counter() - self.opened_at < WAND_WARMUP_S:
            return "HOLD STILL"
        return "NO WAND DATA"

    # --- the thread ----------------------------------------------------------

    def _watch(self) -> None:
        while not self._stop.is_set():
            try:
                line = self.link.readline().decode(errors="ignore")
            except Exception as error:          # unplugged mid game
                self.error = f"disconnected: {error}"
                print(f"Wand {self.error}")
                return
            if not line:
                continue                        # readline timed out, still alive
            # feed() runs outside the lock: classifying a stroke costs tens of
            # milliseconds and would stall the game thread in poll(). Guarded,
            # because a thread that dies here takes the wand with it silently.
            try:
                events = self.reader.feed(line)
            except Exception as error:
                self.error = f"reader failed on {line.strip()!r}: {error}"
                print(f"Wand {self.error}")
                continue
            if events:
                with self._lock:
                    self._events.extend(events)


# --- the camera --------------------------------------------------------------


def lane_from_x(x: int, width: int, current: int, deadzone: int) -> int:
    """Lane 0..2 for a pixel x, with hysteresis so a lane edge does not flicker.

    Same mapping as root game.py's lane_of, plus the deadzone: the lane only
    changes once x is deadzone px past the edge it just crossed. Without that, a
    player standing on a lane line flips the character every single frame.
    """
    lane = min(LANES - 1, max(0, x * LANES // width))
    if lane == current:
        return lane
    lane_w = width / LANES
    edge = lane * lane_w if lane > current else (lane + 1) * lane_w
    return lane if abs(x - edge) >= deadzone else current


class CameraSource:
    """Move the character by moving your body. Emits LaneChange and nothing else.

    A daemon thread owns the camera. camera.read() blocks until the next webcam
    frame and pose.process() costs another 20-30ms on top; run on the game loop
    that is under 20 FPS. Both release the GIL, so off the loop the game holds
    60 and simply reads whatever the thread saw last.

    Landmarks come from perception.read_points, scaled to the preview size, so
    the lane the game reads and the lane drawn on screen cannot disagree.
    """

    def __init__(self, camera_id: int = CAM_ID, confidence: float = CAM_CONFIDENCE):
        # Deferred on purpose: keyboard-only play must not pay MediaPipe's
        # import, and must still run on a machine that has neither it nor a
        # webcam. perception is a root module, so run from the repo root.
        import cv2

        from perception import read_points

        self._cv2 = cv2
        self._read_points = read_points
        self.confidence = confidence
        self.camera = cv2.VideoCapture(camera_id)
        if not self.camera.isOpened():
            self.camera.release()
            raise RuntimeError(f"cannot open webcam {camera_id}")

        self.lane = 1                 # the lane the game already knows about
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._lane: int | None = None      # what the thread last saw
        self._frame: bytes | None = None   # preview, CAM_W x CAM_H RGB
        self._points: dict = {}
        self._message = "STARTING CAMERA"
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    # --- the InputSource protocol --------------------------------------------

    def poll(self, dt: float) -> list[InputEvent]:
        with self._lock:
            lane = self._lane
        if lane is None or lane == self.lane:
            return []
        self.lane = lane
        return [LaneChange(lane)]

    def send_feedback(self, state: str) -> None:
        """No op. Nothing on a webcam lights up. See WandSource."""

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)
        self.camera.release()

    # --- extras draw.py uses, not part of the Protocol -----------------------

    def snapshot(self):
        """(preview bytes, points, lane, message), in one lock acquire."""
        with self._lock:
            return self._frame, self._points, self._lane, self._message

    # --- the thread ----------------------------------------------------------

    def _watch(self) -> None:
        # Built here, not in __init__: a MediaPipe graph belongs to the thread
        # that made it.
        import mediapipe as mp

        cv2 = self._cv2
        lane_w = CAM_W // LANES
        held = 1                 # hysteresis base; survives a dropout
        lane: int | None = None
        points: dict = {}
        seen_at = 0.0

        with mp.solutions.pose.Pose(
            smooth_landmarks=True,
            min_detection_confidence=self.confidence,
            min_tracking_confidence=self.confidence,
        ) as pose:
            while not self._stop.is_set():
                ok, frame = self.camera.read()
                if not ok:
                    self._publish(None, {}, None, "NO CAMERA FRAME")
                    return
                # Mirror, or stepping right moves the player left.
                frame = cv2.flip(frame, 1)
                fresh = self._read_points(pose, frame, CAM_W, CAM_H, self.confidence)

                # Hold the last complete reading briefly: a detection blip must
                # not freeze the player mid wave. Lifted from root game.py.
                now = time.perf_counter()
                if {"nose", "left", "right"} <= fresh.keys():
                    points, seen_at = fresh, now
                elif now - seen_at > CAM_GRACE_S:
                    points = {}

                if not points:
                    lane, message = None, "STEP INTO FRAME"
                else:
                    mid = (points["left"][0] + points["right"][0]) // 2
                    lane = held = lane_from_x(mid, CAM_W, held, CAM_DEADZONE_PX)
                    # Root game.py froze the player here, because it needed the
                    # shoulder *span* to decide what you were straddling. This
                    # only needs the midpoint, which stays meaningful up close,
                    # so warn about the framing but keep tracking: sitting at a
                    # desk to test must not lock you out.
                    wide = abs(points["left"][0] - points["right"][0]) > lane_w
                    message = "STEP BACK" if wide else ""

                preview = cv2.cvtColor(
                    cv2.resize(frame, (CAM_W, CAM_H)), cv2.COLOR_BGR2RGB
                )
                self._publish(preview.tobytes(), points, lane, message)

    def _publish(self, frame, points, lane, message) -> None:
        with self._lock:
            self._frame = frame
            self._points = points
            self._lane = lane
            self._message = message
