"""Timeline of the mix: edit the numbers here. All times are seconds from the start of the mix."""
import random

# Sections where the head nods on every 2nd beat (lighter mood). Everywhere else it nods on every beat.
LIGHT = [(0.0, 9 * 60 + 35.0), (37 * 60 + 14.0, 45 * 60 + 52.0)]

# ---- settings.json: everything the playground can tune (copy its "All settings" box into settings.json) ----
import json, os
DEFAULTS = {
    # head motion
    'depth': 15, 'windup': 0.35, 'fwd': 32, 'yaw': 8, 'roll': 3.5, 'sway': 1,
    'stiff': 2.6, 'damp': 0.42,                    # nod stiffness (Hz) and damping; yaw/roll springs scale along
    'energy': 0.0,                                 # 0 = every beat gets the same strength, 1 = quiet parts nod softer (down to 45 %)
    # surprise spiral eyes
    'sp_crazy': 0.15, 'sp_len': 30, 'sp_speed': 0.25, 'sp_flashes': 2, 'sp_phrase': 4, 'sp_zoom': 0.18,
    # text ring
    'rg_text': 'TILOS RADIO', 'rg_text2': 'ADAM DOLPH', 'rg_rep': 4, 'rg_rpm': 1.6, 'rg_r': 900,
    # stage
    'scale': 0.88,
}
SETTINGS_FILE = os.environ.get('TILOS_SETTINGS') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
SET = dict(DEFAULTS)
if os.path.exists(SETTINGS_FILE):
    _user = json.load(open(SETTINGS_FILE))
    SET.update({k: v for k, v in _user.items() if k in DEFAULTS})      # unknown keys (bpm, mode, prev ...) are playground-only
elif os.environ.get('TILOS_SETTINGS'):
    raise SystemExit('settings file not found: ' + SETTINGS_FILE)

# "Surprise" spiral eyes: fixed start times + number of extra random ones.
SPIRAL_LEN = float(SET['sp_len'])                  # seconds per surprise (includes the fade in/out)
SPIRAL_FIXED = [4 * 60 + 20.0, 20 * 60 + 9.0]
SPIRAL_RANDOM = 3
SPIRAL_CRAZY = float(SET['sp_crazy'])              # 0..1 overall intensity (flashes/direction changes only start above ~0.35)
SPIRAL_SPEED = float(SET['sp_speed'])              # base spin, revolutions per second
SPIRAL_ZOOM = float(SET['sp_zoom'])                # zoom pulses on the beat
SPIRAL_FLASHES = float(SET['sp_flashes'])          # max flashes per second
SPIRAL_PHRASE = int(SET['sp_phrase'])              # beats between direction/handedness changes


SEED = 20261119                                    # change it to get a different random schedule


def schedule(total):
    """returns (spiral_windows, ring_windows) for a mix of `total` seconds; deterministic for a given SEED."""
    rng = random.Random(SEED)
    starts = list(SPIRAL_FIXED)
    for _ in range(SPIRAL_RANDOM):                  # always drop the next one into the biggest empty stretch, at a random spot in its middle
        pts = sorted([0.0] + starts + [total - SPIRAL_LEN])
        lo, hi = max(zip((q - p for p, q in zip(pts, pts[1:])), pts, pts[1:]))[1:]
        starts.append(rng.uniform(lo + 0.35 * (hi - lo), lo + 0.65 * (hi - lo)))
    spiral = sorted((round(s, 1), round(s + SPIRAL_LEN, 1)) for s in starts if s + SPIRAL_LEN <= total)
    ring = []                                       # the text ring is always visible
    return spiral, ring


def _smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def ring_state(t, windows):
    """(alpha, scale) of the text ring: always visible."""
    return 1.0, 1.0


def fmt(t):
    return '%d:%02d' % (int(t) // 60, int(t) % 60)
