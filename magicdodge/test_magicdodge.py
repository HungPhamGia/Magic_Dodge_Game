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


def killer(shape: str) -> str:
    """The spell that kills this shape, read off the rules themselves.

    The tests below are about mechanics, not about which element wins, so they
    ask for the pair rather than naming it. Rewriting the cycle then costs one
    edit in game.BEATS instead of a sweep through this file. The cycle itself
    is pinned literally in test_beat_cycle_matches_the_elements, which is the
    one place that would catch BEATS being wrong.
    """
    return next(spell for spell, beaten in BEATS.items() if beaten == shape)


def loser(shape: str) -> str:
    """The spell this shape beats, so casting it empowers the monster."""
    return BEATS[shape]


# --- beat cycle ---------------------------------------------------------------


def test_beat_cycle_matches_the_elements():
    """The rule as a player meets it, in the elements the art draws.

    circle is water, triangle is fire, square is earth.
    """
    assert BEATS["circle"] == "triangle", "water douses fire"
    assert BEATS["triangle"] == "square", "fire scorches earth"
    assert BEATS["square"] == "circle", "earth soaks up water"


def test_beat_cycle_is_a_cycle():
    assert set(BEATS) == set(BEATS.values()) == {"triangle", "circle", "square"}
    for shape, beaten in BEATS.items():
        assert BEATS[BEATS[beaten]] == shape, "three shapes must close the loop"


def test_kill():
    game = playing()
    game.threats.append(monster(shape="circle"))
    game.bolts.append(Bolt(lane=1, y=0.6, shape=killer("circle")))
    game.update(0.05)
    assert game.threats == [], "the spell that beats it kills it"
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
    game.bolts.append(Bolt(lane=1, y=0.6, shape=loser("circle")))
    game.update(0.05)
    assert target.empowered
    assert target.speed == 1.0 * EMPOWER_SPEED_MULT
    assert game.combo == 1.0, "empowering breaks the combo"

    target.y = 0.5
    game.bolts.append(Bolt(lane=1, y=0.6, shape=loser("circle")))
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
    game.bolts.append(Bolt(lane=1, y=1.0, shape=killer("circle")))
    game.update(0.5)                    # one frame crosses the whole field
    assert near not in game.threats, "the closest threat is hit first"
    assert far in game.threats


def test_bolt_cannot_tunnel():
    game = playing()
    target = monster(y=0.5, shape="circle")
    game.threats.append(target)
    game.bolts.append(Bolt(lane=1, y=1.0, shape=killer("circle")))
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
    game.bolts.append(Bolt(lane=1, y=0.30, shape=killer("circle")))
    game.update(0.05)
    assert game.score == int(SCORE_EARLY_KILL * 2.0)


def test_combo_caps():
    game = playing()
    game.threats.append(monster(lane=0, y=0.1))   # keeps the wave from ending
    for _ in range(12):
        game.threats.append(monster(shape="circle"))
        game.bolts.append(Bolt(lane=1, y=0.6, shape=killer("circle")))
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
    game.threats.append(monster(y=0.5, shape="square"))
    game.bolts.append(Bolt(lane=1, y=0.6, shape="triangle", cast=cast))
    game.update(0.05)

    line = log.records[0]
    assert line["outcome"] == "kill"
    assert line["shape_cast"] == "triangle"
    assert line["shape_target"] == "square", "fire scorches earth"
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
    game.bolts.append(Bolt(lane=1, y=0.6, shape=killer("circle")))
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
    from .config import WAVE_FLOOR_S, WAVE_GAP_FLOOR_S

    for earlier, later in zip(WAVES, WAVES[1:]):
        assert later["fall"] <= earlier["fall"], "a wave must not slow down"
        assert later["gap"] <= earlier["gap"]
        assert later["monster_lanes"] <= earlier["monster_lanes"]
        assert set(earlier["shapes"]) <= set(later["shapes"]), "shapes only add"
        assert later["rows"] >= 1
    # A gap under CROWDED_Y * fall buys nothing: the row is refused and retried,
    # so the wave gets longer instead of denser. Tuning below it looks like a
    # difficulty change and is not one.
    for wave, row in enumerate(WAVES, 1):
        assert row["gap"] > CROWDED_Y * row["fall"], (
            f"wave {wave} gap {row['gap']} is under the crowding floor "
            f"{CROWDED_Y * row['fall']:.2f}; rows cannot arrive that fast")

    # However long you last, the floors are the hard ceiling on difficulty.
    assert wave_config(99)["fall"] >= WAVE_FLOOR_S
    assert wave_config(99)["gap"] >= WAVE_GAP_FLOOR_S
    # ...and the tail must keep closing across the join, not step back. One
    # shared floor used to raise the gap here and make wave 7 easier than 6.
    assert wave_config(len(WAVES) + 1)["gap"] < WAVES[-1]["gap"]
    assert wave_config(len(WAVES) + 1)["fall"] < WAVES[-1]["fall"]


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


# --- artwork ------------------------------------------------------------------
#
# Also pygame, so also a local import. Renders one frame headless: this is what
# fails if a filename in draw.SPRITES, the element/ path, or an alpha channel
# ever breaks.


def test_every_sprite_loads_and_a_frame_renders():
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame

    from . import draw
    from .config import FIELD_W, WINDOW_H
    from .inputs import KeyboardSource

    pygame.init()
    screen = pygame.display.set_mode((FIELD_W, WINDOW_H))

    game = playing()
    game.threats = [monster(lane=0, shape="triangle"), monster(lane=1, shape="circle"),
                    monster(lane=2, shape="square", speed=0.0), wall(lane=2, y=0.2)]
    game.threats[2].empowered = True
    game.bolts = [Bolt(lane=i, y=0.6, shape=s)
                  for i, s in enumerate(("triangle", "circle", "square"))]
    draw.frame(screen, game, KeyboardSource())      # loads element/ on first call

    assert draw._backdrop is not None, "element/Background.png did not load"
    for name in draw.SPRITES:
        art = draw._sprite(name, 130)
        assert art is not None, f"no art for {name}"
        assert art.get_height() == 130
        assert art.get_bounding_rect().width > 1, f"{name} is blank or fully clear"

    # Lanes sit on the backdrop's floor, so nothing may hang over the masonry.
    # This is what fails if MONSTER_SIZE or PLAYER_ART_H is raised too far.
    from .config import LANES, PATH_LEFT, PATH_RIGHT

    widest = max([draw.MONSTER_SIZE] + [draw._sprite(f"player{i}", draw.PLAYER_ART_H).get_width()
                                        for i in range(len(draw.WALK))])
    assert draw.lane_center(0) - widest // 2 >= PATH_LEFT, "lane 0 overhangs the wall"
    assert draw.lane_center(LANES - 1) + widest // 2 <= PATH_RIGHT, "lane 2 overhangs"

    # The spell art carries a big invisible glow and each file encloses its
    # content differently, so _load_art crops before scaling. Without that the
    # earth bolt renders about 1.7x wider than the water one.
    from .config import LANE_W

    widths = [draw._sprite(f"bolt_{s}", draw.BOLT_ART_H).get_width()
              for s in ("triangle", "circle", "square")]
    assert max(widths) <= 1.4 * min(widths), f"bolt art badly mismatched: {widths}"
    assert max(widths) < LANE_W, f"a bolt is wider than its lane: {widths}"

    # The wave break rows are read off BEATS, but which element they open on
    # is a presentation choice that nothing else would catch if it slipped.
    assert draw.CYCLE[0] == "triangle", "wave break rows start on fire"
    rows = [(spell, BEATS[spell]) for spell in draw.CYCLE]
    assert len(rows) == len(BEATS)
    assert {s for s, _ in rows} == {m for _, m in rows} == set(BEATS), rows

    draw.frame(screen, game, KeyboardSource())      # again, off the caches

    from .game import WAVE_BREAK

    game.state = WAVE_BREAK                         # exercises _matchups
    game.break_left = 4.2
    draw.frame(screen, game, KeyboardSource())
    pygame.quit()


def test_the_start_screen_waits_for_the_watch():
    """A run must not begin, or be uploaded, on an invented heart rate trace."""
    from .game import MENU, WAVE_BREAK
    from .heart_rate import HeartRateMonitor

    class Waiting:
        state, wave, shake_ms, bolts = MENU, 1, 0, []

    game = Game(FakeSource(), MemoryLog())
    assert game.state == MENU, "a fresh game opens on the start screen"

    # The menu holds: no clock, no spawner, and he does not walk yet.
    for _ in range(60 * 3):
        game.update(1 / 60)
    assert game.state == MENU and game.scroll == 0.0 and not game.threats

    # A watch was asked for and has not reported, so the gate stays shut.
    hr = HeartRateMonitor(device_wanted=True)
    hr.update(1 / 60, Waiting())
    assert not hr.ready(), "no reading yet"

    # main.py only calls start() when ready(), so the state must not move.
    if hr.ready():
        game.start()
    assert game.state == MENU

    # First reading opens it.
    hr.push(88)
    hr.update(1 / 60, Waiting())
    assert hr.ready() and not hr.simulated
    game.start()
    assert game.state == WAVE_BREAK, "SPACE opens wave 1 on the banner"
    game.start()
    assert game.state == WAVE_BREAK, "start() elsewhere is a no op"

    # A watch that falls off in the menu shuts the gate again.
    from .heart_rate import DEVICE_GRACE_S
    for _ in range(int(60 * DEVICE_GRACE_S) + 120):
        hr.update(1 / 60, Waiting())
    assert not hr.ready(), "a dropped watch cannot start a run"

    # No watch asked for: nothing to wait for, so a keyboard playtest starts.
    assert HeartRateMonitor().ready()


def test_a_real_watch_beats_the_simulation():
    """A pushed reading must survive the update loop, and outlive its gaps.

    It used to not: the easing that shapes the simulation was applied to real
    readings too, so at 60 FPS a watch reporting once a second contributed
    0.3% of the displayed value. A watch pinned at 150 showed the simulation's
    133 and the uploaded record called it real.
    """
    from .heart_rate import DEVICE_GRACE_S, HeartRateMonitor

    class Playing:                       # a wave 3 PLAY state, simulated ~133
        state, wave, shake_ms, bolts = "PLAY", 3, 0, []

    game = Playing()
    hr = HeartRateMonitor()
    assert hr.simulated, "no device yet"

    # Scanning and connecting takes about seven seconds, simulated meanwhile.
    for _ in range(60 * 8):
        hr.update(1 / 60, game)
    assert hr.samples, "the stand-in covers the gap"
    ramped = hr.current()

    # A watch reporting once a second while the game runs at 60.
    for frame in range(60 * 30):
        if frame % 60 == 0:
            hr.push(150)
        hr.update(1 / 60, game)
    assert hr.current() == 150, f"the watch says 150, the game shows {hr.current()}"
    assert not hr.simulated
    summary = hr.summarize()
    assert summary["mean_bpm"] > 145, "the log records the watch, not the sim"
    # The simulated prelude must not survive into the record: it ramps, so it
    # used to hand the coach a peak the player never had.
    assert abs(summary["peak_bpm"] - 150) <= 2, (      # +/- the sampling noise
        f"invented prelude in the record: {summary}")
    assert ramped != 150, "the prelude really was a different number"

    # It holds the last reading between reports rather than drifting back.
    for _ in range(int(60 * DEVICE_GRACE_S * 0.8)):
        hr.update(1 / 60, game)
    assert hr.current() == 150, "held through a gap shorter than the grace"
    assert not hr.simulated

    # Past the grace the watch is gone, so the simulation takes over again and
    # says so -- the number must never freeze on a watch that walked away.
    for _ in range(60 * 60):
        hr.update(1 / 60, game)
    assert hr.simulated, "a dropped watch falls back to the simulation"
    assert hr.current() < 145, "and actually moves off the last reading"


def test_the_road_and_the_stride_run_at_the_threat_speed():
    """Walking, falling and the walk cycle are all one number: walk_speed.

    If the road and the threats ever drift apart the monsters slide along the
    ground instead of standing on it, which is the whole illusion.
    """
    from . import draw
    from .config import FIELD_BOTTOM, FIELD_TOP
    from .game import PLAY

    game = playing()
    assert game.scroll == 0.0
    assert game.walk_speed == game.spawner.speed, "road and threats must agree"

    # Read it first: this wave's budget is empty, so the update finishes the
    # wave and walk_speed is the next wave's by the time it returns.
    slow = game.walk_speed
    game.update(1.0)
    assert abs(game.scroll - slow) < 1e-9, "one second of walking"

    # A later wave walks faster, with no second knob to keep in step.
    game.wave = len(WAVES)
    assert game.walk_speed > slow

    # Dead men do not walk.
    game.state = GAME_OVER
    stopped = game.scroll
    game.update(1.0)
    assert game.scroll == stopped

    # The cycle reaches every frame and wraps, never indexing past the art.
    game.state = PLAY
    seen = set()
    for step in range(len(draw.WALK) * 3):
        game.scroll = step * draw.WALK_STEP_PX / (FIELD_BOTTOM - FIELD_TOP)
        seen.add(int(draw.road_px(game) / draw.WALK_STEP_PX) % len(draw.WALK))
    assert seen == set(range(len(draw.WALK))), seen


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"  ok  {test.__name__}")
    print(f"\n{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
