# config.py — every number the game reads, and what moving it does.

# --- window -------------------------------------------------------------------
# ponytail: one fixed logical size, scaled to the display by SDL (main.py asks
# for pygame.SCALED), so fullscreen costs a flag and every coordinate here stays
# a plain number. 2048x1280 is this panel, so it renders 1:1 and letterboxes
# elsewhere. If a bigger display looks soft, derive these from the screen size.

FIELD_W = 608                     # game column. Smaller = more room for camera
CAM_W = 1440                      # preview box; keep CAM_W:CAM_H at 4:3
CAM_H = 1080                      # taller = thinner text strip under it
WINDOW_W = FIELD_W + CAM_W        # 2048. With --no-camera the window is FIELD_W
WINDOW_H = 1280                   # logical height; SDL scales it to the screen
FPS = 60                          # frame cap only; dt is real elapsed time

# --- layout -------------------------------------------------------------------

LANES = 3                         # not a free knob: draw and camera assume it

# Lanes sit on the walkable floor of element/Background.png, not across the
# whole window: the stone border either side is wall, and a monster centred on
# a plain third of FIELD_W stands half on the masonry. Measured off the scaled
# image -- the floor's edge highlight is at x=79 and x=529, the wall's shadow
# line just inside it at 62 and 550. Re-measure these two if the art changes.
PATH_LEFT = 62                    # first walkable pixel
PATH_RIGHT = 550                  # last walkable pixel
PATH_W = PATH_RIGHT - PATH_LEFT   # 446, derived
LANE_W = PATH_W // LANES          # 148, derived. Was FIELD_W // LANES = 202
FIELD_TOP = 80                    # pixel y for grid y = 0.0, where threats spawn
FIELD_BOTTOM = 1120               # pixel y for grid y = 1.0, where they hit you
PLAYER_ROW_Y = FIELD_BOTTOM       # derived: hits land where the player is drawn

# --- player -------------------------------------------------------------------

PLAYER_HP = 3                     # hearts. Raise for a more forgiving run
IFRAME_MS = 800                   # invulnerable after a hit, so one row = one heart

# --- casting ------------------------------------------------------------------

CAST_COOLDOWN_MS = 400            # min gap between casts. Lower = shoot faster
MISFIRE_LOCKOUT_MS = 500          # frozen out after a reject. Only a wand misfires
BOLT_TRAVEL_S = 1              # bolt crosses the field this fast. Raise = slower

# --- camera control (inputs.CameraSource) -------------------------------------

CAM_ID = 1                        # webcam index; --camera N overrides
CAM_CONFIDENCE = 0.6              # MediaPipe floor. Lower tracks in worse light
CAM_DEADZONE_PX = 90              # step this far past a lane edge to switch lane.
                                  # Raise if you flicker, lower if sluggish.
                                  # Preview pixels, so it moves with CAM_W
CAM_GRACE_S = 0.5                 # hold the last good reading through a blip

# --- wand control (inputs.WandSource) -----------------------------------------
# Firmware: drawing_wand_mpu6050.ino. Streams "P,<x>,<y>,<pen>" at 100 Hz, x and
# y in degrees, and takes two commands: "z" zeroes the position, "b" re-measures
# the gyro bias. It has no LED or buzzer command, so WandSource.send_feedback
# has nothing to drive.
#
# It sends those P lines over UDP only -- serial carries the startup banners and
# nothing else -- so the wand is unusable on a COM port with this firmware, and
# WAND_WIFI is the default. Join this PC to the wand's own access point first:
# SSID MagicWand, password wand1234. That adapter has no internet while joined.
# The address and UDP port live in wifi.py.

WAND_WIFI = "wifi"                # the sentinel WAND_PORT takes to mean UDP
WAND_PORT = WAND_WIFI             # or a COM port; --wand COM5 overrides
WAND_BAUD = 115200                # must match Serial.begin in the firmware
WAND_MIN_PTS = 15                 # 100 Hz, so a 150ms floor. Below this = a twitch
WAND_REJECT = 60.0                # score above this misfires. Templates peak at 24
WAND_PX_PER_DEG = 4.0             # THE speed knob: panel pixels per degree of
                                  # wrist rotation. Lower = the drawing moves
                                  # less for the same motion. 4.0 keeps a
                                  # typical 28 degree stroke inside the panel
WAND_TEMPLATES = "strokes_*.json" # written by wand/record.py, at the repo root
WAND_WARMUP_S = 6.0               # silence past this is a bad port, not calibration
WAND_CALIBRATE_S = 6.0            # wait for "# ready" after the reboot. Measured
                                  # 2.6s: 0.5s boot + 1.5s of gyro averaging.
                                  # Set to 0 to skip the reset and reboot yourself

# --- waves --------------------------------------------------------------------
# One row per wave, read straight down the column you care about:
#
#   fall           seconds a threat takes to cross the field. THE speed knob
#   gap            seconds between rows
#   rows           how many rows the wave spawns. Wave *length*, not speed
#   monster_lanes  how many of the three lanes hold a monster. The rest are
#                  wall, so this is how much of the row you may stand in
#   shapes         the MONSTER shapes this wave sends. NOT what you draw: you
#                  kill a monster with the spell that beats it, so a wave of
#                  squares is a wave of triangles to draw. See game.BEATS,
#                  where circle is water, triangle is fire, square is earth:
#                      square monster   (earth) -> draw triangle (fire)
#                      triangle monster (fire)  -> draw circle   (water)
#                      circle monster   (water) -> draw square   (earth)
#
# Lower fall and gap = harder. Editing a row changes that wave and no other,
# which is the whole point of a table: what you see is what wave 4 plays like,
# with no arithmetic in the way.

WAVES = [
    # fall  gap  rows  monster_lanes  shapes            (you draw)
    {"fall": 8.0, "gap": 3.5, "rows": 6, "monster_lanes": 2,
     "shapes": ["square"]},                             # triangle
    {"fall": 6.5, "gap": 2.8, "rows": 9, "monster_lanes": 2,
     "shapes": ["square", "triangle"]},                 # triangle, circle
    {"fall": 5.5, "gap": 2.2, "rows": 12, "monster_lanes": 1,
     "shapes": ["square", "triangle"]},                 # triangle, circle
    {"fall": 4.5, "gap": 1.7, "rows": 16, "monster_lanes": 1,
     "shapes": ["square", "triangle", "circle"]},       # all three
    {"fall": 3.5, "gap": 1.3, "rows": 22, "monster_lanes": 1,
     "shapes": ["square", "triangle", "circle"]},       # all three
    {"fall": 2.8, "gap": 0.95, "rows": 30, "monster_lanes": 1,
     "shapes": ["square", "triangle", "circle"]},       # all three
]
WAVE_ENDLESS = 0.85               # past the table, each wave scales fall and
                                  # gap by this. Toward 1.0 = a longer tail
WAVE_FLOOR_S = 1.2                # ...but fall never below this. The hard
                                  # ceiling on difficulty, however long you last
WAVE_GAP_FLOOR_S = 0.4            # and gap never below this. Separate from the
                                  # fall floor because gap is now tuned under
                                  # 1.2s: sharing one floor made wave 7 spawn
                                  # SLOWER than wave 6, an easier wave 7.
                                  # Below game.CROWDED_Y * fall a row cannot
                                  # start anyway, so 0.4 is about as low as is
                                  # worth setting
WAVE_BREAK_S = 10               # rest between waves. You can still move

# --- combat -------------------------------------------------------------------

EMPOWER_SPEED_MULT = 1.25         # speed-up when a losing spell hits. Once, no stack

# --- scoring ------------------------------------------------------------------

SCORE_KILL = 100                  # base points, before the combo multiplier
SCORE_EARLY_KILL = 150            # more for killing high up, to reward casting early
EARLY_KILL_Y = 0.33               # grid y under which a kill counts as early
SCORE_WALL_DODGE = 10             # small on purpose: dodging is not the scoring loop
COMBO_STEP = 0.5                  # multiplier gained per consecutive kill
COMBO_CAP = 4.0                   # ceiling. Resets on misfire, damage, escape, empower

# --- feel ---------------------------------------------------------------------

SHAKE_MS = 150                    # screen shake after a block or a hit
SHAKE_PX = 6                      # how far the field jumps while shaking

# --- palette (dark theme, to sit on element/Background.png's dungeon stone) ---

COLORS = {                        # one colour per spell, shared by monster,
    "triangle": (232,  76,  76),  # bolt, sigil and legend so they can never drift
    "circle":   ( 74, 144, 226),
    "square":   ( 92, 184, 108),
}
BG        = ( 18,  18,  24)       # behind everything, and what _fade blends to
GRID      = ( 62,  62,  78)       # column edge and the FIELD_TOP rule
PLAYER    = (240, 240, 245)       # the fallback triangle, when the art is missing
WALL      = (150, 150, 168)       # walls, and the dimmer HUD text
EMPOWERED = (255, 200,  60)       # glow outline on a sped-up monster
TEXT      = (232, 232, 240)       # score, wave banner, lane readout
VEIL      = ( 12,  12,  18, 205)  # wave break and game over wash; alpha keeps
                                  # the field readable underneath
