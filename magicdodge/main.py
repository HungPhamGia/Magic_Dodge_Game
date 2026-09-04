"""Window, loop, log file. The only place the outside world is touched.

Run from the repo root with:

    python -m magicdodge.main                 fullscreen, camera + wand + keyboard
    python -m magicdodge.main --camera 0      a different webcam
    python -m magicdodge.main --wand COM7     a serial port instead of Wi-Fi
    python -m magicdodge.main --no-camera --no-wand    keyboard only
    python -m magicdodge.main --hr-name Band  narrow the watch scan by name
    python -m magicdodge.main --no-hr         play without a heart rate watch
    python -m magicdodge.main --sim-hr        fake heart rate, skip the watch wait
    python -m magicdodge.main --windowed      in a window, for debugging

The camera, the wand and the heart rate watch are all on by default and all
turn off the same way. SPACE starts the run from the title screen, and that
screen waits until the watch is actually sending, so no run can be played on a
simulated trace and then uploaded as if it were real -- use --no-hr to play
without one. Esc quits, which is the way out of fullscreen. Z recentres the
wand, which drifts because the firmware integrates gyro.
"""

import argparse
import json
import time
from pathlib import Path

import pygame

from . import draw
from .config import (
    CAM_CONFIDENCE,
    CAM_ID,
    FIELD_W,
    FPS,
    WAND_PORT,
    WAND_WIFI,
    WINDOW_H,
    WINDOW_W,
)
from . import cloud
from .coach import Coach, load_profile, read_records, summarize, update_profile
from .game import GAME_OVER, Game
from .heart_rate import HeartRateMonitor
from .hr_device import HeartDeviceBridge
from .inputs import CameraSource, KeyboardSource, WandSource


class CastLog:
    """One JSONL line per cast, one per wave. See Game._log_cast for the schema.

    confidence and duration_ms are placeholders while the keyboard is the only
    source. They are written anyway so the schema does not change when the wand
    starts filling them in for real.
    """

    def __init__(self, directory):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"session_{int(time.time())}.jsonl"
        self._file = self.path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._file.write(json.dumps(record) + "\n")
        self._file.flush()      # a crash mid playtest must not cost the session

    def close(self) -> None:
        self._file.close()


class Sources:
    """Poll several devices as one. Feedback and close fan out to all of them.

    The camera moves you, the wand and the keyboard cast, so the game needs all
    three at once. Order is camera, wand, keyboard: movement first, and within a
    frame an explicit keypress wins.
    """

    def __init__(self, *sources):
        self.sources = sources

    def poll(self, dt):
        return [event for source in self.sources for event in source.poll(dt)]

    def send_feedback(self, state):
        for source in self.sources:
            source.send_feedback(state)

    def close(self):
        for source in self.sources:
            source.close()


def open_camera(camera_id: int, confidence: float):
    """The camera is a nice-to-have. A missing webcam must not stop the game."""
    try:
        camera = CameraSource(camera_id, confidence)
    except Exception as error:      # no webcam, no mediapipe, wrong index
        print(f"Camera off ({error}); keyboard only.")
        return None
    print(f"Camera {camera_id} on. Step left and right to change lane.")
    return camera


def open_wand(port: str):
    """Also a nice-to-have. No wand plugged in must not stop the game."""
    try:
        wand = WandSource(port)
    except Exception as error:      # no board, no network, port already open
        print(f"Wand off ({error}); cast with J K L.")
        if port == WAND_WIFI:
            # Nothing to enumerate for UDP: bind succeeds whether or not the
            # wand is there, so the only useful thing to say is which network.
            print("  join this PC to the wand's Wi-Fi: SSID MagicWand,"
                  " password wand1234.")
            return None
        # By far the most common cause is the port already being held by the
        # Arduino Serial Monitor or live_test.py. The recording scripts say so
        # on failure and so should this.
        try:
            import serial.tools.list_ports

            found = [f"{p.device} - {p.description}"
                     for p in serial.tools.list_ports.comports()]
            print("  ports:", ", ".join(found) if found else "none")
            if any(line.startswith(port) for line in found):
                print(f"  {port} exists, so something else has it open."
                      " Close the Serial Monitor or live_test.py.")
        except Exception:
            pass
        return None
    print("Wand ready. Hold the button and draw a shape to cast.")
    return wand


def start_music(path: str | None = None, use_music: bool = True) -> None:
    """Loop a background track. Copyright stays out of the repo: the game plays a
    file you drop into the music/ folder (or one named with --music), and simply
    stays silent if there is none. Harry Potter's own theme is not shipped."""
    if not use_music:
        return
    import glob
    try:
        pygame.mixer.init()
    except Exception as error:
        print(f"No audio device ({error}); playing without music.")
        return
    candidates = [path] if path else []
    if not path:
        folder = Path(__file__).parent.parent / "music"
        for ext in ("ogg", "mp3", "wav"):
            candidates += sorted(glob.glob(str(folder / f"*.{ext}")))
    for name in candidates:
        try:
            pygame.mixer.music.load(name)
            pygame.mixer.music.set_volume(0.5)
            pygame.mixer.music.play(-1)              # loop forever
            print(f"Music: {name}")
            return
        except Exception:
            continue
    print("No music found. Drop a track (e.g. the Harry Potter theme) into the "
          "'music' folder as music/theme.mp3 or theme.ogg to play it.")


def main(
    camera_id: int | None = CAM_ID,
    confidence: float = CAM_CONFIDENCE,
    fullscreen: bool = True,
    wand_port: str | None = WAND_PORT,
    hr_device: str | None = None,
    hr_name: str | None = None,
    use_hr: bool = True,
    music: str | None = None,
    use_music: bool = True,
) -> None:
    camera = open_camera(camera_id, confidence) if camera_id is not None else None
    wand = open_wand(wand_port) if wand_port is not None else None

    pygame.init()
    start_music(music, use_music)
    # SCALED renders at this fixed size and lets SDL fit it to the display, so
    # every coordinate in config.py stays a plain number. Without a camera the
    # game column is the whole surface and simply gets bars either side.
    width = WINDOW_W if camera else FIELD_W
    flags = (pygame.SCALED | pygame.FULLSCREEN) if fullscreen else 0
    screen = pygame.display.set_mode((width, WINDOW_H), flags)
    pygame.display.set_caption("MagicDodge")
    clock = pygame.time.Clock()

    keyboard = KeyboardSource()
    log = CastLog(Path(__file__).parent / "logs")
    profile_path = Path(__file__).parent / "logs" / "profile.json"
    devices = [device for device in (camera, wand, keyboard) if device]
    game = Game(Sources(*devices), log)
    coach = Coach()          # the post-run LLM coach; one per run, reset on restart
    # On by default, like the camera and the wand, and off the same way with
    # --no-hr. It used to need --hr-name, and forgetting it did not fail: the
    # game simply never scanned, showed a simulated trace, and uploaded it as a
    # session. A sensor you have to remember to ask for is a sensor you will
    # demo without. --hr-name still narrows the scan when several straps are
    # in the room; without it hr_device.py matches the Heart Rate service.
    watch_wanted = use_hr
    heart = HeartRateMonitor(device_wanted=watch_wanted)
    print(f"Logging to {log.path}")

    # A real BLE watch, if asked for. The bridge runs on its own thread and
    # calls push() on whichever heart monitor is current, so restart still
    # feeds the new one. Without a watch the simulation above stays in charge.
    if watch_wanted:
        HeartDeviceBridge(lambda bpm: heart.push(bpm),
                          target=hr_device, name=hr_name).start()

    try:
        while not keyboard.quit:
            dt = clock.tick(FPS) / 1000.0
            game.update(dt)
            heart.update(dt, game)   # sample the heart rate against the game state
            # The camera may have moved the player, so the keyboard's own idea
            # of the lane has to follow or the next keypress snaps it back.
            keyboard.lane = game.player.lane

            # On the first frame of game over, hand the finished run to the coach.
            # _end_wave has already flushed the last wave_summary, so the log is
            # complete; the request runs on its own thread and never blocks here.
            if game.state == GAME_OVER and coach.status == "idle":
                summary = summarize(read_records(log.path), game.score, game.wave)
                summary["heart_rate"] = heart.summarize()          # effort input
                summary["history"] = load_profile(profile_path)    # past runs, for progress
                coach.request(summary)
                # Persist the run and push it to the cloud (or a local file).
                update_profile(profile_path, summary)
                where = cloud.upload({"ts": int(time.time()), **summary}, log.path.parent)
                print(f"Session uploaded to {where}")

            # The start gate. heart.ready() is the whole rule: a watch that was
            # asked for has to be feeding before wave 1 opens, so no run can
            # start on a simulated trace and be uploaded as if it were real.
            if keyboard.start:
                keyboard.start = False
                if heart.ready():
                    game.start()

            if keyboard.restart:
                if game.state == GAME_OVER:
                    game.reset()                # back to the start screen, which
                                                # re-checks the watch is still on
                    coach = Coach()             # fresh coach for the next run
                    heart = HeartRateMonitor(device_wanted=watch_wanted)
                keyboard.reset(game.player.lane)

            if keyboard.recenter:
                if wand:
                    wand.recenter()
                keyboard.recenter = False

            draw.frame(screen, game, keyboard, camera, wand, coach, heart)
            pygame.display.flip()
    finally:
        log.close()
        game.source.close()
        pygame.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MagicDodge")
    parser.add_argument("--camera", type=int, default=CAM_ID, help="webcam index")
    parser.add_argument("--no-camera", action="store_true", help="keyboard only")
    parser.add_argument("--confidence", type=float, default=CAM_CONFIDENCE)
    parser.add_argument("--windowed", action="store_true", help="do not go fullscreen")
    parser.add_argument("--wand", default=WAND_PORT, help="wand serial port")
    parser.add_argument("--no-wand", action="store_true", help="cast with J K L")
    parser.add_argument("--hr-device", default=None,
                        help="BLE heart rate watch MAC/UUID to connect to")
    parser.add_argument("--hr-name", default=None,
                        help="narrow the watch scan by name substring, e.g. Band")
    parser.add_argument("--no-hr", action="store_true",
                        help="play without a heart rate watch (skips the wait)")
    parser.add_argument("--sim-hr", action="store_true",
                        help="skip the watch wait and use a simulated (fake) heart "
                             "rate, for testing without a device")
    parser.add_argument("--music", default=None,
                        help="path to a music file to loop (else music/ is scanned)")
    parser.add_argument("--no-music", action="store_true", help="play without music")
    args = parser.parse_args()
    main(
        None if args.no_camera else args.camera,
        args.confidence,
        fullscreen=not args.windowed,
        wand_port=None if args.no_wand else args.wand,
        hr_device=args.hr_device,
        hr_name=args.hr_name,
        use_hr=not (args.no_hr or args.sim_hr),
        music=args.music,
        use_music=not args.no_music,
    )
