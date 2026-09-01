"""Self check for the pure logic. No pygame, no framework.

Run:  python -m magicdodge.test_magicdodge
"""

import random
from pathlib import Path

from .config import (
    EMPOWER_SPEED_MULT,
    IFRAME_MS,
    LANES,
    PLAYER_HP,
    SCORE_EARLY_KILL,
    SCORE_KILL,
    SCORE_WALL_DODGE,
    WAVES,
)
from .game import (
    BEATS,
    CROWDED_Y,
    FEEDBACK_STATES,
    GAME_OVER,
    Bolt,
    CastResolved,
    Game,
    Spawner,
    Threat,
    wave_config,
)


class FakeSource:
    """Scripted InputSource: one list of events per frame."""

    def __init__(self, frames=None):
        self.frames = list(frames or [])
        self.feedback = []

    def poll(self, dt):
        return self.frames.pop(0) if self.frames else []

    def send_feedback(self, state):
        self.feedback.append(state)

    def close(self):
        pass


class MemoryLog:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


def playing(frames=None, log=None) -> Game:
    """A Game already in PLAY with the spawner switched off."""
    game = Game(FakeSource(frames), log)
    game._start_wave()
    game.spawner.remaining = 0      # tests place their own threats
    return game


def monster(lane=1, y=0.5, shape="circle", speed=0.0) -> Threat:
    return Threat(lane=lane, y=y, kind="monster", shape=shape, speed=speed)


def wall(lane=1, y=0.5, speed=0.0) -> Threat:
    return Threat(lane=lane, y=y, kind="wall", shape=None, speed=speed)


# --- beat cycle ---------------------------------------------------------------


def test_beat_cycle_is_a_cycle():
    assert set(BEATS) == set(BEATS.values()) == {"triangle", "circle", "square"}
    for shape, beaten in BEATS.items():
        assert BEATS[BEATS[beaten]] == shape, "three shapes must close the loop"


def test_kill():
    game = playing()
    game.threats.append(monster(shape="circle"))
    game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle"))
    game.update(0.05)
    assert game.threats == [], "triangle beats circle"
    assert game.bolts == []
    assert game.score == SCORE_KILL
    assert game.combo == 1.5


def test_block():
    game = playing()
    target = monster(shape="circle")
    game.threats.append(target)
    game.combo = 2.0
    game.bolts.append(Bolt(lane=1, y=0.6, shape="circle"))
    game.update(0.05)
    assert game.threats == [target], "same shape does not kill"
    assert game.bolts == [], "the bolt is still spent"
    assert game.score == 0
    assert game.combo == 2.0, "a block leaves the combo alone"
    assert game.shake_ms > 0


def test_empower_once_only():
    game = playing()
    target = monster(shape="circle", speed=1.0)
    game.threats.append(target)
    game.combo = 3.0
    game.bolts.append(Bolt(lane=1, y=0.6, shape="square"))
    game.update(0.05)
    assert target.empowered
    assert target.speed == 1.0 * EMPOWER_SPEED_MULT
    assert game.combo == 1.0, "empowering breaks the combo"

    target.y = 0.5
    game.bolts.append(Bolt(lane=1, y=0.6, shape="square"))
    game.update(0.05)
    assert target.speed == 1.0 * EMPOWER_SPEED_MULT, "the multiplier must not stack"


def test_wall_absorbs_bolt():
    game = playing()
    blocker = wall()
    game.threats.append(blocker)
    game.combo = 2.5
    game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle"))
    game.update(0.05)
    assert game.threats == [blocker], "the wall is unaffected"
    assert game.bolts == []
    assert game.combo == 2.5


# --- swept collision ----------------------------------------------------------


def test_swept_hit_is_nearest_to_player():
    game = playing()
    near, far = monster(y=0.9, shape="circle"), monster(y=0.4, shape="circle")
    game.threats += [far, near]
    game.bolts.append(Bolt(lane=1, y=1.0, shape="triangle"))
    game.update(0.5)                    # one frame crosses the whole field
    assert near not in game.threats, "the closest threat is hit first"
    assert far in game.threats


def test_bolt_cannot_tunnel():
    game = playing()
    target = monster(y=0.5, shape="circle")
    game.threats.append(target)
    game.bolts.append(Bolt(lane=1, y=1.0, shape="triangle"))
    game.update(1.0)                    # a 4 unit step over a 1 unit field
    assert game.threats == [], "a huge dt must not step past the threat"


def test_bolt_ignores_other_lanes():
    game = playing()
    target = monster(lane=0, shape="circle")
    game.threats.append(target)
    game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle"))
    game.update(0.05)
    assert game.threats == [target]


# --- player row ---------------------------------------------------------------


def test_damage_and_iframes():
    game = playing()
    game.combo = 3.0
    game.threats.append(monster(lane=game.player.lane, y=0.99, speed=1.0))
    game.update(0.05)
    assert game.player.hp == PLAYER_HP - 1
    assert game.combo == 1.0, "damage breaks the combo"
    assert game.iframe_ms == IFRAME_MS
    assert game.threats == []

    game.threats.append(monster(lane=game.player.lane, y=0.99, speed=1.0))
    game.update(0.05)
    assert game.player.hp == PLAYER_HP - 1, "invulnerable frames absorb the second hit"


def test_escaped_monster_breaks_combo_without_damage():
    game = playing()
    game.combo = 2.0
    game.threats.append(monster(lane=0, y=0.99, speed=1.0))
    game.player.lane = 2
    game.update(0.05)
    assert game.player.hp == PLAYER_HP
    assert game.combo == 1.0
    assert game.threats == []


def test_wall_dodge_scores_and_keeps_combo():
    game = playing()
    game.combo = 2.0
    game.threats.append(wall(lane=0, y=0.99, speed=1.0))
    game.player.lane = 2
    game.update(0.05)
    assert game.score == SCORE_WALL_DODGE
    assert game.combo == 2.0


def test_game_over_at_zero_hp():
    game = playing()
    game.player.hp = 1
    game.threats.append(monster(lane=game.player.lane, y=0.99, speed=1.0))
    game.update(0.05)
    assert game.state == GAME_OVER


# --- scoring ------------------------------------------------------------------


def test_early_kill_pays_more_and_scales_with_combo():
    game = playing()
    game.combo = 2.0
    game.threats.append(monster(y=0.20, shape="circle"))
    game.bolts.append(Bolt(lane=1, y=0.30, shape="triangle"))
    game.update(0.05)
    assert game.score == int(SCORE_EARLY_KILL * 2.0)


def test_combo_caps():
    game = playing()
    game.threats.append(monster(lane=0, y=0.1))   # keeps the wave from ending
    for _ in range(12):
        game.threats.append(monster(shape="circle"))
        game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle"))
        game.update(0.05)
    assert game.combo == 4.0


def test_misfire_breaks_combo_and_logs():
    log = MemoryLog()
    misfire = CastResolved("triangle", 0.42, 300, False)
    game = playing(frames=[[misfire]], log=log)
    game.combo = 3.0
    game.update(0.016)
    assert game.combo == 1.0
    assert game.bolts == [], "a misfire fires nothing"
    assert log.records[0]["outcome"] == "misfire"
    assert log.records[0]["confidence"] == 0.42


# --- logging ------------------------------------------------------------------


def test_cast_log_shape():
    log = MemoryLog()
    cast = CastResolved("triangle", 0.9, 812, True)
    game = playing(log=log)
    game.threats.append(monster(y=0.5, shape="circle"))
    game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle", cast=cast))
    game.update(0.05)

    line = log.records[0]
    assert line["outcome"] == "kill"
    assert line["shape_cast"] == "triangle"
    assert line["shape_target"] == "circle"
    assert line["duration_ms"] == 812
    assert line["hit_y"] == 0.5
    assert set(line) == {
        "t_ms", "wave", "shape_cast", "shape_target", "confidence", "duration_ms",
        "outcome", "hit_y", "combo", "player_hp", "lane",
    }


def test_no_target_is_logged():
    log = MemoryLog()
    cast = CastResolved("triangle", 0.9, 700, True)
    game = playing(log=log)
    game.bolts.append(Bolt(lane=1, y=0.2, shape="triangle", cast=cast))
    game.update(0.1)
    assert log.records[0]["outcome"] == "no_target"
    assert log.records[0]["shape_target"] is None
    assert log.records[0]["hit_y"] is None


def test_wave_summary_written():
    log = MemoryLog()
    game = playing(log=log)
    game.threats.append(monster(shape="circle"))
    game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle"))
    game.update(0.05)                   # kill empties the field, wave completes
    summary = [r for r in log.records if r.get("type") == "wave_summary"]
    assert len(summary) == 1
    assert summary[0]["kills"] == 1
    assert summary[0]["wave"] == 1
    assert game.wave == 2


def test_feedback_states_are_known_and_deduped():
    game = playing()
    for _ in range(5):
        game.update(0.016)
    assert set(game.source.feedback) <= set(FEEDBACK_STATES)
    assert len(game.source.feedback) < 5, "repeats must not be re-emitted"


# --- spawner ------------------------------------------------------------------


def test_every_row_fills_all_lanes_and_leaves_a_way_through():
    rng = random.Random(1234)
    for wave in range(1, 9):
        spawner = Spawner(wave, rng)
        threats: list[Threat] = []
        rows = 0

        for _ in range(2000):
            crowded = [t for t in threats if t.y < CROWDED_Y]
            row = spawner.tick(0.4, threats)
            if row:
                assert not crowded, "a row spawned on top of the last one"
                rows += 1
                lanes = sorted(t.lane for t in row)
                assert lanes == list(range(LANES)), "every lane must be filled"
                monsters = [t for t in row if t.kind == "monster"]
                walls = [t for t in row if t.kind == "wall"]
                assert monsters, "a row of pure walls is unsurvivable"
                assert len(walls) < LANES, "walls must never cover every lane"
                assert all(t.shape for t in monsters)
                assert all(t.shape is None for t in walls)
            threats += row
            for threat in threats:
                threat.y += threat.speed * 0.4
            threats = [t for t in threats if t.y < 1.0]
            if spawner.done:
                break

        assert spawner.done, f"wave {wave} never exhausted its budget"
        assert rows == spawner.config["rows"]


def test_adjacent_walls_share_a_group():
    spawner = Spawner(4, random.Random(5))       # one monster lane, two walls
    for _ in range(60):
        row = spawner.tick(1.0, [])
        walls = sorted((t.lane, t.group_id) for t in row if t.kind == "wall")
        if len(walls) == 2 and walls[1][0] - walls[0][0] == 1:
            assert walls[0][1] == walls[1][1], "adjacent walls draw as one block"
            return
        if len(walls) == 2:
            assert walls[0][1] != walls[1][1], "split walls are separate blocks"
        spawner.remaining = 1                    # keep it spawning
    raise AssertionError("never produced a two lane wall")


def test_wave_difficulty_increases():
    early, late = Spawner(1, random.Random(0)), Spawner(6, random.Random(0))
    assert late.speed > early.speed
    assert late.interval < early.interval
    assert late.config["monster_lanes"] < early.config["monster_lanes"]
    assert early.remaining == WAVES[0]["rows"]

    # Past the table the last row keeps its shapes but keeps closing, or a good
    # player would reach the plateau and never die.
    endless = Spawner(len(WAVES) + 3, random.Random(0))
    assert endless.config["shapes"] == WAVES[-1]["shapes"]
    assert endless.config["monster_lanes"] == WAVES[-1]["monster_lanes"]
    assert endless.speed > late.speed
    assert endless.interval < late.interval


def test_the_wave_table_only_gets_harder():
    """A typo in one row is the whole risk of hand written waves."""
    from .config import WAVE_FLOOR_S

    for earlier, later in zip(WAVES, WAVES[1:]):
        assert later["fall"] <= earlier["fall"], "a wave must not slow down"
        assert later["gap"] <= earlier["gap"]
        assert later["monster_lanes"] <= earlier["monster_lanes"]
        assert set(earlier["shapes"]) <= set(later["shapes"]), "shapes only add"
        assert later["rows"] >= 1
    # However long you last, the floor is the hard ceiling on difficulty.
    assert wave_config(99)["fall"] >= WAVE_FLOOR_S
    assert wave_config(99)["gap"] >= WAVE_FLOOR_S


# --- cast input ---------------------------------------------------------------
#
# This one reaches into inputs.py, so it imports pygame locally. The rest of the
# file stays pygame free, which is what proves game.py is decoupled.


def test_one_key_casts():
    """A single keypress fires, and the cooldown is the only thing gating it."""
    import pygame

    from .config import CAST_COOLDOWN_MS
    from .inputs import KeyboardSource

    source = KeyboardSource()
    events = []
    source.t_ms = 1000.0

    source._keydown(pygame.K_j, events)
    assert len(events) == 1
    cast = events[0]
    assert cast.ok and cast.shape == "triangle"
    assert cast.duration_ms == 0 and cast.confidence == 1.0

    source._keydown(pygame.K_k, events)
    assert len(events) == 1, "the cooldown is the only cost, but it is real"
    assert 0.0 <= source.cooldown() <= 1.0, "and the HUD can show it"

    source.t_ms += CAST_COOLDOWN_MS
    assert source.cooldown() is None, "the cooldown expires"
    source._keydown(pygame.K_k, events)
    assert len(events) == 2, "and casting works again"


# --- camera input -------------------------------------------------------------


def test_camera_lane_from_x():
    """lane_from_x is the whole camera rule, and it needs no webcam to check."""
    from .config import CAM_DEADZONE_PX, CAM_W
    from .inputs import lane_from_x

    lane_w = CAM_W // LANES
    for x, expected in ((10, 0), (CAM_W // 2, 1), (CAM_W - 10, 2)):
        assert lane_from_x(x, CAM_W, 1, CAM_DEADZONE_PX) == expected, x
    assert lane_from_x(-50, CAM_W, 1, 0) == 0, "clamped, not negative"
    assert lane_from_x(CAM_W + 50, CAM_W, 1, 0) == LANES - 1, "clamped at the top"

    # Standing on the lane 0/1 line: the deadzone has to hold the current lane,
    # or a jittering landmark flips the player every frame.
    edge = lane_w
    assert lane_from_x(edge - 1, CAM_W, 1, CAM_DEADZONE_PX) == 1, "just inside"
    assert lane_from_x(edge + 1, CAM_W, 0, CAM_DEADZONE_PX) == 0, "and from the left"
    assert lane_from_x(edge - CAM_DEADZONE_PX, CAM_W, 1, CAM_DEADZONE_PX) == 0, (
        "but a real step across still moves you"
    )


# --- wand input ---------------------------------------------------------------


def _recorded(shape):
    """One real stroke recorded by record.py, or None on a machine without them."""
    import glob
    import json

    root = Path(__file__).resolve().parent.parent
    files = sorted(glob.glob(str(root / f"strokes_{shape}_*.json")))
    if not files:
        return None
    strokes = json.load(open(files[0], encoding="utf-8"))
    return [tuple(point) for point in strokes[0]]


def _wand_reader():
    import sys

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from wand import dollar

    from .inputs import StrokeReader, load_templates

    templates = load_templates(root, dollar.normalize)
    return StrokeReader(templates, dollar.classify) if templates else None


def test_wand_reads_a_recorded_stroke():
    """Replay real hardware data through the whole wand path, no wand needed."""
    from .game import CastResolved, CastStarted

    stroke = _recorded("circle")
    reader = _wand_reader()
    if stroke is None or reader is None:
        return                      # no recordings on this machine

    def line(point, pen):
        return f"P,{point[0]:.2f},{point[1]:.2f},{pen}"

    reader.feed("# hold still, calibrating")   # firmware chatter is ignored

    events = [e for point in stroke for e in reader.feed(line(point, 1))]
    assert len(events) == 1 and isinstance(events[0], CastStarted), (
        "exactly one CastStarted, raised once the stroke is long enough"
    )

    resolved = reader.feed(line(stroke[-1], 0))
    assert len(resolved) == 1
    cast = resolved[0]
    assert isinstance(cast, CastResolved)
    assert cast.shape == "circle", f"recorded circle classified as {cast.shape}"
    assert cast.ok is True
    assert 0.0 < cast.confidence <= 1.0
    assert cast.duration_ms >= 0


def test_wand_casts_without_the_ready_banner():
    """Joining a board that is already running must still cast.

    The firmware prints "# ready" once, from setup(). Opening the port does not
    reliably reset a CH340, so the banner is usually long gone by the time we
    are listening. Gating on it made the wand dead for the whole session.
    """
    from .game import CastResolved

    stroke = _recorded("square")
    reader = _wand_reader()
    if stroke is None or reader is None:
        return

    # No "#" line ever arrives; the stream starts mid loop().
    events = [
        e for point in stroke
        for e in reader.feed(f"P,{point[0]:.2f},{point[1]:.2f},1")
    ]
    assert events, "a P line is proof enough that calibration finished"
    cast = reader.feed(f"P,{stroke[-1][0]:.2f},{stroke[-1][1]:.2f},0")[0]
    assert isinstance(cast, CastResolved) and cast.shape == "square"


def test_wand_ignores_a_twitch():
    """A flick shorter than WAND_MIN_PTS must not start a cast it never ends."""
    from .config import WAND_MIN_PTS

    reader = _wand_reader()
    if reader is None:
        return
    reader.feed("P,0.00,0.00,0")   # one P line is what marks the board ready

    events = []
    for i in range(WAND_MIN_PTS - 1):
        events += reader.feed(f"P,{i}.00,0.00,1")
    events += reader.feed("P,0.00,0.00,0")
    assert events == [], "a twitch produces nothing, so channeling cannot stick"


def test_wand_survives_drift():
    """A recorded stroke with gyro drift added must still classify."""
    import json
    from pathlib import Path as P

    from wand import dollar

    from .inputs import StrokeReader, detrend, load_templates

    root = P(__file__).resolve().parent.parent
    templates = load_templates(root, dollar.normalize)
    reader = StrokeReader(templates, dollar.classify)

    path = sorted(root.glob("strokes_circle_*.json"))[0]
    clean = json.loads(path.read_text(encoding="utf-8"))[0]
    # 20 deg/s leftward, the reported symptom, at the firmware's 100 Hz.
    drifty = [[x - 20.0 * i * 0.01, y] for i, (x, y) in enumerate(clean)]

    for label, stroke in (("clean", clean), ("drifting", drifty)):
        events = []
        for x, y in stroke:
            events += reader.feed(f"P,{x:.2f},{y:.2f},1")
        events += reader.feed("P,0,0,0")
        cast = events[-1]
        assert cast.shape == "circle", f"{label} stroke read as {cast.shape}"

    # Detrend is what makes that work: without it the drifting one is unusable.
    from .config import WAND_REJECT

    assert dollar.classify(drifty, templates, reject=WAND_REJECT)[0] != "circle"
    assert dollar.classify(detrend(drifty), templates, reject=WAND_REJECT)[0] == "circle"


def test_wand_calibration_over_serial_reboots_the_board():
    """On a COM port, calibrate() pulses RTS and waits for the boot banner."""
    from .inputs import WandSource

    class FakePort:
        """A serial port: it has the modem-control lines a Link does not."""

        def __init__(self, lines):
            self.lines, self.dtr, self.rts, self.flushed = lines, True, True, False
            self.sent = b""

        def __setattr__(self, name, value):
            if name in ("dtr", "rts") and hasattr(self, "sent"):
                self.pulses.append((name, value))
            object.__setattr__(self, name, value)

        pulses = []

        def reset_input_buffer(self):
            self.flushed = True

        def write(self, data):
            self.sent += data

        def readline(self):
            return self.lines.pop(0).encode() if self.lines else b""

    FakePort.pulses = []
    wand = WandSource.__new__(WandSource)        # no link, no thread
    wand.link = FakePort([
        "P,1.00,2.00,0",                         # stale, from before the reboot
        "# hold still, calibrating",
        "# bias gx -1.51 gy 1.70 gz -1.09",
        "# ready",
    ])
    assert wand.calibrate(timeout=2.0) is True
    # EN goes low only while RTS is asserted and DTR is not: that is the reset.
    pulses = wand.link.pulses
    assert pulses.index(("rts", True)) < pulses.index(("rts", False))
    assert ("dtr", False) in pulses, "DTR high would enter the bootloader"
    assert wand.link.flushed, "the pre-reboot stream must be dropped"


def test_wand_calibration_over_wifi_asks_the_firmware():
    """Over UDP there are no modem lines, so it sends the "b" command instead."""
    from .inputs import WandSource

    class FakeLink:
        """wifi.Link: readline, write, close and nothing else."""

        def __init__(self, lines):
            self.lines, self.sent = lines, b""

        def write(self, data):
            self.sent += data

        def readline(self):
            return self.lines.pop(0).encode() if self.lines else b""

    wand = WandSource.__new__(WandSource)
    # A "b" is answered with the bias line, never with "# ready": accepting
    # only "ready" would time out on every Wi-Fi start.
    wand.link = FakeLink(["# recalibrating", "# bias gx -1.51 gy 1.70 gz -1.09"])
    assert wand.calibrate(timeout=2.0) is True
    assert wand.link.sent == b"b", "the firmware only recalibrates on b"

    # A wand that is not on the network must time out, not hang or claim success.
    silent = WandSource.__new__(WandSource)
    silent.link = FakeLink([])
    assert silent.calibrate(timeout=0.2) is False


def test_wand_confidence_falls_with_the_score():
    from .inputs import wand_confidence

    assert wand_confidence(0.0, 60.0) == 1.0
    assert wand_confidence(60.0, 60.0) == 0.0
    assert wand_confidence(999.0, 60.0) == 0.0, "clamped, never negative"
    assert wand_confidence(12.0, 60.0) > wand_confidence(24.0, 60.0)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
