# MagicDodge

A three lane dodging and spellcasting game. You move between lanes by leaning in front of a
webcam, and you cast by drawing a shape in the air with an MPU6050 wand. Monsters carry a
shape; a triangle beats a circle, a circle beats a square, a square beats a triangle. Cast the
wrong one and the monster gets faster.

Keyboard alone plays the whole game, so neither piece of hardware is required.

## Install

Python 3.11 or newer.

```
pip install -r requirements.txt
```

Only `pygame-ce` is mandatory. `mediapipe` and `opencv` are for the camera, `pyserial` for a
wired wand; without them the game falls back to keyboard on its own.

## Run

Run `magicdodge` from the repo root with `-m` — it is a package and imports `wand` as a
sibling. The `wand_test/` bench scripts work either way, by path or with `-m`, from any
directory; they put the repo root on `sys.path` themselves and read `strokes_*.json` from
the root rather than from the current directory.

```
python -m magicdodge.main                        camera + wand + keyboard, fullscreen
python -m magicdodge.main --no-camera --no-wand  keyboard only
python -m magicdodge.main --windowed             windowed, for debugging
python -m magicdodge.main --wand COM7            a serial port instead of Wi-Fi
python -m magicdodge.test_magicdodge             tests

python -m wand.record                            record gestures -> strokes_*.json at the root
python -m wand_test.test                         leave-one-out accuracy on those recordings
python -m wand_test.live_test                    draw with the wand, see it classified
python -m wand_test.canvas                       raw wand trace, no recognition

python demogame_v1/game.py                       earlier camera-only prototype, run from its dir
```

Esc quits. Z recentres the wand, which drifts because the firmware integrates gyro.

Controls without hardware: arrows or A/D to change lane, hold Space and press 1/2/3 to pick
triangle/circle/square, release to cast. F1 toggles instant-cast debug mode.

## Layout

```
magicdodge/            the game. game.py is pure logic and imports no pygame
  config.py            every tunable constant
  game.py              state machine, entities, collision, scoring
  inputs.py            KeyboardSource, CameraSource, WandSource behind one protocol
  draw.py  main.py     rendering, window, loop, JSONL cast log
wand/                  shared wand stack
  record.py            capture gesture templates
  dollar.py            $1 recognizer
  wifi.py              UDP link to the wand's softAP
wand_test/             bench tools for the recognizer, not part of the game
demogame_v1/           superseded camera-only prototype
drawing_wand_mpu6050/  ESP + MPU6050 firmware
strokes_*.json         recorded gesture templates. The wand will not start without them
```

`magicdodge/logs/` (per-session JSONL cast telemetry) and `element/` (art not yet wired in)
are gitignored.

## Hardware

- ESP board with an MPU6050, running `drawing_wand_mpu6050/`. It hosts its own Wi-Fi AP; the
  PC joins that network, so that adapter has no internet while connected.
- Any webcam for lane control.
