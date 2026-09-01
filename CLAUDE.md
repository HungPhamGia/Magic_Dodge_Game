# MagicDodge — Keyboard Build Spec

Build a three lane dodging and spellcasting game in Python with pygame-ce. Keyboard input only.

This build exists to prove the game loop is fun and to create a clean seam that a physical wand and a camera plug into later. Follow the input abstraction section exactly. Everything else is negotiable.

---

## Non goals

Do not build any of these. They are later milestones.

- Wand, serial, BLE, IMU, gesture recognition
- Camera, MediaPipe, pose tracking
- LLM calls of any kind
- Pulse sensor or heart rate
- Boss waves
- Sound effects or music
- Menus, settings screens, save files
- Sprite assets or image loading
- Particle systems

Render everything with pygame drawing primitives. No external art.

---

## Stack

- Python 3.11
- pygame-ce (`pip install pygame-ce`)
- Standard library only otherwise

No other dependencies. The wand will use pyserial and the camera will use mediapipe later, so keep the project a plain Python package with no framework around it.

---

## File layout

```
magicdodge/
  main.py            entry point, window, clock, top level loop
  config.py          every tunable constant, nothing else
  game.py            state machine and update order
  entities.py        Player, Threat, Bolt
  spawner.py         wave definitions and spawn timing
  render.py          all drawing
  hud.py             HP, score, combo, legend, channel bar
  logger.py          JSONL cast log
  inputs/
    __init__.py
    base.py          InputSource protocol and event dataclasses
    keyboard.py      KeyboardSource
logs/                created at runtime, gitignored
```

`game.py` must not import pygame. It receives input events and returns state. Only `main.py`, `render.py`, and `inputs/keyboard.py` touch pygame.

---

## Input abstraction

This is the most important part of the build. Write it first.

```python
# inputs/base.py
from dataclasses import dataclass
from typing import Protocol

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

class InputSource(Protocol):
    def poll(self, dt: float) -> list[InputEvent]: ...
    def send_feedback(self, state: str) -> None: ...
    def close(self) -> None: ...
```

`send_feedback` is called by the game on every state change with one of these strings. `KeyboardSource` implements it as a no op. It becomes the LED and buzzer driver when the wand arrives.

```
"idle" | "channeling" | "recognized" | "misfire" | "cooldown" | "damage" | "wave_clear" | "game_over"
```

`game.py` must work if you swap `KeyboardSource` for any other `InputSource`. Never read the keyboard outside `inputs/keyboard.py`.

---

## Controls

Two cast modes. Toggle with F1. Default is channel mode.

### Channel mode (default)

| Key | Action |
| --- | --- |
| Left arrow or A | Move one lane left |
| Right arrow or D | Move one lane right |
| Hold Space | Begin channeling |
| 1 while Space held | Select Triangle |
| 2 while Space held | Select Circle |
| 3 while Space held | Select Square |
| Release Space | Fire the selected shape |
| Esc | Quit |

Rules inside `KeyboardSource`:

- Emit `CastStarted` on Space press. Ignore Space if a cooldown or lockout is active.
- Track hold duration in milliseconds.
- On release, decide the outcome:
  - Duration below `CHANNEL_MIN_MS` gives `ok=False`, `shape=None`
  - No shape key pressed during the hold gives `ok=False`, `shape=None`
  - Otherwise roll a random float. Below `FAKE_MISFIRE_RATE` gives `ok=False` but keeps the shape for logging
  - Otherwise `ok=True`
- If the hold passes `CHANNEL_MAX_MS`, auto resolve as a misfire without waiting for release, then swallow the eventual release.
- Fake confidence: successful cast draws uniform 0.78 to 0.98. Misfire draws uniform 0.30 to 0.70.

The minimum channel time and the random misfire are deliberate. They make keyboard play cost the same time and carry the same failure rate as the real wand, so difficulty numbers tuned now still apply later. Do not remove them.

### Instant mode (debug)

Keys 1, 2, 3 fire immediately. No channel, no misfire, confidence fixed at 1.0, duration fixed at 0. For testing collision and waves quickly.

---

## Beat cycle

| Spell | Beats | Loses to |
| --- | --- | --- |
| Triangle | Circle | Square |
| Circle | Square | Triangle |
| Square | Triangle | Circle |

```python
BEATS = {"triangle": "circle", "circle": "square", "square": "triangle"}
```

Colors, used identically for the spell, the monster, and the legend:

```python
COLORS = {
    "triangle": (232,  76,  76),   # red
    "circle":   ( 74, 144, 226),   # blue
    "square":   ( 92, 184, 108),   # green
}
BG        = ( 18,  18,  24)
GRID      = ( 40,  40,  52)
PLAYER    = (240, 240, 245)
WALL      = (110, 110, 125)
EMPOWERED = (255, 200,  60)        # glow outline
TEXT      = (230, 230, 235)
```

---

## Data model

Positions are on a grid, not in pixels. Never do pixel collision.

```python
# entities.py

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
```

A wall covering two lanes is two `Threat` objects with the same `group_id`, drawn as one block.

---

## Game states

```
PLAY -> WAVE_BREAK -> PLAY -> ... -> GAME_OVER
```

- Start directly in `WAVE_BREAK` for wave 1 so the player sees the wave banner.
- `WAVE_BREAK` lasts `WAVE_BREAK_S` and shows the wave number and the beat cycle legend enlarged.
- `PLAY` ends when the wave's spawn budget is exhausted and no threats remain on the field.
- `GAME_OVER` when HP reaches 0. Show final score, wave reached, and "Press R to restart".

---

## Update order

Run this exact sequence every frame while in `PLAY`.

1. `events = input_source.poll(dt)`
2. Apply `LaneChange` to the player
3. Apply `CastResolved`. If `ok` is True, spawn a `Bolt` in the player's current lane
4. Move bolts upward, resolve bolt collisions, remove spent bolts
5. Move threats downward
6. Resolve threats that crossed `y >= 1.0`
7. Tick the spawner
8. Check wave complete and game over
9. Emit any `send_feedback` state change
10. Render

---

## Collision resolution

### Bolt against threats

Use swept collision so fast bolts cannot tunnel through threats at 60 FPS.

- Record the bolt's `y_prev` before moving and `y_new` after
- Candidates are threats in the same lane with `y_new <= threat.y <= y_prev`
- If there are several, hit the one with the largest `threat.y`, which is the one nearest the player
- Resolve, then remove the bolt

Outcomes:

| Case | Result |
| --- | --- |
| Threat is a wall | Bolt absorbed. Wall unaffected. Combo unchanged |
| Spell beats monster shape | Monster destroyed. Award points. Combo increases |
| Spell equals monster shape | Blocked. Bolt absorbed. Monster survives. No points. Combo unchanged. Screen shake |
| Spell loses to monster shape | Monster empowered. `speed *= EMPOWER_SPEED_MULT`, `empowered = True`. Combo resets |

An already empowered monster hit again by a losing spell does not stack. Cap the multiplier at one application.

### Threat reaching the player row

When `threat.y >= 1.0`:

| Case | Result |
| --- | --- |
| Same lane as player | 1 damage. Remove threat. Combo resets. Feedback `"damage"`. Brief invulnerability of `IFRAME_MS` |
| Different lane, monster | Monster escaped. Remove. Combo resets. No damage |
| Different lane, wall | Remove silently. Award `SCORE_WALL_DODGE`. Combo unchanged |

---

## Scoring

```
points = base * combo_multiplier
base   = SCORE_EARLY_KILL if threat.y < 0.33 else SCORE_KILL
```

Combo multiplier starts at 1.0, gains `COMBO_STEP` per consecutive kill, caps at `COMBO_CAP`.

Combo resets to 1.0 on: misfire, taking damage, an escaped monster, or empowering a monster.

Combo is unchanged by: a block, a wall dodge, a wall absorbing a bolt.

---

## config.py

Write this file verbatim. Every number the game reads lives here and nowhere else.

```python
# config.py

# window
WINDOW_W = 720
WINDOW_H = 960
FPS = 60

# layout
LANES = 3
LANE_W = WINDOW_W // LANES        # 240
HUD_TOP_H = 80
FIELD_TOP = 80                    # pixel y for grid y = 0.0
FIELD_BOTTOM = 860                # pixel y for grid y = 1.0
PLAYER_ROW_Y = 860
HUD_BOTTOM_H = 100

# player
PLAYER_HP = 3
IFRAME_MS = 800

# casting
CHANNEL_MIN_MS = 600
CHANNEL_MAX_MS = 2000
FAKE_MISFIRE_RATE = 0.10
CAST_COOLDOWN_MS = 400
MISFIRE_LOCKOUT_MS = 500
BOLT_TRAVEL_S = 0.25              # full field traverse

# waves
WAVE_BASE_FALL_S = 4.0            # top to bottom at wave 1
WAVE_FALL_STEP_S = 0.25           # subtracted per wave
WAVE_FALL_FLOOR_S = 2.0
WAVE_BASE_SPAWN_S = 2.5
WAVE_SPAWN_STEP_S = 0.15
WAVE_SPAWN_FLOOR_S = 1.0
WAVE_SPAWN_COUNT = 8
WAVE_BREAK_S = 5.0

# combat
EMPOWER_SPEED_MULT = 1.25

# scoring
SCORE_KILL = 100
SCORE_EARLY_KILL = 150
EARLY_KILL_Y = 0.33
SCORE_WALL_DODGE = 10
COMBO_STEP = 0.5
COMBO_CAP = 4.0

# feel
SHAKE_MS = 150
SHAKE_PX = 6
```

Derive fall speed from time, not the other way round:

```python
fall_time = max(WAVE_FALL_FLOOR_S, WAVE_BASE_FALL_S - WAVE_FALL_STEP_S * (wave - 1))
speed = 1.0 / fall_time
```

---

## Waves

Waves are data. Keep them in a list so a generator can write them later.

```python
# spawner.py
WAVES = [
    {"shapes": ["triangle"],                     "wall_ratio": 0.00},
    {"shapes": ["triangle", "circle"],           "wall_ratio": 0.00},
    {"shapes": ["triangle", "circle"],           "wall_ratio": 0.25},
    {"shapes": ["triangle", "circle", "square"], "wall_ratio": 0.30},
]
```

Wave 5 and beyond reuse index 3 and only scale speed and spawn interval.

Spawner rules:

- Each wave has a budget of `WAVE_SPAWN_COUNT` spawns
- On each spawn, roll `wall_ratio` to decide wall or monster
- Monsters pick a shape uniformly from the wave's `shapes` list
- Walls occupy one lane, or two adjacent lanes with probability 0.35
- A wall must never cover all three lanes
- Never spawn into a lane that already holds a threat with `y < 0.15`

---

## Logging

Create `logs/session_<unix_ts>.jsonl` at startup. Append one line per cast.

```json
{"t_ms": 14820, "wave": 3, "shape_cast": "triangle", "shape_target": "circle",
 "confidence": 0.88, "duration_ms": 940, "outcome": "kill", "hit_y": 0.41,
 "combo": 2.5, "player_hp": 2, "lane": 1}
```

`outcome` is one of: `kill`, `block`, `empower`, `misfire`, `absorbed_by_wall`, `no_target`.

`shape_target` and `hit_y` are null when the bolt hit nothing.

Also append one wave summary line per wave.

```json
{"t_ms": 62000, "type": "wave_summary", "wave": 3, "casts": 11, "kills": 7,
 "misfires": 2, "blocks": 1, "empowers": 1, "damage_taken": 1,
 "max_combo": 4.0, "duration_s": 31.2}
```

`confidence` and `duration_ms` are fabricated in this build. Log them anyway. The schema must not change when the real wand arrives.

---

## Rendering

`render.py` owns all pygame drawing. Grid y maps to screen y with:

```python
screen_y = FIELD_TOP + grid_y * (FIELD_BOTTOM - FIELD_TOP)
```

- Background `BG`, lane dividers in `GRID` as thin vertical lines
- Monster: a filled shape in its `COLORS` entry, about 90 px, centered in its lane. Triangle as a polygon, circle as a circle, square as a rect
- Empowered monster: add a 3 px `EMPOWERED` outline and a slow pulse on the outline alpha
- Wall: a `WALL` rect spanning its lanes, 60 px tall, with diagonal hatch lines
- Bolt: a small bright circle in the spell colour with a short trailing streak
- Player: a white triangle pointing up at `PLAYER_ROW_Y`. Blink at 10 Hz during invulnerability
- Screen shake: offset the whole field surface by a random value up to `SHAKE_PX` for `SHAKE_MS` after a block or after taking damage

---

## HUD

`hud.py` draws:

- Top left: HP as filled or hollow hearts, drawn as simple polygons
- Top right: score, and combo multiplier as `x2.5` when above 1.0
- Bottom left, always visible: the beat cycle legend. Three small shapes in their colours with arrows showing triangle to circle to square to triangle. Do not hide this on a menu, players and judges will not memorise it
- Above the player: the channel bar. Fills while Space is held. Red until `CHANNEL_MIN_MS`, then green. Shows the selected shape icon once a number key is pressed. This is the visual stand in for the wand LED, so make it readable at a glance
- Wave break overlay: large wave number, enlarged legend, countdown

---

## Build order

Complete each step and verify its check before starting the next.

| Step | Deliverable | Check |
| --- | --- | --- |
| 1 | `config.py`, `inputs/base.py`, `inputs/keyboard.py` | `game.py` contains no `import pygame` |
| 2 | Window, grid, player that moves between lanes | Left and right feel responsive |
| 3 | Threats spawn and fall, damage on contact, HP | You can lose by standing still |
| 4 | Casting, bolts, swept collision, beat cycle | All four outcomes reachable: kill, block, empower, wall absorb |
| 5 | Waves, difficulty scaling, wave break screen | Wave 5 is clearly harder than wave 1 |
| 6 | Scoring, combo, HUD, game over and restart | Combo breaks on exactly the listed events |
| 7 | `logger.py` | A full session writes a valid JSONL file that parses |
| 8 | Tuning pass | See below |

## Tuning target

A person outside the team, playing for the first time, should reach wave 3 and die somewhere around wave 6.

- Clears wave 6 easily: raise `wall_ratio` in later waves or increase `WAVE_FALL_STEP_S`
- Dies on wave 2: raise `WAVE_BASE_FALL_S` to 5.0

Fix difficulty in this build. Once the wand is attached you will no longer be able to tell whether a death came from the difficulty curve or from gesture recognition failing.
