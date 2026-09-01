"""Everything you see. Drawing primitives only, no assets.

One entry point: frame(screen, game, source). The field is drawn first onto its
own surface so a screen shake can offset it, then the HUD goes on top at rest.
"""

import math
import random

import pygame

from .config import (
    BG,
    CAM_H,
    CAM_W,
    COLORS,
    EMPOWERED,
    FIELD_BOTTOM,
    FIELD_TOP,
    FIELD_W,
    GRID,
    LANE_W,
    LANES,
    PLAYER,
    PLAYER_HP,
    PLAYER_ROW_Y,
    SHAKE_PX,
    TEXT,
    VEIL,
    WALL,
    WAND_PX_PER_DEG,
    WINDOW_H,
)
from .game import GAME_OVER, WAVE_BREAK

MONSTER_SIZE = 130
WALL_H = 86
BOLT_R = 13
PLAYER_SIZE = 78

CYCLE = ["triangle", "circle", "square"]
HEART_R = 19
BAR_W, BAR_H = 170, 22
BAD = (200, 50, 50)

_field = None       # scratch surface for the shake, made on first frame
_fonts: dict = {}


def frame(screen, game, source, camera=None, wand=None) -> None:
    global _field
    if _field is None:
        _field = pygame.Surface((FIELD_W, WINDOW_H))
        _fonts.update(
            small=pygame.font.SysFont(None, 40),
            med=pygame.font.SysFont(None, 62),
            lane=pygame.font.SysFont(None, 96),
            huge=pygame.font.SysFont(None, 124),
        )

    _draw_field(_field, game)
    screen.fill(BG)
    if game.shake_ms > 0:
        jitter = (
            random.randint(-SHAKE_PX, SHAKE_PX),
            random.randint(-SHAKE_PX, SHAKE_PX),
        )
        screen.blit(_field, jitter)
    else:
        screen.blit(_field, (0, 0))

    _draw_hud(screen, game, source)
    if camera is not None:
        _draw_camera(screen, camera)
    if wand is not None and camera is not None:
        _wand_panel(screen, wand)


def screen_y(grid_y: float) -> float:
    return FIELD_TOP + grid_y * (FIELD_BOTTOM - FIELD_TOP)


def lane_center(lane: int) -> int:
    return lane * LANE_W + LANE_W // 2


def shape_at(surface, shape, color, cx, cy, size, width=0) -> None:
    """One glyph. The field and the legend share it so they never drift."""
    cx, cy, half = int(cx), int(cy), size // 2
    if shape == "circle":
        pygame.draw.circle(surface, color, (cx, cy), half, width)
    elif shape == "square":
        pygame.draw.rect(
            surface, color, pygame.Rect(cx - half, cy - half, size, size), width
        )
    else:
        pygame.draw.polygon(
            surface,
            color,
            [(cx, cy - half), (cx + half, cy + half), (cx - half, cy + half)],
            width,
        )


# =============================================================================
# The field
# =============================================================================


def _draw_field(field, game) -> None:
    field.fill(BG)
    _lanes(field)
    _walls(field, game.threats)
    _monsters(field, game.threats)
    _bolts(field, game.bolts)
    _player(field, game)


def _lanes(field) -> None:
    for lane in range(1, LANES):
        x = lane * LANE_W
        pygame.draw.line(field, GRID, (x, FIELD_TOP), (x, WINDOW_H), 2)
    pygame.draw.line(field, GRID, (0, FIELD_TOP), (field.get_width(), FIELD_TOP), 2)


def _walls(field, threats) -> None:
    groups: dict = {}
    for threat in threats:
        if threat.kind == "wall":
            # An ungrouped wall is its own group of one. group_id 0 is a real
            # id, so this tests for None rather than truthiness.
            key = threat.group_id if threat.group_id is not None else id(threat)
            groups.setdefault(key, []).append(threat)

    for parts in groups.values():
        lanes = [p.lane for p in parts]
        rect = pygame.Rect(
            min(lanes) * LANE_W + 4,
            int(screen_y(parts[0].y)) - WALL_H // 2,
            (max(lanes) - min(lanes) + 1) * LANE_W - 8,
            WALL_H,
        )
        pygame.draw.rect(field, WALL, rect)
        field.set_clip(rect)
        for x in range(rect.left - WALL_H, rect.right, 18):
            pygame.draw.line(field, BG, (x, rect.bottom), (x + WALL_H, rect.top), 3)
        field.set_clip(None)


def _monsters(field, threats) -> None:
    pulse = 0.55 + 0.45 * math.sin(pygame.time.get_ticks() / 260.0)
    for threat in threats:
        if threat.kind != "monster":
            continue
        cx, cy = lane_center(threat.lane), screen_y(threat.y)
        shape_at(field, threat.shape, COLORS[threat.shape], cx, cy, MONSTER_SIZE)
        if threat.empowered:
            # ponytail: the glow pulses by fading the colour toward the
            # background instead of blitting a per-shape alpha surface. Same
            # read at a glance, one line instead of a surface per monster.
            glow = _fade(EMPOWERED, 0.6 + 0.4 * pulse)
            shape_at(field, threat.shape, glow, cx, cy, MONSTER_SIZE + 10, width=3)


def _bolts(field, bolts) -> None:
    for bolt in bolts:
        color = COLORS[bolt.shape]
        cx, cy = lane_center(bolt.lane), screen_y(bolt.y)
        tail = screen_y(min(1.0, bolt.y + 0.06))
        pygame.draw.line(field, _fade(color, 0.55), (cx, cy), (cx, tail), 5)
        pygame.draw.circle(field, color, (int(cx), int(cy)), BOLT_R)


def _player(field, game) -> None:
    # 10 Hz blink while invulnerable.
    if game.iframe_ms > 0 and int(game.t_ms / 50) % 2:
        return
    cx, half = lane_center(game.player.lane), PLAYER_SIZE // 2
    pygame.draw.polygon(
        field,
        PLAYER,
        [
            (cx, PLAYER_ROW_Y - half),
            (cx + half, PLAYER_ROW_Y + half),
            (cx - half, PLAYER_ROW_Y + half),
        ],
    )


def _fade(color, amount: float):
    """Blend a colour toward the background. amount 1.0 keeps it fully bright."""
    return tuple(int(bg + (c - bg) * amount) for c, bg in zip(color, BG))


# =============================================================================
# The HUD
# =============================================================================


def _draw_hud(screen, game, source) -> None:
    _hearts(screen, game.player.hp)
    _score(screen, game)
    _legend(screen, 26, WINDOW_H - 35, 34)
    if game.state == WAVE_BREAK:
        _wave_break(screen, game)
    elif game.state == GAME_OVER:
        _game_over(screen, game)
    # Above the wave break wash: the controls and the cooldown have to stay
    # readable through it.
    _hint(screen, source)
    _cooldown_bar(screen, game, source)
    _misfire(screen, game)


def _hearts(screen, hp: int) -> None:
    for i in range(PLAYER_HP):
        _heart(screen, 44 + i * 56, 54, HEART_R, i < hp)


def _heart(screen, x, y, r, filled) -> None:
    points = [
        (x, y + r * 0.9),
        (x - r, y - r * 0.1),
        (x - r * 0.5, y - r * 0.7),
        (x, y - r * 0.2),
        (x + r * 0.5, y - r * 0.7),
        (x + r, y - r * 0.1),
    ]
    pygame.draw.polygon(screen, BAD if filled else WALL, points, 0 if filled else 2)


def _score(screen, game) -> None:
    score = _fonts["med"].render(f"{game.score}", True, TEXT)
    screen.blit(score, (FIELD_W - score.get_width() - 26, 30))
    if game.combo > 1.0:
        combo = _fonts["small"].render(f"x{game.combo:.1f}", True, EMPOWERED)
        screen.blit(combo, (FIELD_W - combo.get_width() - 26, 76))


def _legend(screen, x, y, size) -> None:
    """Always visible. Nobody memorises the cycle from a menu."""
    step = size + 30
    for i, shape in enumerate(CYCLE):
        cx = x + size // 2 + i * step
        shape_at(screen, shape, COLORS[shape], cx, y, size)
        _arrow(screen, cx + size // 2 + 6, y, cx + step - size // 2 - 6, y)
    # The cycle closes: the last arrow points at a repeat of the first shape.
    cx = x + size // 2 + len(CYCLE) * step
    shape_at(screen, CYCLE[0], COLORS[CYCLE[0]], cx, y, size, width=2)


def _legend_w(size: int) -> int:
    """Total width of _legend, so callers can centre it."""
    return size + len(CYCLE) * (size + 30)


def _arrow(screen, x1, y1, x2, y2) -> None:
    pygame.draw.line(screen, TEXT, (x1, y1), (x2, y2), 2)
    pygame.draw.polygon(screen, TEXT, [(x2, y2), (x2 - 7, y2 - 5), (x2 - 7, y2 + 5)])


def _hint(screen, source) -> None:
    """Nobody can play a game whose controls are only in a spec file."""
    text = getattr(source, "hint", lambda: "")()
    if not text:
        return
    line = _fonts["small"].render(text, True, WALL)
    screen.blit(line, (FIELD_W - line.get_width() - 26, WINDOW_H - 105))


def _cooldown_bar(screen, game, source) -> None:
    """Says why a keypress did nothing, above the player where you are looking."""
    fill = getattr(source, "cooldown", lambda: None)()
    if fill is None or game.state == GAME_OVER:
        return
    x = lane_center(game.player.lane) - BAR_W // 2
    y = PLAYER_ROW_Y - 104
    pygame.draw.rect(screen, WALL, pygame.Rect(x, y, BAR_W, BAR_H), 2)
    pygame.draw.rect(screen, WALL, (x + 2, y + 2, int((BAR_W - 4) * fill), BAR_H - 4))


def _misfire(screen, game) -> None:
    """Only a wand can trigger this today. See inputs.WandSource."""
    if game.misfire_ms <= 0:
        return
    label = _fonts["small"].render("MISFIRE", True, BAD)
    screen.blit(
        label,
        (lane_center(game.player.lane) - label.get_width() // 2, PLAYER_ROW_Y - 150),
    )


def _wave_break(screen, game) -> None:
    _wash(screen)
    _center(screen, _fonts["huge"], f"WAVE {game.wave}", 430, TEXT)
    _legend(screen, (FIELD_W - _legend_w(64)) // 2, 650, 64)
    _center(screen, _fonts["med"], f"{game.break_left:0.1f}", 775, EMPOWERED)


def _game_over(screen, game) -> None:
    _wash(screen)
    _center(screen, _fonts["huge"], "GAME OVER", 470, BAD)
    _center(screen, _fonts["med"], f"Score {game.score}", 603, TEXT)
    _center(screen, _fonts["med"], f"Wave {game.wave}", 675, TEXT)
    _center(screen, _fonts["small"], "Press R to restart", 775, TEXT)


def _wash(screen) -> None:
    veil = pygame.Surface((FIELD_W, WINDOW_H), pygame.SRCALPHA)
    veil.fill(VEIL)
    screen.blit(veil, (0, 0))


def _center(screen, font, text, y, color) -> None:
    surface = font.render(text, True, color)
    screen.blit(surface, ((FIELD_W - surface.get_width()) // 2, y))


# =============================================================================
# The camera column
# =============================================================================

CAM_X = FIELD_W                       # the panel starts where the game stops
CAM_Y = FIELD_TOP                     # and lines up with the top of the field
CAM_LANE_W = CAM_W // LANES
LANE_TINT = (80, 200, 130, 70)
LANE_NAMES = ("LEFT", "CENTER", "RIGHT")
WARN_H = 76
WARN_BG = (250, 250, 252, 210)
CAM_LINE = (255, 255, 255)
CAM_NOSE = (0, 230, 230)
CAM_SHOULDER = (255, 220, 40)

_preview: tuple = (None, None)        # (bytes we converted, the surface we made)


def _draw_camera(screen, camera) -> None:
    data, points, lane, message = camera.snapshot()
    rect = pygame.Rect(CAM_X, CAM_Y, CAM_W, CAM_H)

    pygame.draw.line(screen, GRID, (CAM_X, 0), (CAM_X, WINDOW_H), 2)
    if data is None:
        pygame.draw.rect(screen, GRID, rect)
    else:
        screen.blit(_preview_surface(data), rect.topleft)

    _cam_lanes(screen, rect, lane)
    if points:
        _cam_body(screen, points)
    if message:
        _cam_warning(screen, rect, message)
    pygame.draw.rect(screen, WALL, rect, 2)

    # One line under the video. A framing problem is drawn on the video itself,
    # so the strip stays a strip.
    label = LANE_NAMES[lane] if lane is not None else "--"
    _cam_center(screen, _fonts["lane"], label, rect.bottom + 10, TEXT)


def _cam_warning(screen, rect, message) -> None:
    """STEP BACK / STEP INTO FRAME, banded across the bottom of the video."""
    band = pygame.Surface((rect.width, WARN_H), pygame.SRCALPHA)
    band.fill(WARN_BG)
    screen.blit(band, (rect.x, rect.bottom - WARN_H))
    text = _fonts["med"].render(message, True, BAD)
    screen.blit(
        text,
        (rect.centerx - text.get_width() // 2, rect.bottom - WARN_H + 12),
    )


def _preview_surface(data):
    """Convert once per camera frame, not once per game frame.

    The camera thread runs at about 25 fps and the game at 60, so the same
    bytes object comes back three times in a row. `is` catches that.
    """
    global _preview
    if data is not _preview[0]:
        _preview = (data, pygame.image.frombuffer(data, (CAM_W, CAM_H), "RGB"))
    return _preview[1]


def _cam_lanes(screen, rect, lane) -> None:
    """The same three lanes as the field, so the two views cannot disagree."""
    if lane is not None:
        tint = pygame.Surface((CAM_LANE_W, CAM_H), pygame.SRCALPHA)
        tint.fill(LANE_TINT)
        screen.blit(tint, (rect.x + lane * CAM_LANE_W, rect.y))
    for i in (1, 2):
        x = rect.x + i * CAM_LANE_W
        pygame.draw.line(screen, CAM_LINE, (x, rect.y), (x, rect.bottom), 3)


def _cam_body(screen, points) -> None:
    """Shoulders and nose, so you can see what the game is actually reading."""
    def at(name):
        x, y = points[name]
        return CAM_X + x, CAM_Y + y

    left, right, nose = at("left"), at("right"), at("nose")
    pygame.draw.line(screen, CAM_SHOULDER, left, right, 5)
    mid = ((left[0] + right[0]) // 2, (left[1] + right[1]) // 2)
    for point in (left, right):
        pygame.draw.circle(screen, CAM_SHOULDER, point, 11)
    pygame.draw.circle(screen, CAM_NOSE, nose, 14)
    # The midpoint is the pixel the lane is read from. Show the thing that decides.
    pygame.draw.circle(screen, BAD, mid, 15)


def _cam_center(screen, font, text, y, color) -> None:
    surface = font.render(text, True, color)
    screen.blit(surface, (CAM_X + (CAM_W - surface.get_width()) // 2, y))


# =============================================================================
# The wand stroke, in the corner of the video
# =============================================================================

WAND_PANEL = 320                  # square, inside the camera preview
WAND_INSET = 24
WAND_LABEL_H = 20                 # room under the art for the shape name
WAND_FADE_S = 2.5                 # how long a finished stroke stays up


def _wand_panel(screen, wand) -> None:
    """live_test.py in miniature.

    Without it a rejected cast is a mystery: the game can say MISFIRE but not
    what you actually drew. It sits inside the video so the layout does not move.
    """
    live, last, name, age, status = wand.snapshot()
    # Top right of the video: the bottom is where the STEP BACK band goes, and
    # you stand in the middle of frame, so the top corner is the free space.
    rect = pygame.Rect(
        CAM_X + CAM_W - WAND_PANEL - WAND_INSET,
        CAM_Y + WAND_INSET,
        WAND_PANEL,
        WAND_PANEL,
    )
    panel = pygame.Surface(rect.size, pygame.SRCALPHA)
    panel.fill(WARN_BG)
    screen.blit(panel, rect.topleft)
    pygame.draw.rect(screen, WALL, rect, 2)

    if status:
        # The board calibrates for about two seconds on connect and the wand
        # has to be still for it. Say so rather than looking broken.
        _wand_label(screen, rect, status, BAD)
        return

    # While a stroke is in progress it is the only thing shown. Drawing the
    # previous cast underneath it just gives you two shapes to read.
    screen.set_clip(rect)
    if len(live) > 1:
        pygame.draw.lines(screen, PLAYER, False, _wand_points(rect, live), 5)
    elif age < WAND_FADE_S and len(last) > 1:
        pygame.draw.lines(
            screen, COLORS.get(name, WALL), False, _wand_points(rect, last), 5
        )
    screen.set_clip(None)

    if not live and age < WAND_FADE_S:
        _wand_label(screen, rect, (name or "MISFIRE").upper(), COLORS.get(name, BAD))


def _wand_points(rect, stroke):
    """Degrees to panel pixels at a fixed scale, y up, centred on the panel.

    Fixed, not fitted. Fitting rescaled the drawing on every frame, so an early
    two-degree wobble filled the whole box and the line whipped around while
    you drew: that is what reads as the wand being too sensitive. The firmware
    zeroes its position when you press the button, so every stroke starts dead
    centre and simply grows outward from there. Same model as live_test.py.

    Drawing bigger than the panel is clipped, which is honest feedback. Scale
    with WAND_PX_PER_DEG.
    """
    return [
        (
            rect.centerx + p[0] * WAND_PX_PER_DEG,
            rect.centery - WAND_LABEL_H - p[1] * WAND_PX_PER_DEG,
        )
        for p in stroke
    ]


def _wand_label(screen, rect, text, color) -> None:
    surface = _fonts["small"].render(text, True, color)
    screen.blit(surface, (rect.centerx - surface.get_width() // 2, rect.bottom - 48))
