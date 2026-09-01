"""Three-lane dodging game driven by MediaPipe pose landmarks.

Boxes one lane wide fall in 2 of the 3 lanes. Get both shoulders fully inside
the one empty lane and you are immune. Caught straddling, the box connects only
if it reaches your nose, so squatting buys you time to finish the side-run.
"""

import argparse
import random
import statistics
import sys
import time

import cv2
import mediapipe as mp

from demogame_v1.perception import get_screen_size, read_points

LANES = 3
LIVES = 3

BOX_H = 140  # px, obstacle height
NOSE_R_RATIO = 0.35  # head hitbox radius, as a fraction of shoulder span
SPEED_START, SPEED_CAP = 300.0, 850.0  # px/second
LEAD_START, LEAD_FLOOR = 1.5, 0.8  # seconds of warning before the nose line
GAP = 0.75  # seconds between waves
FLASH = 1.0  # seconds of red border after a hit
GRACE = 0.5  # seconds to coast on stale landmarks before freezing
CALIBRATE_S = 5.0
CALIBRATE_MIN_SAMPLES = 10

# ponytail: False -> the box falls the full screen height, so a squat buys ~1s to
# side-run rather than granting immunity (a permanently held squat would otherwise
# be an un-loseable strategy). Flip to True for "the box passes over your head and
# you are safe", which is the purer reading of the nose-distance rule.
DUCK_IS_IMMUNE = False

FONT = cv2.FONT_HERSHEY_SIMPLEX
WHITE = (255, 255, 255)
RED = (0, 0, 255)
GREEN = (0, 255, 0)
CYAN = (255, 255, 0)
YELLOW = (0, 255, 255)


# --- pure logic (see test_game.py) -------------------------------------------


def lane_of(x: int, width: int) -> int:
    """Lane index 0..2 for a pixel x, clamped to the screen."""
    return min(LANES - 1, max(0, x * LANES // width))


def occupied_lanes(left_x: int, right_x: int, width: int) -> set[int]:
    """Lanes the shoulders straddle.

    ponytail: the two endpoints alone cover the span, because at 2-3m the
    shoulders are narrower than one lane. The STEP BACK guard enforces that.
    """
    return {lane_of(left_x, width), lane_of(right_x, width)}


def is_safe(occupied: set[int], safe_lane: int) -> bool:
    """True only when the whole body is inside the empty lane."""
    return occupied == {safe_lane}


def danger_lanes(safe_lane: int) -> list[int]:
    return [lane for lane in range(LANES) if lane != safe_lane]


def box_hits_nose(box_y: float, nose_y: int, nose_r: float) -> bool:
    """True when the nose sits inside the box's vertical span, padded by the head."""
    return box_y - nose_r <= nose_y <= box_y + BOX_H + nose_r


def is_hit(
    occupied: set[int], safe_lane: int, box_y: float, nose_y: int, nose_r: float
) -> bool:
    """The two-tier rule.

    Tier 1: both shoulders inside the empty lane -> immune, whatever the boxes do.
    Tier 2: otherwise a box connects only if it reaches the nose.

    No explicit danger-lane test is needed: occupied holds 1-2 lanes, so
    `occupied != {safe_lane}` already means at least one shoulder is under a box.
    """
    return not is_safe(occupied, safe_lane) and box_hits_nose(box_y, nose_y, nose_r)


def wave_speed(score: int) -> float:
    return min(SPEED_CAP, SPEED_START + 15.0 * score)


def wave_lead(score: int) -> float:
    return max(LEAD_FLOOR, LEAD_START - 0.05 * score)


def spawn_wave(score: int, nose_y_base: float) -> dict:
    """Place the wave so it takes exactly wave_lead(score) to reach the nose line.

    Most of that time is spent above y=0, which the warning arrows cover.
    Uses the calibrated *standing* nose line: if it followed the live nose,
    ducking would move the spawn point and cancel its own benefit.
    """
    speed = wave_speed(score)
    return {
        "safe": random.randrange(LANES),
        "y": min(-BOX_H, nose_y_base - speed * wave_lead(score)),
        "speed": speed,
        "spent": False,
    }


# --- drawing -----------------------------------------------------------------


def put_center(frame, text, y, width, scale, color, thickness=3):
    (text_w, _), _ = cv2.getTextSize(text, FONT, scale, thickness)
    cv2.putText(
        frame,
        text,
        ((width - text_w) // 2, y),
        FONT,
        scale,
        color,
        thickness,
        cv2.LINE_AA,
    )


def draw_lanes_and_wave(frame, width, height, wave, now):
    """Lane guides, safe tint and boxes. One copy, one blend, to keep fps up."""
    lane_w = width // LANES

    if wave:
        overlay = frame.copy()
        cv2.rectangle(
            overlay,
            (wave["safe"] * lane_w, 0),
            ((wave["safe"] + 1) * lane_w, height),
            (0, 90, 0),
            -1,
        )
        box_y = int(wave["y"])
        box_color = (60, 60, 60) if wave["spent"] else RED
        for lane in danger_lanes(wave["safe"]):
            cv2.rectangle(
                overlay,
                (lane * lane_w, box_y),
                ((lane + 1) * lane_w, box_y + BOX_H),
                box_color,
                -1,
            )
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

        # Still above the screen: the player can only go on the arrows.
        if box_y + BOX_H < 0 and int(now * 6) % 2:
            for lane in danger_lanes(wave["safe"]):
                arrow_x = lane * lane_w + lane_w // 2
                cv2.arrowedLine(
                    frame, (arrow_x, 20), (arrow_x, 120), RED, 14, tipLength=0.45
                )

    for boundary_x in (lane_w, lane_w * 2):
        cv2.line(frame, (boundary_x, 0), (boundary_x, height), WHITE, 3)


def draw_player(frame, points, nose_r):
    left, right, nose = points["left"], points["right"], points["nose"]
    cv2.line(frame, left, right, GREEN, 3)
    for point in (left, right):
        cv2.circle(frame, point, 9, YELLOW, -1)
    cv2.circle(frame, nose, int(nose_r), CYAN, 2)
    cv2.circle(frame, nose, 9, CYAN, -1)


def draw_hud(frame, width, height, score, lives, status, status_color):
    cv2.putText(frame, f"SCORE {score}", (24, 56), FONT, 1.2, WHITE, 3, cv2.LINE_AA)
    for life in range(LIVES):
        color = RED if life < lives else (70, 70, 70)
        cv2.circle(frame, (44 + life * 52, 104), 18, color, -1)
    put_center(frame, status, height - 40, width, 1.1, status_color)


# --- loop --------------------------------------------------------------------


def run(camera_id: int, confidence: float) -> int:
    camera = cv2.VideoCapture(camera_id)
    if not camera.isOpened():
        print(f"Cannot open webcam {camera_id}.", file=sys.stderr)
        return 1

    width, height = get_screen_size()
    lane_w = width // LANES
    print(f"Screen: {width} x {height}   lane width: {lane_w}")

    window = "Lane Dodge"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    state = "calibrating"
    samples: list[tuple[int, int]] = []
    calibrate_until = time.perf_counter() + CALIBRATE_S
    nose_y_base = shoulder_span = nose_r = 0.0
    wave = None
    score, lives = 0, LIVES
    flash_until = next_wave_at = 0.0
    last_points: dict[str, tuple[int, int]] = {}
    last_seen = 0.0
    previous = time.perf_counter()

    try:
        with mp.solutions.pose.Pose(
            smooth_landmarks=True,
            min_detection_confidence=confidence,
            min_tracking_confidence=confidence,
        ) as pose:
            while True:
                success, camera_frame = camera.read()
                if not success:
                    print("Cannot read webcam frame.", file=sys.stderr)
                    return 1

                now = time.perf_counter()
                # Clamp: MediaPipe's first inference and any GC/window-drag stall
                # produce a huge delta that would teleport a box past the player.
                delta, previous = min(now - previous, 0.1), now

                camera_frame = cv2.flip(camera_frame, 1)
                points = read_points(pose, camera_frame, width, height, confidence)
                frame = cv2.resize(camera_frame, (width, height))

                # Hold the last complete reading briefly: a detection blip must
                # never cost a life.
                if {"nose", "left", "right"} <= points.keys():
                    last_points, last_seen = points, now
                tracked = last_points if now - last_seen <= GRACE else {}

                span = abs(tracked["left"][0] - tracked["right"][0]) if tracked else 0
                too_close = bool(tracked) and span > lane_w
                if not tracked:
                    message = "STEP INTO FRAME"
                elif too_close:
                    message = "STEP BACK"
                else:
                    message = ""
                frozen = bool(message)

                if state == "calibrating":
                    if frozen:
                        # Only count down while the player is actually standing
                        # correctly, so the timer never stalls at 0.0.
                        calibrate_until = now + CALIBRATE_S
                    else:
                        samples.append((tracked["nose"][1], span))
                    if now >= calibrate_until and len(samples) >= CALIBRATE_MIN_SAMPLES:
                        nose_y_base = statistics.median(s[0] for s in samples)
                        shoulder_span = statistics.median(s[1] for s in samples)
                        nose_r = NOSE_R_RATIO * shoulder_span
                        print(
                            f"Calibrated: nose_y={nose_y_base:.0f} "
                            f"span={shoulder_span:.0f} nose_r={nose_r:.0f}"
                        )
                        state, next_wave_at = "playing", now + GAP

                elif state == "playing":
                    if wave is None and not frozen and now >= next_wave_at:
                        wave = spawn_wave(score, nose_y_base)

                    if wave and not frozen:
                        wave["y"] += wave["speed"] * delta
                        occupied = occupied_lanes(
                            tracked["left"][0], tracked["right"][0], width
                        )
                        if not wave["spent"] and is_hit(
                            occupied, wave["safe"], wave["y"], tracked["nose"][1], nose_r
                        ):
                            wave["spent"] = True  # cannot hit twice on the way down
                            lives -= 1
                            flash_until = now + FLASH
                            if lives <= 0:
                                state = "over"

                        clear_y = nose_y_base if DUCK_IS_IMMUNE else height
                        if wave["y"] > clear_y:
                            if not wave["spent"]:
                                score += 1
                            wave, next_wave_at = None, now + GAP

                draw_lanes_and_wave(frame, width, height, wave, now)
                if tracked:
                    draw_player(frame, tracked, nose_r or 40)

                if state == "calibrating":
                    put_center(
                        frame, "STAND IN THE CENTER LANE", height // 2, width, 1.6, WHITE
                    )
                    countdown = max(0.0, calibrate_until - now)
                    put_center(
                        frame,
                        message or f"{countdown:.1f}",
                        height // 2 + 90,
                        width,
                        1.6,
                        RED if message else GREEN,
                    )
                elif state == "playing":
                    if frozen:
                        status, status_color = message, RED
                    else:
                        occupied = occupied_lanes(
                            tracked["left"][0], tracked["right"][0], width
                        )
                        safe = wave is not None and is_safe(occupied, wave["safe"])
                        status = f"{'SAFE' if safe else 'RISK'}  lanes={sorted(occupied)}"
                        status_color = GREEN if safe else YELLOW
                    draw_hud(frame, width, height, score, lives, status, status_color)
                else:
                    put_center(frame, "GAME OVER", height // 2 - 40, width, 2.4, RED)
                    put_center(
                        frame, f"SCORE {score}", height // 2 + 50, width, 1.6, WHITE
                    )
                    put_center(
                        frame, "R restart   Q quit", height // 2 + 130, width, 1.0, WHITE
                    )

                if now < flash_until:
                    cv2.rectangle(frame, (0, 0), (width - 1, height - 1), RED, 40)

                cv2.imshow(window, frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("r") and state == "over":
                    score, lives, wave = 0, LIVES, None
                    state, next_wave_at = "playing", now + GAP
                if key == ord("c"):
                    # Recalibration means a new player or a moved camera, so the
                    # run restarts too -- otherwise it would resume on 0 lives.
                    score, lives, wave = 0, LIVES, None
                    state, samples = "calibrating", []
                    calibrate_until = now + CALIBRATE_S
    finally:
        camera.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Three-lane pose dodging game")
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--confidence", type=float, default=0.6)
    args = parser.parse_args()
    raise SystemExit(run(args.camera, args.confidence))
