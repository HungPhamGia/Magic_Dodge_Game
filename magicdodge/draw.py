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
from .game import BEATS, GAME_OVER, WAVE_BREAK

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
MATCH_GAP = 88          # centre of the field to either emblem's centre
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
_raw: dict = {}     # name -> the file as loaded
_scaled: dict = {}  # (name, height) -> surface at that height, or None
_fill: dict = {}    # name -> how much of its canvas height is real content


def frame(screen, game, source, camera=None, wand=None, coach=None, hr=None) -> None:
    global _field
    if _field is None:
        _field = pygame.Surface((FIELD_W, WINDOW_H))
        _fonts.update(
            small=pygame.font.SysFont(None, 40),
            med=pygame.font.SysFont(None, 62),
            lane=pygame.font.SysFont(None, 96),
            huge=pygame.font.SysFont(None, 124),
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


def _draw_field(field, game) -> None:
    if _backdrop is None:
        field.fill(BG)
    else:
        # The road slides down at exactly the speed threats fall, so standing
        # monsters hold still against it and he reads as walking onto them.
        # The art's top and bottom edges agree to about 2% luminance, so the
        # stone joins invisibly. What hangs off the surface is clipped, so
        # the whole thing costs about one screenful.
        #
        # ponytail: the torches do NOT line up across the join. They repeat
        # every ~223px but the wrap leaves a 387px gap, so their rhythm
        # hitches once per loop, every 4-10s depending on wave. Fix, if it
        # ever annoys anyone: crop _backdrop to a whole number of torch
        # periods (rows 87..1203) in _load_art. The loop below already
        # handles a tile shorter than the window, so that is the only change.
        tile = _backdrop.get_height()
        y = int(road_px(game)) % tile - tile
        while y < WINDOW_H:
            field.blit(_backdrop, (0, y))
            y += tile
    _lanes(field)
    _walls(field, game.threats)
    _monsters(field, game.threats)
    _bolts(field, game.bolts)
    _player(field, game)


def _lanes(field) -> None:
    # With the art there is nothing to draw: its masonry already bounds the
    # corridor, and its floor is one even tile grid, so a line ruled over it
    # reads as UI laid on top of a picture rather than as part of the room.
    if _backdrop is None:
        # Both corridor edges too, not just the two splits: without the art
        # there is nothing else to say where you may stand.
        for lane in range(LANES + 1):
            x = lane_left(lane)
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
            lane_left(min(lanes)) + 4,
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
        _emblem(field, threat.shape, COLORS[threat.shape], cx, cy, MONSTER_SIZE)
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
        # The streak stays whether or not there is art: a bolt crosses the
        # field in BOLT_TRAVEL_S, about 15 frames, and the trail is what makes
        # it readable at that speed.
        pygame.draw.line(field, _fade(color, 0.55), (cx, cy), (cx, tail), 5)
        art = _sprite(f"bolt_{bolt.shape}", BOLT_ART_H)
        if art is None:
            pygame.draw.circle(field, color, (int(cx), int(cy)), BOLT_R)
        else:
            field.blit(art, art.get_rect(center=(int(cx), int(cy))))


def _player(field, game) -> None:
    # 10 Hz blink while invulnerable.
    if game.iframe_ms > 0 and int(game.t_ms / 50) % 2:
        return
    cx = lane_center(game.player.lane)
    # The stride is driven by ground covered, not by the clock, so the feet
    # keep up on a fast wave and slow down on a slow one without a second knob.
    name = f"player{int(road_px(game) / WALK_STEP_PX) % len(WALK)}"
    if _sprite(name, PLAYER_ART_H) is None:
        name = "player0"          # a part filled element/ must not flicker
    sprite = _sprite(name, PLAYER_ART_H)
    if sprite is None:
        half = PLAYER_SIZE // 2
        pygame.draw.polygon(
            field,
            PLAYER,
            [
                (cx, PLAYER_ROW_Y - half),
                (cx + half, PLAYER_ROW_Y + half),
                (cx - half, PLAYER_ROW_Y + half),
            ],
        )
        return

    # Standing on the row rather than centred on it. Centred is where the
    # triangle was, but at 150px he then covered the controls hint. Collision
    # is grid space, so where he sits is only ever cosmetic.
    rect = sprite.get_rect(midbottom=(cx, PLAYER_ROW_Y))
    field.blit(sprite, rect)


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
    if game.state == WAVE_BREAK:
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


def _hr_readout(screen, hr) -> None:
    """Live heart rate under the hearts, so the effort input is visible on screen."""
    if hr is None:
        return
    line = _fonts["small"].render(f"{hr.current()} bpm", True, BAD)
    screen.blit(line, (26, 82))


def _score(screen, game) -> None:
    score = _fonts["med"].render(f"{game.score}", True, TEXT)
    screen.blit(score, (FIELD_W - score.get_width() - 26, 30))
    if game.combo > 1.0:
        combo = _fonts["small"].render(f"x{game.combo:.1f}", True, EMPOWERED)
        screen.blit(combo, (FIELD_W - combo.get_width() - 26, 76))


def _matchups(screen, top: int, size: int) -> None:
    """Three rows: the spell you cast, and the monster it kills.

    Read off game.BEATS, so it cannot teach a pairing the rules do not play.
    This replaced a strip of the cycle drawn as a chain, which stated the ring
    correctly but left you to work the pairing out yourself mid wave. A row
    just answers the question you actually have.
    """
    mid = FIELD_W // 2
    for i, spell in enumerate(CYCLE):
        y = top + i * MATCH_PITCH
        beaten = BEATS[spell]
        # The spell uses the bolt art, stored rotated head up, so the picture
        # here is the picture you see coming out of the wand.
        _emblem(screen, spell, COLORS[spell], mid - MATCH_GAP, y,
                _content_h(f"bolt_{spell}", size), art=f"bolt_{spell}")
        _arrow(screen, mid - 40, y, mid + 40, y)
        _emblem(screen, beaten, COLORS[beaten], mid + MATCH_GAP, y,
                _content_h(beaten, size))


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
