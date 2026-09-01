# MagicDodge

A three lane dodging and spellcasting game, played with your body and a wand.

You lean left or right in front of a webcam to change lane. You cast by drawing a shape in the
air with an MPU6050 wand — a triangle, a circle, or a square. Monsters fall down the three
lanes, each carrying a shape of its own, and you have to answer each one with the shape that
beats it.

**Triangle beats circle. Circle beats square. Square beats triangle.**

Answer correctly and the monster dies. Answer with its own shape and your bolt is blocked.
Answer with the shape it beats and it gets 25% faster and keeps coming. Walls fall too; nothing
kills a wall, you just have to not be in its lane. Anything that reaches your row costs a heart,
and you have three.

Neither the camera nor the wand is required — the keyboard plays the whole game.

## Install

Python 3.11 or newer.

```
pip install -r requirements.txt
```

Only `pygame-ce` is mandatory. `mediapipe` and `opencv` drive the camera, `pyserial` a wired
wand. Without them, or without the hardware plugged in, the game says so on startup and falls
back to the keyboard instead of failing.

## Play

```
python -m magicdodge.main
```

Run it from the repo root — `magicdodge` is a package and imports `wand` as a sibling.

| Key | |
| --- | --- |
| Left / Right, or A / D | change lane |
| J / K / L | cast triangle / circle / square |
| Z | recentre the wand |
| R | restart after a game over |
| Esc | quit, and the way out of fullscreen |

Casting has a 400 ms cooldown and that is its only cost — one key, one bolt. Z matters because
the firmware integrates gyro, so the wand's idea of centre drifts as you play.

Killing a monster high up the field scores more than killing it just above your head, and
consecutive kills build a multiplier to 4x. The multiplier resets when you take damage, let a
monster escape, misfire, or empower something.

### Options

```
python -m magicdodge.main --no-camera --no-wand   keyboard only
python -m magicdodge.main --windowed              windowed, for debugging
python -m magicdodge.main --camera 0              a different webcam (default 1)
python -m magicdodge.main --wand COM5             a serial port instead of Wi-Fi
python -m magicdodge.main --confidence 0.4        looser pose tracking, for bad light
```

## Difficulty

Six hand-tuned waves in `magicdodge/config.py`, then it scales on forever. Wave 1 gives you
eight seconds per fall and only asks for circles; wave 6 gives you three, uses all three
shapes, and stacks the rows deeper. Past the table each wave multiplies the fall time and the
gap between rows by 0.9, never dropping below 1.2 seconds — that floor is the hard ceiling on
how bad it can get, however long you last. Fifteen seconds of rest between waves.

Every constant lives in `config.py` with a comment on which way to move it.

## The wand

An ESP board with an MPU6050, running `drawing_wand_mpu6050/`. It streams `P,<x>,<y>,<pen>` at
100 Hz and accepts one command, `z`, to zero itself.

It hosts its own Wi-Fi access point, and the PC joins that network — which means that adapter
has no internet while you are playing. `--wand COM5` uses USB serial instead.

Shape recognition is the [$1 recognizer](https://depts.washington.edu/acelab/proj/dollar/),
matching against gestures you record yourself:

```
python -m wand.record       draw each shape a few times -> strokes_*.json at the repo root
python -m wand_test.test    leave-one-out accuracy over what you recorded
python -m wand_test.live_test   draw, and watch it classified live
python -m wand_test.canvas      raw wand trace, no recognition
```

Record your own before playing with the wand — the templates committed here are one person's
handwriting, and the game refuses to start the wand without a `strokes_*.json` at the root. A
match scoring worse than 60 is treated as a misfire; good templates score around 24.

Unlike `magicdodge`, the `wand_test/` scripts run from anywhere, by path or with `-m`.

## Layout

```
magicdodge/            the game
  game.py              state machine, entities, collision, scoring — no pygame in here
  config.py            every tunable constant, each with a comment
  inputs.py            KeyboardSource, CameraSource, WandSource behind one protocol
  perception.py        MediaPipe pose landmarks, for CameraSource
  draw.py              all rendering
  main.py              window, loop, JSONL cast log
wand/
  record.py            capture gesture templates
  dollar.py            $1 recognizer
  wifi.py              UDP link to the wand's access point
wand_test/             bench tools for the recognizer, not part of the game
demogame_v1/           earlier camera-only prototype, superseded
drawing_wand_mpu6050/  the firmware
strokes_*.json         recorded gestures. The wand will not start without them
```

`game.py` deliberately imports no pygame, which is what lets `test_magicdodge.py` cover the
rules without a window. Every input device implements the same three methods, so the game
cannot tell a keyboard from a wand.

```
python -m magicdodge.test_magicdodge
```

Each session writes `magicdodge/logs/session_<ts>.jsonl`, one line per cast plus a summary per
wave — what was cast, at what confidence, and what it hit. Those logs and `element/` are
gitignored.
