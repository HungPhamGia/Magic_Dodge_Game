"""All the rules, and nothing else. This file never imports pygame.

Reading order: what drives the game, the objects, the beat cycle, the waves,
then the state machine over all of it. The input contract lives here rather
than in inputs.py so that this file depends on nothing: sources import the
game, never the other way round. Swapping the keyboard for a wand or a camera
therefore changes nothing below.
"""

import itertools
import random
from dataclasses import dataclass
from typing import Protocol

from .config import (
    BOLT_TRAVEL_S,
    CAST_COOLDOWN_MS,
    COMBO_CAP,
    COMBO_STEP,
    EARLY_KILL_Y,
    EMPOWER_SPEED_MULT,
    IFRAME_MS,
    LANES,
    MISFIRE_LOCKOUT_MS,
    PLAYER_HP,
    SCORE_EARLY_KILL,
    SCORE_KILL,
    SCORE_WALL_DODGE,
    SHAKE_MS,
    WAVE_BREAK_S,
    WAVE_ENDLESS,
    WAVE_FLOOR_S,
    WAVES,
)

# =============================================================================
# What drives the game. Any device that produces these events can play it;
# see inputs.py for the keyboard and for the wand and camera seams.
# =============================================================================


@dataclass
class LaneChange:
    lane: int                    # 0, 1, or 2


@dataclass
class CastStarted:
    pass


@dataclass
class CastResolved:
    shape: str | None            # "triangle" | "circle" | "square" | None
    confidence: float            # 0.0 to 1.0
    duration_ms: int
    ok: bool                     # False means misfire


InputEvent = LaneChange | CastStarted | CastResolved

# send_feedback is called with exactly one of these on every state change.
FEEDBACK_STATES = (
    "idle", "channeling", "recognized", "misfire",
    "cooldown", "damage", "wave_clear", "game_over",
)


class InputSource(Protocol):
    def poll(self, dt: float) -> list[InputEvent]: ...
    def send_feedback(self, state: str) -> None: ...
    def close(self) -> None: ...


# =============================================================================
# The objects. Positions are grid units, never pixels.
# =============================================================================


@dataclass
class Player:
    lane: int = 1
    hp: int = 3


@dataclass
class Threat:
    lane: int
    y: float                 # 0.0 at the top, 1.0 at the player row
    kind: str                # "monster" | "wall"
    shape: str | None        # monsters only, None for walls
    speed: float             # y units per second
    empowered: bool = False
    group_id: int | None = None   # walls spanning two lanes share this


@dataclass
class Bolt:
    lane: int
    y: float                 # starts at 1.0, travels toward 0.0
    shape: str
    # The CastResolved that fired this bolt, carried along so the log line can
    # pair a cast with the outcome it eventually produced.
    cast: object | None = None


# =============================================================================
# The beat cycle. Each spell beats one shape and loses to the other.
# =============================================================================

BEATS = {"triangle": "circle", "circle": "square", "square": "triangle"}

BOLT_SPEED = 1.0 / BOLT_TRAVEL_S      # y units per second, upward

PLAY, WAVE_BREAK, GAME_OVER = "PLAY", "WAVE_BREAK", "GAME_OVER"


# =============================================================================
# The waves. A spawn is a full row: monster_lanes lanes hold a monster, every
# remaining lane is walled off. So a row names the lanes you may stand in, and
# standing there costs you the kill.
#
# The curve itself is config.WAVES, one row per wave. Nothing is computed here.
# =============================================================================

CROWDED_Y = 0.15          # a row only starts once the last one has cleared this

_group_ids = itertools.count()


def wave_config(wave: int) -> dict:
    """The row for this wave. Past the table, the last row scaled by WAVE_ENDLESS.

    A table on its own would plateau at its last row, so a good player would
    never die. One multiplier past the last hand written wave keeps it closing,
    floored so the fall time cannot walk down to zero.
    """
    if wave <= len(WAVES):
        return WAVES[wave - 1]
    last = WAVES[-1]
    scale = WAVE_ENDLESS ** (wave - len(WAVES))
    return {
        **last,
        "fall": max(WAVE_FLOOR_S, last["fall"] * scale),
        "gap": max(WAVE_FLOOR_S, last["gap"] * scale),
    }


class Spawner:
    def __init__(self, wave: int, rng=random):
        self.wave = wave
        self.config = wave_config(wave)
        # Speed is derived from fall time, never the other way round: a row is
        # tuned by how long you get to react to it.
        self.speed = 1.0 / self.config["fall"]
        self.interval = self.config["gap"]
        self.remaining = self.config["rows"]
        self.timer = 0.0
        self.rng = rng

    @property
    def done(self) -> bool:
        return self.remaining == 0

    def tick(self, dt: float, threats: list[Threat]) -> list[Threat]:
        if self.done:
            return []
        self.timer -= dt
        if self.timer > 0:
            return []
        self.timer = self.interval
        spawned = self._row(threats)
        if spawned:
            self.remaining -= 1     # a blocked spawn costs no budget, it retries
        return spawned

    def _row(self, threats: list[Threat]) -> list[Threat]:
        # A row fills every lane, so it waits for the previous one to drop clear.
        if any(t.y < CROWDED_Y for t in threats):
            return []

        lanes = list(range(LANES))
        self.rng.shuffle(lanes)
        monsters = lanes[: self.config["monster_lanes"]]

        row = [
            Threat(
                lane=lane,
                y=0.0,
                kind="monster",
                shape=self.rng.choice(self.config["shapes"]),
                speed=self.speed,
            )
            for lane in monsters
        ]
        # At least one lane always holds a monster, so the walls in a row can
        # never cover all three.
        return row + self._walls(sorted(set(lanes) - set(monsters)))

    def _walls(self, lanes: list[int]) -> list[Threat]:
        """Adjacent wall lanes share a group_id so they draw as one block."""
        walls: list[Threat] = []
        group, previous = None, None
        for lane in lanes:
            if previous is None or lane != previous + 1:
                group = next(_group_ids)
            walls.append(
                Threat(
                    lane=lane, y=0.0, kind="wall", shape=None,
                    speed=self.speed, group_id=group,
                )
            )
            previous = lane
        return walls


# =============================================================================
# The state machine. PLAY -> WAVE_BREAK -> PLAY -> ... -> GAME_OVER
# =============================================================================


def _blank_stats() -> dict:
    return {
        "casts": 0, "kills": 0, "misfires": 0, "blocks": 0,
        "empowers": 0, "damage_taken": 0, "max_combo": 1.0,
    }


class Game:
    def __init__(self, source, log=None, rng=random):
        self.source = source
        self.log = log
        self.rng = rng
        self.reset()

    def reset(self) -> None:
        self.player = Player(hp=PLAYER_HP)
        self.threats: list[Threat] = []
        self.bolts: list[Bolt] = []
        self.wave = 1
        self.score = 0
        self.combo = 1.0
        self.state = WAVE_BREAK       # wave 1 starts on the banner
        self.break_left = WAVE_BREAK_S
        self.spawner = None
        self.t_ms = 0.0
        self.wave_start_ms = 0.0
        self.iframe_ms = 0.0
        self.shake_ms = 0.0
        self.cooldown_ms = 0.0
        self.misfire_ms = 0.0         # drives the on screen MISFIRE flash
        self.channeling = False
        self.stats = _blank_stats()
        self.feedback = None

    # --- one frame, in this exact order --------------------------------------

    def update(self, dt: float) -> None:
        self.t_ms += dt * 1000.0
        for timer in ("iframe_ms", "shake_ms", "cooldown_ms", "misfire_ms"):
            setattr(self, timer, max(0.0, getattr(self, timer) - dt * 1000.0))

        events = self.source.poll(dt)                                   # 1

        if self.state == GAME_OVER:
            self._emit("game_over")
            return

        if self.state == WAVE_BREAK:
            for event in events:
                if isinstance(event, LaneChange):
                    self.player.lane = event.lane
            self.break_left -= dt
            if self.break_left <= 0:
                self._start_wave()
            self._emit("idle")
            return

        damaged = misfired = recognized = False

        for event in events:                                            # 2, 3
            if isinstance(event, LaneChange):
                self.player.lane = event.lane
            elif isinstance(event, CastStarted):
                self.channeling = True
            elif isinstance(event, CastResolved):
                self.channeling = False
                self.stats["casts"] += 1
                if event.ok:
                    self.bolts.append(
                        Bolt(lane=self.player.lane, y=1.0, shape=event.shape, cast=event)
                    )
                    self.cooldown_ms = CAST_COOLDOWN_MS
                    recognized = True
                else:
                    # Only a wand can produce this today, see inputs.WandSource.
                    self.stats["misfires"] += 1
                    self.cooldown_ms = self.misfire_ms = MISFIRE_LOCKOUT_MS
                    self._break_combo()
                    self._log_cast(event, "misfire", None, None)
                    misfired = True

        for bolt in list(self.bolts):                                   # 4
            y_prev = bolt.y
            bolt.y -= BOLT_SPEED * dt
            target = self._swept_target(bolt, y_prev)
            if target is not None:
                self._resolve_hit(bolt, target)
                self.bolts.remove(bolt)
            elif bolt.y <= 0.0:
                self._log_cast(bolt.cast, "no_target", None, None)
                self.bolts.remove(bolt)

        for threat in list(self.threats):                               # 5, 6
            threat.y += threat.speed * dt
            if threat.y < 1.0:
                continue
            if threat.lane == self.player.lane:
                if self.iframe_ms <= 0:
                    self.player.hp -= 1
                    self.stats["damage_taken"] += 1
                    self.iframe_ms = IFRAME_MS
                    self.shake_ms = SHAKE_MS
                    self._break_combo()
                    damaged = True
            elif threat.kind == "wall":
                self.score += SCORE_WALL_DODGE          # combo unchanged
            else:
                self._break_combo()                     # monster escaped
            self.threats.remove(threat)

        self.threats.extend(self.spawner.tick(dt, self.threats))        # 7

        cleared = False                                                 # 8
        if self.player.hp <= 0:
            self._end_wave()
            self.state = GAME_OVER
        elif self.spawner.done and not self.threats:
            self._end_wave()
            self.wave += 1
            self.state = WAVE_BREAK
            self.break_left = WAVE_BREAK_S
            cleared = True

        self._emit(self._feedback_state(damaged, misfired, recognized, cleared))  # 9

    # --- collision -----------------------------------------------------------

    def _swept_target(self, bolt, y_prev):
        """Nearest threat the bolt crossed this frame, so it cannot tunnel."""
        crossed = [
            t for t in self.threats
            if t.lane == bolt.lane and bolt.y <= t.y <= y_prev
        ]
        return max(crossed, key=lambda t: t.y) if crossed else None

    def _resolve_hit(self, bolt, threat) -> None:
        if threat.kind == "wall":
            outcome = "absorbed_by_wall"                # combo unchanged
        elif BEATS[bolt.shape] == threat.shape:
            outcome = "kill"
            base = SCORE_EARLY_KILL if threat.y < EARLY_KILL_Y else SCORE_KILL
            self.score += int(base * self.combo)
            self.combo = min(COMBO_CAP, self.combo + COMBO_STEP)
            self.stats["max_combo"] = max(self.stats["max_combo"], self.combo)
            self.stats["kills"] += 1
            self.threats.remove(threat)
        elif bolt.shape == threat.shape:
            outcome = "block"                           # combo unchanged
            self.stats["blocks"] += 1
            self.shake_ms = SHAKE_MS
        else:
            outcome = "empower"
            if not threat.empowered:                    # never stacks
                threat.speed *= EMPOWER_SPEED_MULT
                threat.empowered = True
            self.stats["empowers"] += 1
            self._break_combo()
        self._log_cast(bolt.cast, outcome, threat.shape, threat.y)

    def _break_combo(self) -> None:
        self.combo = 1.0

    # --- waves ---------------------------------------------------------------

    def _start_wave(self) -> None:
        self.spawner = Spawner(self.wave, self.rng)
        self.stats = _blank_stats()
        self.wave_start_ms = self.t_ms
        self.state = PLAY

    def _end_wave(self) -> None:
        if self.log is None or self.spawner is None:
            return
        self.log.write({
            "t_ms": int(self.t_ms),
            "type": "wave_summary",
            "wave": self.wave,
            **{k: self.stats[k] for k in
               ("casts", "kills", "misfires", "blocks", "empowers", "damage_taken")},
            "max_combo": self.stats["max_combo"],
            "duration_s": round((self.t_ms - self.wave_start_ms) / 1000.0, 1),
        })

    # --- output --------------------------------------------------------------

    def _log_cast(self, cast, outcome, target_shape, hit_y) -> None:
        if self.log is None or cast is None:
            return
        self.log.write({
            "t_ms": int(self.t_ms),
            "wave": self.wave,
            "shape_cast": cast.shape,
            "shape_target": target_shape,
            "confidence": round(cast.confidence, 2),
            "duration_ms": cast.duration_ms,
            "outcome": outcome,
            "hit_y": round(hit_y, 2) if hit_y is not None else None,
            "combo": self.combo,
            "player_hp": self.player.hp,
            "lane": self.player.lane,
        })

    def _feedback_state(self, damaged, misfired, recognized, cleared) -> str:
        if self.state == GAME_OVER:
            return "game_over"
        if damaged:
            return "damage"
        if cleared:
            return "wave_clear"
        if misfired:
            return "misfire"
        if recognized:
            return "recognized"
        if self.channeling:
            return "channeling"
        if self.cooldown_ms > 0:
            return "cooldown"
        return "idle"

    def _emit(self, state: str) -> None:
        if state != self.feedback:
            self.feedback = state
            self.source.send_feedback(state)
