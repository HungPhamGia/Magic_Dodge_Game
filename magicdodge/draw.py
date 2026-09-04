"""Everything you see. Art from element/, primitives for the rest.

One entry point: frame(screen, game, source). The field is drawn first onto its
own surface so a screen shake can offset it, then the HUD goes on top at rest.

Every sprite is optional. A missing or unreadable file falls back to the drawn
glyph it replaced, so a half-populated element/ folder costs you the art, not
the playtest.
"""

import math
import random
from pathlib import Path

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
    PATH_LEFT,
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
from .game import BEATS, GAME_OVER, MENU, WAVE_BREAK

MONSTER_SIZE = 130
WALL_H = 86
BOLT_R = 13             # the fallback circle, when the spell art is missing
BOLT_ART_H = 56         # the spell in flight. About 0.43 of MONSTER_SIZE, so
                        # it reads as a projectile without hiding its target
BOLT_ALPHA = 16         # alpha above which a bolt pixel counts as content.
                        # The art carries a huge sub-16 glow that never
                        # renders; see _load_art for why that matters
PLAYER_SIZE = 78        # the fallback triangle, when character.png is missing
PLAYER_ART_H = 138      # the wizard. Aspect 0.954, so 131 wide in a 148 lane
WALK_STEP_PX = 31       # road travelled per walk frame. THE cadence knob: a
                        # full 4 frame cycle covers 124px, about his own
                        # height, so the stride matches the ground going by.
                        # Lower = faster feet for the same speed

# Walked off game.BEATS, never written down twice: in the legend an arrow
# always means "beats", so it cannot drift from the rules it teaches.
# Seeded on fire and walked round: fire kills earth, earth kills water, water
# kills fire. The seed lives here and not in game.BEATS because which element
# the wave break opens on is presentation, not a rule.
CYCLE = ["triangle"]
while len(CYCLE) < len(BEATS):
    CYCLE.append(BEATS[CYCLE[-1]])
HEART_R = 19
BAR_W, BAR_H = 240, 22  # the reload gauge, bottom left where the legend was
MATCH_SIZE = 96         # emblem height in the wave break matchup rows
MATCH_PITCH = 128       # row to row
# Four columns across the 608px field: the shape you draw, the spell it throws,
# the arrow, and the monster it kills. Explicit rather than derived from a gap,
# because the art either side is not the same width and the row has to look
# balanced, not be arithmetically symmetric.
GLYPH_X = 116           # the shape, labelling the spell beside it
GLYPH_SIZE = 56         # smaller than the art: it is a label, not a subject
SPELL_X = 220
ARROW_X1, ARROW_X2 = 280, 342
MONSTER_X = 452
OUTLINE_GROW = 16       # how far the traced shape stands off the monster
OUTLINE_PX = 4          # stroke width, thick enough to read over busy art
BAD = (200, 50, 50)

ELEMENTS = Path(__file__).parent.parent / "element"
# The walk cycle, in order. All four are drawn to the bottom of their own
# canvas, so aligning them midbottom keeps his feet on the road; the robe and
# staff move between frames because they are meant to.
WALK = ["character.png", "character2.png", "character3.png", "character4.png"]
# The spell art, drawn on the bolt rather than on the monster. Listed apart
# because it is prepared differently on the way in: see _load_art.
BOLT_ART = {"triangle": "fire.png", "circle": "water.png", "square": "earth.png"}
SPRITES = {
    "triangle": "fire_mons.png",
    "circle":   "water_mons.png",
    "square":   "earth_mons.png",
    **{f"player{i}": name for i, name in enumerate(WALK)},
    **{f"bolt_{shape}": name for shape, name in BOLT_ART.items()},
}

_field = None       # scratch surface for the shake, made on first frame
_fonts: dict = {}
_backdrop = None    # element/Background.png at field size, or None
_sky_surf = None    # baked starry sky above the vanishing point
_raw: dict = {}     # name -> the file as loaded
_scaled: dict = {}  # (name, height) -> surface at that height, or None
_fill: dict = {}    # name -> how much of its canvas height is real content

_px = None          # the player's animated x, eased for a smooth lane-change glide
_plast_lane = None  # last integer lane, to catch the moment a change happens
_pdir = 0           # direction of the last change, for the motion trail
_pswitch_ms = -10000  # tick of the last lane change, for the arrival spark


def frame(screen, game, source, camera=None, wand=None, coach=None, hr=None) -> None:
    global _field
    if _field is None:
        _field = pygame.Surface((FIELD_W, WINDOW_H))
        _fonts.update(
            small=pygame.font.SysFont(None, 40),
            med=pygame.font.SysFont(None, 62),
            lane=pygame.font.SysFont(None, 96),
            huge=pygame.font.SysFont(None, 124),
            title=pygame.font.SysFont(None, 112),   # MAGICDODGE is 599px wide
                                                    # at huge, in a 608px field

            coach=pygame.font.SysFont(None, 34),
        )
        _load_art()

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

    _draw_hud(screen, game, source, coach, hr)
    if camera is not None:
        _draw_camera(screen, camera)
    if wand is not None and camera is not None:
        _wand_panel(screen, wand)


def screen_y(grid_y: float) -> float:
    return FIELD_TOP + grid_y * (FIELD_BOTTOM - FIELD_TOP)


def _depth(grid_y: float) -> float:
    """Kept for callers that only want a size cue."""
    return 0.60 + 0.55 * max(0.0, min(1.0, grid_y))


# --- perspective (the llm_update chase angle) --------------------------------
HORIZON = 250                        # y of the vanishing band
PERSP = 3.2                          # convergence strength; higher = deeper
SMIN = 1.0 / (1.0 + PERSP)
CXF = FIELD_W / 2.0


def _persp(t: float) -> float:
    return 1.0 / (1.0 + PERSP * (1.0 - max(0.0, min(1.0, t))))


def project(lane, t):
    """Lane 0..2 and depth t (0 far at the gate, 1 near at your feet) to a screen
    point and a scale, in perspective: lanes fan out from the vanishing point and
    a sprite grows as it nears. The art sprites are drawn at these points."""
    s = _persp(t)
    y = HORIZON + (FIELD_BOTTOM - HORIZON) * (s - SMIN) / (1.0 - SMIN)
    x = CXF + (lane - 1) * LANE_W * s
    return x, y, s


def _edge(off, t):
    """A point on a rail of constant lane offset (in lane widths) at depth t."""
    s = _persp(t)
    y = HORIZON + (FIELD_BOTTOM - HORIZON) * (s - SMIN) / (1.0 - SMIN)
    return CXF + off * LANE_W * s, y


def road_px(game) -> float:
    """How far he has walked, in pixels.

    game.scroll counts in the same y units a threat falls in, so this is the
    one conversion that keeps the road, the threats and the stride agreeing.
    """
    return game.scroll * (FIELD_BOTTOM - FIELD_TOP)


def lane_left(lane: int) -> int:
    """Left pixel of a lane. Lanes live on the art's floor, not the window."""
    return PATH_LEFT + lane * LANE_W


def lane_center(lane: int) -> int:
    return lane_left(lane) + LANE_W // 2


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
# The art
# =============================================================================


def _load_art() -> None:
    """element/, once, after set_mode. A missing file is a warning, not a stop."""
    global _backdrop
    try:
        art = pygame.image.load(str(ELEMENTS / "Background.png")).convert()
        _backdrop = pygame.transform.smoothscale(art, (FIELD_W, WINDOW_H))
    except Exception as error:
        print(f"No backdrop ({error}); flat colour.")
    for name, filename in SPRITES.items():
        try:
            art = pygame.image.load(str(ELEMENTS / filename)).convert_alpha()
        except Exception as error:
            print(f"No art for {name} ({error}); drawing the shape instead.")
            continue
        if name.startswith("bolt_"):
            # Crop before anything else. About 85% of each spell canvas is a
            # sub-BOLT_ALPHA glow that never renders, and the three enclose
            # their content at very different sizes -- earth fills 78% of its
            # canvas, water 46% -- so scaling by canvas would draw earth half
            # again as big as water. Cropping to real content equalises them.
            art = art.subsurface(art.get_bounding_rect(min_alpha=BOLT_ALPHA)).copy()
            # Then turn it round: a bolt flies UP the field, and all of this
            # art is drawn mass down, wisps up. Unrotated the wisps would lead.
            art = pygame.transform.rotate(art, 180)
        _raw[name] = art
        # Monster art sits in 18-30% of canvas padding while the bolt art is
        # cropped flush, so the same `size` draws them at visibly different
        # scales. Recorded here so _content_h can cancel it out.
        _fill[name] = art.get_bounding_rect(min_alpha=BOLT_ALPHA).h / art.get_height()


def _sprite(name: str, height: int):
    """The art at that height, kept. None means draw the glyph instead.

    smoothscale because every asset is a heavy downscale -- the monsters land
    at about 0.14x, the wizard at 0.12x -- and nearest at that ratio throws
    away most of the source and aliases. The blocks are big enough that
    bilinear still reads as pixel art.
    """
    key = (name, height)
    if key not in _scaled:
        art = _raw.get(name)
        if art is None:
            _scaled[key] = None
        else:
            w, h = art.get_size()
            _scaled[key] = pygame.transform.smoothscale(
                art, (max(1, round(w * height / h)), height)
            )
    return _scaled[key]


def _content_h(name: str, visible: int) -> int:
    """Canvas height that renders `visible` pixels of actual creature.

    Only worth using where two pieces of art are compared side by side -- the
    wave break rows -- since that is where unequal canvas padding shows. The
    field sizes monsters by canvas as before, so nothing in play moves.
    """
    return round(visible / (_fill.get(name) or 1.0))


def _emblem(surface, shape, color, cx, cy, size, art=None) -> None:
    """The art for this shape, or the plain shape when there is no art.

    The shape is a fallback now, not a label drawn alongside: with the
    elementals in, the creature is its own identity and a ring round it as
    well only crowds the lane. A missing PNG still draws and still plays.

    art picks which picture: the monster by default, or a bolt_ entry for the
    spell that kills it. Either way the fallback is the shape, because the
    shape is what the rules are written in.
    """
    sprite = _sprite(art or shape, size)
    if sprite is None:
        shape_at(surface, shape, color, cx, cy, size)
    else:
        surface.blit(sprite, sprite.get_rect(center=(int(cx), int(cy))))


# =============================================================================
# The field
# =============================================================================


def _blit_glow(surface, color, cx, cy, radius) -> None:
    g = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    for r in range(radius, 0, -2):
        a = int(90 * (1 - r / radius) ** 2)
        pygame.draw.circle(g, (*color, a), (radius, radius), r)
    surface.blit(g, (int(cx) - radius, int(cy) - radius))


def _sky():
    """A baked night sky the corridor recedes into: stars, and a soft glow at the
    vanishing point so the far end reads as opening onto the sky."""
    global _sky_surf
    if _sky_surf is None:
        s = pygame.Surface((FIELD_W, WINDOW_H))
        s.fill(BG)
        rng = random.Random(7)
        for _ in range(150):
            x, y = rng.randint(0, FIELD_W), rng.randint(0, HORIZON + 120)
            b = rng.randint(70, 180)
            pygame.draw.circle(s, (b, b, min(255, b + 24)), (x, y),
                               rng.choice([1, 1, 1, 2]))
        _blit_glow(s, (150, 130, 210), FIELD_W // 2, int(HORIZON), 190)
        _sky_surf = s
    return _sky_surf


def _column(field, x, yb, h, w, a, s, inner) -> None:
    """A detailed stone column with a lit torch, in the dungeon's grey masonry:
    three shading bands for roundness, brick courses, a capital and a base, and
    a bracketed flame on the side facing the road. a fades it with distance."""
    x, w = int(x), max(3, int(w))
    top = int(yb - h)
    light, mid, dark = _fade((162, 156, 156), a), _fade((120, 114, 118), a), _fade((78, 74, 82), a)
    # contact shadow, so the column reads as planted on the floor, not floating
    sw2, sh2 = int(w * 2.2), max(4, int(0.05 * h))
    shadow = pygame.Surface((sw2, sh2), pygame.SRCALPHA)
    pygame.draw.ellipse(shadow, (0, 0, 0, int(130 * a)), shadow.get_rect())
    field.blit(shadow, (x - sw2 // 2, int(yb) - sh2 // 2))
    # shaft, rounded by three vertical shading bands
    pygame.draw.rect(field, mid, (x - w // 2, top, w, int(h)))
    pygame.draw.rect(field, light, (x - w // 2, top, max(1, w // 3), int(h)))
    pygame.draw.rect(field, dark, (x + w // 6, top, max(1, w // 3), int(h)))
    # brick courses
    seg = max(6, int(0.09 * h))
    for by in range(top + seg, int(yb), seg):
        pygame.draw.line(field, dark, (x - w // 2, by), (x + w // 2, by), 1)
    # capital and base, a touch wider than the shaft
    cw, ch = int(w * 1.5), max(3, int(0.055 * h))
    pygame.draw.rect(field, light, (x - cw // 2, top - ch, cw, ch))
    pygame.draw.rect(field, mid, (x - cw // 2, int(yb) - ch, cw, ch))
    pygame.draw.rect(field, dark, (x - cw // 2, top - ch, cw, ch), 1)
    # a flaming torch on the inner side, only once the column is solid enough
    if a > 0.4:
        fx = x + inner * int(w * 0.55)
        fy = top + int(0.17 * h)
        flick = 0.8 + 0.35 * math.sin(pygame.time.get_ticks() / 90.0 + x * 0.3)
        fr = max(3, int(9 * s * flick))
        pygame.draw.line(field, (64, 46, 30), (x, fy), (fx, fy), max(2, int(3 * s)))
        _blit_glow(field, (255, 168, 60), fx, fy - fr, int(fr * 3.4))
        pygame.draw.ellipse(field, (238, 120, 34), (fx - fr, fy - fr * 3, fr * 2, fr * 3))
        pygame.draw.ellipse(field, (255, 198, 74),
                            (fx - int(fr * 0.6), fy - int(fr * 2.4), int(fr * 1.2), int(fr * 2)))
        pygame.draw.ellipse(field, (255, 248, 200),
                            (fx - max(1, int(fr * 0.3)), fy - int(fr * 1.6),
                             max(2, int(fr * 0.6)), max(2, int(fr * 1.1))))


def _persp_columns(field, game) -> None:
    """Columns planted at fixed points along the road and carried by the SAME
    scroll as the floor, so they travel with the road toward you. Each enters
    faded at the far end and fades out as it nears, so nothing pops."""
    R = road_px(game)                      # distance walked, same units as the floor
    view = float(FIELD_BOTTOM - FIELD_TOP)  # one screenful of travel ahead
    spacing = view / 5.0                    # gap between column pairs
    first = int(R // spacing)
    for m in range(first, first + 8):
        rel = m * spacing - R               # how far ahead this column still is
        if rel < 0.0 or rel > view:
            continue
        t = 1.0 - rel / view                # 0 far, 1 near -- moves with the road
        a = max(0.0, min((view - rel) / (view * 0.30),   # fade in at the far end
                         rel / (view * 0.14), 1.0))       # fade out as it nears
        if a <= 0.03:
            continue
        s = _persp(t)
        h, w = 360 * s, max(3, int(42 * s))
        for off, inner in ((-1.62, 1), (1.62, -1)):    # on the floor's edge, not beyond it
            x, yb = _edge(off, t)
            _column(field, x, yb, h, w, a, s, inner)


def _persp_backdrop(field, game) -> bool:
    """Warp Hung's dungeon art into a receding trapezoid that runs back to the
    vanishing point (the llm_update chase angle). The floor scrolls toward you so
    you read as walking forward. Art is kept; only its projection changes."""
    if _backdrop is None:
        return False
    src = _backdrop
    sw, sh = src.get_size()
    scroll = int(road_px(game))
    n = 80
    slice_h = max(1, sh // n)
    for i in range(n):
        f0, f1 = i / n, (i + 1) / n
        lx, ya = _edge(-1.7, f0)
        rx, _ = _edge(1.7, f0)
        _, yb = _edge(-1.7, f1)
        w, h = max(1, int(rx - lx)), max(1, int(yb - ya) + 1)
        v = int(f0 * sh - scroll) % sh          # subtract: floor flows toward you
        if v + slice_h > sh:
            v = sh - slice_h
        strip = src.subsurface((0, v, sw, slice_h))
        warped = pygame.transform.smoothscale(strip, (w, h))
        if f0 < 0.32:                       # fade the far end into the starry sky
            warped.set_alpha(int(255 * (f0 / 0.32)))
        field.blit(warped, (int(lx), int(ya)))
    return True


def _draw_field(field, game) -> None:
    field.blit(_sky(), (0, 0))                   # starry sky the corridor opens onto
    if not _persp_backdrop(field, game):
        for off in (-1.5, -0.5, 0.5, 1.5):
            pygame.draw.line(field, GRID, _edge(off, 0.0), _edge(off, 1.0), 2)
    _persp_columns(field, game)                  # columns down both sides
    for off in (-0.5, 0.5):                       # faint lane dividers
        pygame.draw.line(field, GRID, _edge(off, 0.0), _edge(off, 1.0), 1)
    _walls(field, game.threats)
    _monsters(field, game.threats)
    _bolts(field, game.bolts)
    _player(field, game)


def _walls(field, threats) -> None:
    groups: dict = {}
    for threat in threats:
        if threat.kind == "wall":
            # An ungrouped wall is its own group of one. group_id 0 is a real
            # id, so this tests for None rather than truthiness.
            key = threat.group_id if threat.group_id is not None else id(threat)
            groups.setdefault(key, []).append(threat)

    for parts in sorted(groups.values(), key=lambda g: g[0].y):
        lanes = [p.lane for p in parts]
        _, y, s = project(lanes[0], parts[0].y)
        lx = CXF + (min(lanes) - 1.5) * LANE_W * s
        rx = CXF + (max(lanes) - 0.5) * LANE_W * s
        h = max(10, int(WALL_H * s))
        rect = pygame.Rect(int(lx) + 3, int(y - h / 2), int(rx - lx) - 6, h)
        pygame.draw.rect(field, WALL, rect)
        field.set_clip(rect)
        for x in range(rect.left - h, rect.right, 18):
            pygame.draw.line(field, BG, (x, rect.bottom), (x + h, rect.top), 3)
        field.set_clip(None)


def _monsters(field, threats) -> None:
    pulse = 0.55 + 0.45 * math.sin(pygame.time.get_ticks() / 260.0)
    for threat in sorted(threats, key=lambda t: t.y):      # far first, near on top
        if threat.kind != "monster":
            continue
        cx, cy, s = project(threat.lane, threat.y)          # perspective place + scale
        size = max(16, int(MONSTER_SIZE * s * 1.15))
        _emblem(field, threat.shape, COLORS[threat.shape], cx, cy, size)
        if threat.empowered:
            # ponytail: the glow pulses by fading the colour toward the
            # background instead of blitting a per-shape alpha surface. Same
            # read at a glance, one line instead of a surface per monster.
            glow = _fade(EMPOWERED, 0.6 + 0.4 * pulse)
            shape_at(field, threat.shape, glow, cx, cy, size + 10, width=3)


def _bolts(field, bolts) -> None:
    for bolt in sorted(bolts, key=lambda b: b.y):
        color = COLORS[bolt.shape]
        cx, cy, s = project(bolt.lane, bolt.y)              # perspective place + scale
        x2, y2, _ = project(bolt.lane, min(1.0, bolt.y + 0.06))
        pygame.draw.line(field, _fade(color, 0.55), (cx, cy), (x2, y2), max(2, int(5 * s)))
        art = _sprite(f"bolt_{bolt.shape}", max(10, int(BOLT_ART_H * s * 1.1)))
        if art is None:
            pygame.draw.circle(field, color, (int(cx), int(cy)), max(3, int(BOLT_R * s)))
        else:
            field.blit(art, art.get_rect(center=(int(cx), int(cy))))


def _update_player_x(game) -> None:
    """Ease the drawn player x toward its lane and note when it changes, so he
    glides between lanes instead of snapping."""
    global _px, _plast_lane, _pdir, _pswitch_ms
    target = project(game.player.lane, 1.0)[0]
    if _px is None:
        _px, _plast_lane = float(target), game.player.lane
    if game.player.lane != _plast_lane:
        _pdir = 1 if game.player.lane > _plast_lane else -1
        _pswitch_ms = pygame.time.get_ticks()
        _plast_lane = game.player.lane
    _px += (target - _px) * 0.20


def _lane_spark(field, x, y, p) -> None:
    """A ring of sparks that flies out when a lane is taken (p is 0..1)."""
    for i in range(8):
        a = math.pi * 2 * i / 8
        r = 24 + 70 * p
        dot = pygame.Surface((8, 8), pygame.SRCALPHA)
        pygame.draw.circle(dot, (255, 220, 120, int(210 * (1 - p))), (4, 4), 3)
        field.blit(dot, (int(x + r * math.cos(a)) - 4, int(y + r * 0.45 * math.sin(a)) - 4))


def _player_triangle(field, cx, alpha) -> None:
    """The fallback player, as a translucent triangle (alpha for the trail)."""
    half = PLAYER_SIZE // 2
    surf = pygame.Surface((PLAYER_SIZE, PLAYER_SIZE), pygame.SRCALPHA)
    pygame.draw.polygon(surf, (*PLAYER, alpha),
                        [(half, 0), (PLAYER_SIZE, PLAYER_SIZE), (0, PLAYER_SIZE)])
    field.blit(surf, (int(cx) - half, PLAYER_ROW_Y - PLAYER_SIZE + half))


def _player(field, game) -> None:
    # 10 Hz blink while invulnerable.
    if game.iframe_ms > 0 and int(game.t_ms / 50) % 2:
        return
    _update_player_x(game)
    x = _px if _px is not None else project(game.player.lane, 1.0)[0]
    target = project(game.player.lane, 1.0)[0]
    # The stride is driven by ground covered, not by the clock, so the feet
    # keep up on a fast wave and slow down on a slow one without a second knob.
    name = f"player{int(road_px(game) / WALK_STEP_PX) % len(WALK)}"
    if _sprite(name, PLAYER_ART_H) is None:
        name = "player0"          # a part filled element/ must not flicker
    sprite = _sprite(name, PLAYER_ART_H)

    if abs(target - x) > 4:                          # motion trail during a glide
        for i, a in ((1, 90), (2, 46)):
            gx = int(x - _pdir * i * 20)
            if sprite is None:
                _player_triangle(field, gx, a)
            else:
                ghost = sprite.copy()
                ghost.set_alpha(a)
                field.blit(ghost, ghost.get_rect(midbottom=(gx, PLAYER_ROW_Y)))

    if sprite is None:
        _player_triangle(field, int(x), 255)
    else:
        field.blit(sprite, sprite.get_rect(midbottom=(int(x), PLAYER_ROW_Y)))

    age = pygame.time.get_ticks() - _pswitch_ms      # arrival spark
    if age < 320:
        _lane_spark(field, target, PLAYER_ROW_Y, age / 320.0)


def _fade(color, amount: float):
    """Blend a colour toward the background. amount 1.0 keeps it fully bright."""
    return tuple(int(bg + (c - bg) * amount) for c, bg in zip(color, BG))


# =============================================================================
# The HUD
# =============================================================================


def _draw_hud(screen, game, source, coach=None, hr=None) -> None:
    _hearts(screen, game.player.hp)
    _score(screen, game)
    _hr_readout(screen, hr)
    if game.state == MENU:
        _menu(screen, game, hr)
    elif game.state == WAVE_BREAK:
        _wave_break(screen, game)
    elif game.state == GAME_OVER:
        _game_over(screen, game, coach)
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


HR_POS = (26, 92)       # under the hearts, which end at 71. FIELD_TOP is 80,
                        # so there is no HUD band left down here and this is
                        # over the playfield by necessity
HR_PAD = 12             # breathing room inside its backdrop
HR_VEIL = 205           # backdrop alpha. Opaque enough to read the digits over
                        # the floor art, sheer enough that a monster spawning in
                        # the left lane still shows through instead of vanishing


def _hr_readout(screen, hr) -> None:
    """Heart rate under the hearts, in the big HUD type.

    Red is a real watch feeding it, grey is the simulated stand-in. Without
    that you cannot tell a connected watch from a plausible invention, which
    is the one thing you want to know before a demo starts.

    It sits below FIELD_TOP, so it paints its own backdrop first: otherwise a
    monster falling down the left lane reads straight through the digits, and
    the FIELD_TOP rule cuts across them.
    """
    if hr is None:
        return
    live = not hr.simulated
    line = _fonts["med"].render(f"{hr.current()} bpm", True, BAD if live else WALL)
    x, y = HR_POS
    panel = pygame.Rect(x - HR_PAD, y - HR_PAD // 2,
                        line.get_width() + HR_PAD * 2, line.get_height() + HR_PAD)
    veil = pygame.Surface(panel.size, pygame.SRCALPHA)
    pygame.draw.rect(veil, (*BG, HR_VEIL), veil.get_rect(), border_radius=8)
    pygame.draw.rect(veil, BAD if live else GRID, veil.get_rect(), 2, border_radius=8)
    screen.blit(veil, panel.topleft)
    screen.blit(line, (x, y))


def _score(screen, game) -> None:
    score = _fonts["med"].render(f"{game.score}", True, TEXT)
    screen.blit(score, (FIELD_W - score.get_width() - 26, 30))
    if game.combo > 1.0:
        combo = _fonts["small"].render(f"x{game.combo:.1f}", True, EMPOWERED)
        screen.blit(combo, (FIELD_W - combo.get_width() - 26, 76))


def _matchups(screen, top: int, size: int) -> None:
    """Three rows: draw THIS shape, kill THAT monster.

    Read off game.BEATS, so it cannot teach a pairing the rules do not play.

    Both sides carry their own shape, so the row reads shape beats shape.
    The left labels the spell, which is the only thing connecting a picture of
    fire to the key or the wand stroke that throws it; the right names the
    monster the same way, so an elemental you meet mid wave is already a shape
    you have seen rather than a creature you have to translate.
    """
    for i, spell in enumerate(CYCLE):
        y = top + i * MATCH_PITCH
        beaten = BEATS[spell]
        shape_at(screen, spell, COLORS[spell], GLYPH_X, y, GLYPH_SIZE)
        # The spell uses the bolt art, stored rotated head up, so the picture
        # here is the picture you see coming out of the wand.
        _emblem(screen, spell, COLORS[spell], SPELL_X, y,
                _content_h(f"bolt_{spell}", size), art=f"bolt_{spell}")
        _arrow(screen, ARROW_X1, y, ARROW_X2, y)
        _emblem(screen, beaten, COLORS[beaten], MONSTER_X, y,
                _content_h(beaten, size))
        # The monster's own shape, drawn last so the art cannot cover it.
        shape_at(screen, beaten, COLORS[beaten], MONSTER_X, y,
                 size + OUTLINE_GROW, OUTLINE_PX)


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
    """Says why a keypress did nothing. Fills as the cast recharges.

    Parked bottom left, in the slot the cycle legend used to hold, rather than
    floating over the player. That keeps the lane clear, at the cost of no
    longer sitting where your eyes already are -- hence the wider bar.
    """
    fill = getattr(source, "cooldown", lambda: None)()
    if fill is None or game.state == GAME_OVER:
        return
    x, y = 26, WINDOW_H - 52
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


STUCK_MS = 25_000    # waiting this long means the watch is off, not slow


def _menu(screen, game, hr) -> None:
    """The start screen. Waits for the watch, and says so while it waits.

    The gate itself is heart_rate.ready(); this only reports it, so the screen
    cannot promise a start the loop will refuse. hr is None in the render test
    and in any caller that has no monitor, which counts as nothing to wait for.
    """
    _wash(screen)
    _center(screen, _fonts["title"], "MAGICDODGE", 210, TEXT)

    ready = hr is None or hr.ready()
    if hr is not None and hr.device_wanted:
        if hr.simulated:
            line, color = "Waiting for the heart rate watch...", WALL
        else:
            line, color = f"Heart rate watch ready  -  {hr.current()} bpm", BAD
    else:
        line, color = "No heart rate watch  -  effort is simulated", WALL
    _center(screen, _fonts["small"], line, 360, color)

    # A watch that has not turned up in half a minute is not going to, and the
    # way out of the wait is a launch flag nobody remembers under demo lights.
    if not ready and game.t_ms > STUCK_MS:
        _center(screen, _fonts["small"], "no watch? relaunch with  --no-hr",
                415, _fade(WALL, 0.6))

    _matchups(screen, 560, MATCH_SIZE)

    # Drawn either way so you know what to press before you may press it, dim
    # until the gate opens rather than appearing from nowhere.
    _center(screen, _fonts["med"], "Press SPACE to start", 1000,
            EMPOWERED if ready else _fade(WALL, 0.45))


def _wave_break(screen, game) -> None:
    _wash(screen)
    _center(screen, _fonts["huge"], f"WAVE {game.wave}", 430, TEXT)
    _matchups(screen, 600, MATCH_SIZE)
    _center(screen, _fonts["med"], f"{game.break_left:0.1f}", 950, EMPOWERED)


COACH_ACCENT = (120, 210, 255)       # the "AI Coach" heading colour


def _game_over(screen, game, coach=None) -> None:
    _wash(screen)
    _center(screen, _fonts["huge"], "GAME OVER", 250, BAD)
    _center(screen, _fonts["med"], f"Score {game.score}", 380, TEXT)
    _center(screen, _fonts["med"], f"Wave {game.wave}", 448, TEXT)
    _coach_panel(screen, coach, 560)
    _center(screen, _fonts["small"], "Press R to restart", WINDOW_H - 120, TEXT)


def _coach_panel(screen, coach, top: int) -> None:
    """The LLM coach's read-out, or its progress line while it thinks. Kept to
    the game column width, wrapped, so a long paragraph never runs off the edge."""
    if coach is None:
        return
    margin = 40
    width = FIELD_W - margin * 2
    _center(screen, _fonts["med"], "AI COACH", top, COACH_ACCENT)
    y = top + 70

    if coach.status != "ready" or not coach.feedback:
        _center(screen, _fonts["small"], "analyzing your run...", y + 20, WALL)
        return

    fb = coach.feedback
    y = _wrap(screen, fb.get("headline", ""), margin, y, width, _fonts["coach"], TEXT) + 14
    for label, items, color in (("What went well", fb.get("did_well", []), COLORS["square"]),
                                ("To improve", fb.get("improve", []), EMPOWERED)):
        if not items:
            continue
        line = _fonts["coach"].render(label, True, color)
        screen.blit(line, (margin, y))
        y += line.get_height() + 4
        for item in items:
            y = _wrap(screen, "- " + item, margin + 10, y, width - 10, _fonts["coach"], TEXT) + 4
        y += 8
    if fb.get("effort"):
        y = _wrap(screen, "Effort: " + fb["effort"], margin, y + 2, width, _fonts["coach"], BAD) + 6
    if fb.get("tip"):
        y = _wrap(screen, "Tip: " + fb["tip"], margin, y + 2, width, _fonts["coach"], COACH_ACCENT) + 4


def _wrap(screen, text, x, y, width, font, color) -> int:
    """Blit `text` word-wrapped to `width`, starting at (x, y). Returns the y
    just past the last line, so callers can stack blocks."""
    words = text.split()
    line = ""
    for word in words:
        trial = f"{line} {word}".strip()
        if font.size(trial)[0] > width and line:
            screen.blit(font.render(line, True, color), (x, y))
            y += font.get_height()
            line = word
        else:
            line = trial
    if line:
        screen.blit(font.render(line, True, color), (x, y))
        y += font.get_height()
    return y


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
PANEL_INK = (34, 36, 48)              # drawn ON that light panel, so it stays
                                      # dark while the rest of the palette is not
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
        pygame.draw.lines(screen, PANEL_INK, False, _wand_points(rect, live), 5)
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
