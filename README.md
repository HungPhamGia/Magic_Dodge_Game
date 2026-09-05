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
python -m magicdodge.main --hr-name Band          narrow the watch scan by name
python -m magicdodge.main --no-hr                 play without a heart rate watch
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

## The AI coach

When a run ends, an optional coach reads the session and writes personal feedback on the game
over screen. It folds the cast log into a compact summary (score, waves, accuracy, longest
combo, per shape reliability, outcome counts), attaches the heart rate summary as an effort
signal and a short history of past runs, and sends that to a language model through
[OpenRouter](https://openrouter.ai). The reply is constrained to a fixed JSON schema, so the
game draws it without parsing guesswork. The raw object the coach returns looks like this:

```json
{
  "analysis": "private reasoning, read by the game but never shown on screen",
  "headline": "You reached wave 4 with 1750 points.",
  "did_well": [
    "New personal best, up from 1520 points.",
    "Sharp casting, 79% of your spells landed a kill.",
    "You built a 7x combo."
  ],
  "improve": [
    "You empowered monsters twice, so recall the losing shape.",
    "A few casts were blocked, which is the monster's own shape.",
    "You took three hits, so lean out of a lane earlier."
  ],
  "tip": "Draw the shape that beats the monster.",
  "effort": "Your heart rate rose about 89 beats above rest, so you were working hard.",
  "encouragement": "Nice run. Line up the shapes and that score will climb!"
}
```

`analysis` is a private reasoning field the model fills first and the game hides; the six fields
below it are shown. The coach runs on a background thread, so the game never pauses. It tries
the chosen model, then a set of free models, then a built in offline coach that applies simple
rules to the same summary — so a run always ends with feedback, with or without a key or
internet.

Enable the model by setting an API key; without it, the offline coach runs.

```
export OPENROUTER_API_KEY=sk-or-...                  enables the model
export OPENROUTER_MODEL=anthropic/claude-sonnet-5    optional; this is the default
```

The default is Claude Sonnet 5. `anthropic/claude-haiku-4.5` is cheaper and
`anthropic/claude-opus-4.8` is the strongest. The key is read from the environment and never
stored in the repo: put it in a gitignored `.env` and the launcher sources it. The coach is
instructed to speak only about play and effort, never health or medical advice — the heart rate
is an effort signal, not a clinical reading.

## Heart rate

A wrist heart rate feeds the coach an effort signal and shows live on the HUD. By default
`magicdodge/heart_rate.py` simulates a plausible trace driven by how intense the game is, so the
whole pipeline runs and demonstrates without any hardware.

A real Bluetooth watch (standard BLE Heart Rate profile, `0x180D`) is used whenever one is
found. Install `bleak` and wear the watch; no flag is needed, because the scan matches the Heart
Rate service itself rather than a device name:

```
pip install bleak
python -m magicdodge.main                    finds any heart rate watch in range
python -m magicdodge.main --hr-name Band     narrow it, if several straps are in the room
python -m magicdodge.main --hr-device <MAC>  or connect straight to an address or UUID
python -m magicdodge.main --no-hr            skip the watch, and the wait for it
```

The title screen will not start a run until the watch is actually sending, so a session can
never be played on a simulated trace and then uploaded as if it were real. `--no-hr` is the way
out when you have no watch to hand.

`magicdodge/hr_device.py` runs the BLE reader (`heart_rate_monitor.py`) on a background thread
and pushes each reading into the game, so the real trace replaces the simulation and flows into
the coach and the upload. A missing watch, a missing `bleak`, or a dropped connection falls back
to the simulation without stopping the game. The launcher enables it from `HR_NAME`:

```
export HR_NAME=Band
./chay_game.command
```

## Cloud storage

Each finished session (the game summary plus the heart rate summary) is uploaded so results can
be gathered across players. The backend is chosen from the environment, so nothing secret is
hardcoded:

| Variable | Backend |
| --- | --- |
| `MONGODB_URI` | MongoDB (needs `pip install pymongo`) |
| `FIREBASE_DB_URL` (+ optional `FIREBASE_SECRET`) | Firebase Realtime Database, over stdlib HTTP |
| `SESSION_ENDPOINT` | any HTTP endpoint that accepts a JSON POST |
| none set | a local `magicdodge/logs/uploads.jsonl`, so the pipeline still runs offline |

The upload runs after the game and never interrupts play. Read a Firebase store back with:

```
curl -s "$FIREBASE_DB_URL/magicdodge_sessions.json"
```

## Layout

```
magicdodge/            the game
  game.py              state machine, entities, collision, scoring — no pygame in here
  config.py            every tunable constant, each with a comment
  inputs.py            KeyboardSource, CameraSource, WandSource behind one protocol
  perception.py        MediaPipe pose landmarks, for CameraSource
  draw.py              all rendering
  main.py              window, loop, JSONL cast log
  coach.py             post-run LLM coach (OpenRouter) with an offline fallback
  heart_rate.py        wrist heart rate, simulated or fed by a real device
  hr_device.py         bridges a BLE watch into the game
  cloud.py             uploads each session to Firebase, MongoDB, or a local file
wand/
  record.py            capture gesture templates
  dollar.py            $1 recognizer
  wifi.py              UDP link to the wand's access point
wand_test/             bench tools for the recognizer, not part of the game
demogame_v1/           earlier camera-only prototype, superseded
drawing_wand_mpu6050/  the firmware
strokes_*.json         recorded gestures. The wand will not start without them
heart_rate_monitor.py  standalone BLE heart rate reader (Bleak), driven by hr_device.py
```

`game.py` deliberately imports no pygame, which is what lets `test_magicdodge.py` cover the
rules without a window. Every input device implements the same three methods, so the game
cannot tell a keyboard from a wand.

```
python -m magicdodge.test_magicdodge
```

Each session writes `magicdodge/logs/session_<ts>.jsonl`, one line per cast plus a summary per
wave - what was cast, at what confidence, and what it hit. Those logs and `element/` are
gitignored.
