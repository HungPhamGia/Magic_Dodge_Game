"""Self-check for the pure game logic. Run: python test_game.py"""

from demogame_v1.game import (
    BOX_H,
    LANES,
    box_hits_nose,
    danger_lanes,
    is_hit,
    is_safe,
    lane_of,
    occupied_lanes,
    spawn_wave,
    wave_lead,
    wave_speed,
)

W = 1920  # lane width 640


def test_lane_of():
    assert lane_of(0, W) == 0
    assert lane_of(639, W) == 0
    assert lane_of(640, W) == 1  # boundary belongs to the lane on its right
    assert lane_of(1279, W) == 1
    assert lane_of(1280, W) == 2
    assert lane_of(W - 1, W) == 2
    # Landmarks can land outside the frame; must not index a fourth lane.
    assert lane_of(-50, W) == 0
    assert lane_of(W + 500, W) == LANES - 1


def test_occupied_lanes():
    assert occupied_lanes(860, 1060, W) == {1}  # centred, span 200 < lane 640
    assert occupied_lanes(600, 800, W) == {0, 1}  # straddling the first boundary
    assert occupied_lanes(1060, 860, W) == {1}  # left/right order must not matter


def test_is_safe():
    assert is_safe({1}, 1)
    assert not is_safe({0, 1}, 1)  # one shoulder out is not "fully in the lane"
    assert not is_safe({1}, 0)


def test_danger_lanes():
    assert danger_lanes(1) == [0, 2]
    assert danger_lanes(0) == [1, 2]


def test_box_hits_nose():
    box_y, nose_r = 100.0, 60.0  # hit band spans 40 .. 300
    assert not box_hits_nose(box_y, 39, nose_r)  # box still above the head
    assert box_hits_nose(box_y, 40, nose_r)  # top edge
    assert box_hits_nose(box_y, 170, nose_r)  # dead centre
    assert box_hits_nose(box_y, 100 + BOX_H + 60, nose_r)  # bottom edge
    assert not box_hits_nose(box_y, 100 + BOX_H + 61, nose_r)  # ducked clear


def test_is_hit_two_tiers():
    nose_r, box_y, standing, ducked = 60.0, 100.0, 170, 800

    # Tier 1: fully inside the empty lane is immune even with a box at nose height.
    assert not is_hit({1}, 1, box_y, standing, nose_r)

    # Tier 2: straddling, box at nose height -> hit.
    assert is_hit({0, 1}, 1, box_y, standing, nose_r)
    # Straddling the other side is symmetric.
    assert is_hit({1, 2}, 1, box_y, standing, nose_r)
    # Standing wholly in a danger lane -> hit.
    assert is_hit({0}, 1, box_y, standing, nose_r)

    # Tier 2: same straddle, but squatting puts the nose below the box.
    assert not is_hit({0, 1}, 1, box_y, ducked, nose_r)


def test_difficulty_ramp_is_clamped():
    assert wave_speed(0) == 500.0
    assert wave_lead(0) == 1.5
    assert wave_speed(1000) == 850.0  # never faster than the cap
    assert wave_lead(1000) == 0.8  # never less warning than the floor
    assert wave_speed(10) > wave_speed(0)
    assert wave_lead(10) < wave_lead(0)


def test_spawn_wave_warning_invariant():
    """Every wave must start off-screen and give at least wave_lead() of warning."""
    for score in (0, 5, 20, 100):
        for nose_y_base in (120.0, 195.0, 400.0, 900.0):
            wave = spawn_wave(score, nose_y_base)
            assert wave["safe"] in range(LANES)
            assert wave["y"] <= -BOX_H, "wave must slide in from above the screen"
            warning = (nose_y_base - wave["y"]) / wave["speed"]
            assert warning >= wave_lead(score) - 1e-9, (score, nose_y_base, warning)


if __name__ == "__main__":
    for name, case in sorted(globals().items()):
        if name.startswith("test_"):
            case()
            print(f"ok  {name}")
    print("\nall good")
