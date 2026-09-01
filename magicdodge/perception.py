"""Read three body keypoints from a webcam with MediaPipe Pose."""

import argparse
import sys

import cv2
import mediapipe as mp

pose_api = mp.solutions.pose

# Short keys are the contract with game.py; LABELS is display-only.
KEYPOINTS = {
    "nose": pose_api.PoseLandmark.NOSE.value,
    "left": pose_api.PoseLandmark.LEFT_SHOULDER.value,
    "right": pose_api.PoseLandmark.RIGHT_SHOULDER.value,
}
LABELS = {"nose": "Nose", "left": "Left shoulder", "right": "Right shoulder"}


def get_screen_size() -> tuple[int, int]:
    """Return the current screen size, or 1280x720 as a safe fallback."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        size = (root.winfo_screenwidth(), root.winfo_screenheight())
        root.destroy()
        return size
    except Exception:
        return 1280, 720


def to_pixel(landmark, width: int, height: int) -> tuple[int, int]:
    """Convert a normalized MediaPipe landmark to screen pixels."""
    return int(landmark.x * width), int(landmark.y * height)


def read_points(
    pose, bgr_frame, width: int, height: int, confidence: float
) -> dict[str, tuple[int, int]]:
    """Visible nose/shoulder landmarks, scaled to a width x height screen.

    Landmarks are normalized, so bgr_frame may stay at native camera
    resolution while width/height are the display size.
    """
    result = pose.process(cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB))
    if not result.pose_landmarks:
        return {}
    landmarks = result.pose_landmarks.landmark
    return {
        name: to_pixel(landmarks[index], width, height)
        for name, index in KEYPOINTS.items()
        if landmarks[index].visibility >= confidence
    }


def main(camera_id: int, confidence: float) -> int:
    camera = cv2.VideoCapture(camera_id)
    if not camera.isOpened():
        print(f"Cannot open webcam {camera_id}.", file=sys.stderr)
        return 1

    screen_width, screen_height = get_screen_size()
    screen_size = (screen_width, screen_height)
    print(f"Screen: {screen_width} x {screen_height}")

    window = "MediaPipe Pose"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(window, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    try:
        with pose_api.Pose(
            smooth_landmarks=True,
            min_detection_confidence=confidence,
            min_tracking_confidence=confidence,
        ) as pose:
            while True:
                success, camera_frame = camera.read()
                if not success:
                    print("Cannot read webcam frame.", file=sys.stderr)
                    return 1

                camera_frame = cv2.flip(camera_frame, 1)
                points = read_points(
                    pose, camera_frame, screen_width, screen_height, confidence
                )
                frame = cv2.resize(camera_frame, screen_size)

                # Three visual lanes only; no lane-classification logic.
                lane_width = screen_width // 3
                for boundary_x in (lane_width, lane_width * 2):
                    cv2.line(
                        frame,
                        (boundary_x, 0),
                        (boundary_x, screen_height),
                        (255, 255, 255),
                        3,
                    )
                for index, label in enumerate(("LEFT", "CENTER", "RIGHT")):
                    label_size, _ = cv2.getTextSize(
                        label, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2
                    )
                    label_x = index * lane_width + (lane_width - label_size[0]) // 2
                    cv2.putText(
                        frame,
                        label,
                        (label_x, screen_height - 40),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        1.0,
                        (255, 255, 255),
                        2,
                        cv2.LINE_AA,
                    )

                if points:
                    left = points.get("left")
                    right = points.get("right")
                    if left and right:
                        midpoint = ((left[0] + right[0]) // 2, (left[1] + right[1]) // 2)
                        cv2.line(frame, left, right, (0, 255, 0), 3)
                        cv2.circle(frame, midpoint, 9, (255, 0, 255), -1)
                        cv2.putText(
                            frame,
                            f"Midpoint: {midpoint}",
                            (20, 140),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            (255, 0, 255),
                            2,
                        )

                    colors = {
                        "nose": (255, 255, 0),
                        "left": (0, 255, 255),
                        "right": (0, 255, 255),
                    }
                    for row, (name, point) in enumerate(points.items(), start=1):
                        cv2.circle(frame, point, 9, colors[name], -1)
                        cv2.putText(
                            frame,
                            f"{LABELS[name]}: {point}",
                            (20, 35 * row),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.7,
                            colors[name],
                            2,
                        )

                cv2.imshow(window, frame)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    break
    finally:
        camera.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track nose and shoulders")
    parser.add_argument("--camera", type=int, default=1)
    parser.add_argument("--confidence", type=float, default=0.6)
    args = parser.parse_args()
    raise SystemExit(main(args.camera, args.confidence))
