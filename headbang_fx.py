#!/usr/bin/env python3
"""Renders one time window of the video (headbang + rotating text ring + spiral eyes). make_video.py calls this once per chunk.

usage: python3 -I headbang_fx.py logo.svg events.json audio.mp3 out.mp4 --t0 S --dur S [--mix-offset S]
         [--grid auto|half|beat] [--fx1-clip A:B] [--no-ring] [--no-fx] [--no-audio] [--log f.csv]
Times: t0 is in the time base of events.json/audio. mix-offset is where that time base starts inside the full mix
(e.g. 600 for the excerpt that starts at 10:00), so the light/double sections and the spiral windows use mix time.
"""
import sys, os, math, json, argparse, subprocess, tempfile, hashlib
import numpy as np, cv2
from lxml import etree
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tilos_config as CFG

ap = argparse.ArgumentParser()
ap.add_argument('svg'); ap.add_argument('events'); ap.add_argument('audio'); ap.add_argument('out')
ap.add_argument('--t0', type=float, default=0.0); ap.add_argument('--dur', type=float, default=30.0)
ap.add_argument('--mix-offset', type=float, default=0.0)
ap.add_argument('--grid', choices=['auto', 'half', 'beat'], default='auto')
ap.add_argument('--total', type=float, default=3600.0, help='length of the whole mix in seconds (used for the random schedule)')
ap.add_argument('--spiral-windows', help='override, mix seconds: A:B,C:D'); ap.add_argument('--ring-windows', help='override, mix seconds: A:B,C:D')
ap.add_argument('--fx1-clip', help='force a spiral window, seconds relative to clip start, e.g. 8:18')
ap.add_argument('--no-ring', action='store_true'); ap.add_argument('--no-fx', action='store_true')
ap.add_argument('--energy', help='energy.json from energy.py (needed when settings energy > 0)'); ap.add_argument('--preview', action='store_true', help='540p fast encode')
ap.add_argument('--no-audio', action='store_true'); ap.add_argument('--log'); ap.add_argument('--dry', action='store_true')
args = ap.parse_args()

# ---------------------------------------------------------------- timeline of the real mix (seconds)
LIGHT = CFG.LIGHT
_wins = lambda txt: [tuple(float(v) for v in w.split(':')) for w in txt.split(',')]
_sp, _rg = CFG.schedule(args.total)
FX1 = _wins(args.spiral_windows) if args.spiral_windows else _sp
RING_WINS = []

# ---------------------------------------------------------------- stage / camera config
W, H, FPS = 1920, 1080, 30
VB = 513.0
SCALE = CFG.SET['scale']                                     # whole composition is scaled so the side blocks sit inside the ring
S = H / VB * SCALE
RS = int(round(VB * S))                          # px size of a rasterised logo layer
HEAD_C = (273.5, 184.5)                          # centre of the head in svg units -> centre of the frame
OX = int(round(W / 2 - HEAD_C[0] * S)); OY = int(round(H / 2 - HEAD_C[1] * S))
BG = (0, 0, 0)
PIVOT = np.array([270.0, 335.0, 0.0])
CAM_C = np.array([256.5, 200.0])
D = 650.0
Z = dict(cups=-25.0, face=0.0, hair=6.0, spiral=2.0, mouth=10.0)
HAIR_LAG = 0.25
PREROLL = 2.5
LENS = {'L': (218.2, 160.0, 30.2), 'R': (321.0, 159.2, 31.0)}              # cx, cy, radius in svg units (fitted)
RING_TEXT_1 = CFG.SET['rg_text']                          # the ring carries two texts
RING_TEXT_2 = CFG.SET['rg_text2']
RING_REPEAT = int(CFG.SET['rg_rep'])                                  # how many times the pair (text 1 + text 2) goes around the ring
RING_R = float(CFG.SET['rg_r'])                                   # px: baseline radius of the ring text
RING_RPM = float(CFG.SET['rg_rpm'])                                   # slow, easily readable (40 s per revolution)
INTER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'Inter-SemiBold.otf')   # bundled (SIL Open Font License)

# ---------------------------------------------------------------- svg -> layers
parser = etree.XMLParser(resolve_entities=False, no_network=True)
root = etree.parse(args.svg, parser).getroot()
NS = '{http://www.w3.org/2000/svg}'
paths = {p.get('id'): (p.get('d'), p.get('style')) for p in root.iter(NS + 'path')}

def render(ids):
    body = ''.join(f'<path d="{paths[i][0]}" style="{paths[i][1]}"/>' for i in ids)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{VB:.0f}" height="{VB:.0f}" viewBox="0 0 {VB:.0f} {VB:.0f}">{body}</svg>'
    with tempfile.TemporaryDirectory() as td:
        sp, pp = os.path.join(td, 'l.svg'), os.path.join(td, 'l.png')
        open(sp, 'w').write(svg)
        subprocess.run(['rsvg-convert', '-w', str(RS), '-h', str(RS), sp, '-o', pp], check=True)
        return np.array(Image.open(pp).convert('RGBA'))

def premult_bgra(rgba):
    c = rgba.astype(np.float32); c[..., :3] *= c[..., 3:4] / 255.0
    return c.astype(np.uint8)[..., [2, 1, 0, 3]]

class Card:
    """A flat picture placed in the head's 3D frame. Corners in svg units, image BGRA premultiplied."""
    def __init__(self, name, img, u0, v0, u1, v1, z, group):
        self.name, self.img, self.z, self.group = name, img, z, group
        self.u0, self.v0, self.u1, self.v1 = u0, v0, u1, v1
        self.h, self.w = img.shape[:2]

def layer_card(name, rgba, z, group):
    ys, xs = np.where(rgba[..., 3] > 0); m = 6
    x0, x1 = max(xs.min() - m, 0), min(xs.max() + m + 1, RS)
    y0, y1 = max(ys.min() - m, 0), min(ys.max() + m + 1, RS)
    return Card(name, premult_bgra(rgba[y0:y1, x0:x1]), x0 / S, y0 / S, x1 / S, y1 / S, z, group)

def blend(dst, x0, y0, bgra):
    """dst BGR uint8 (modified), bgra premultiplied, top-left at (x0,y0), clipped to dst."""
    h, w = bgra.shape[:2]
    X0, Y0, X1, Y1 = max(x0, 0), max(y0, 0), min(x0 + w, dst.shape[1]), min(y0 + h, dst.shape[0])
    if X1 <= X0 or Y1 <= Y0: return
    s = bgra[Y0 - y0:Y1 - y0, X0 - x0:X1 - x0]
    a = s[..., 3:4].astype(np.uint16)
    roi = dst[Y0:Y1, X0:X1].astype(np.uint16)
    dst[Y0:Y1, X0:X1] = (roi * (255 - a) // 255 + s[..., :3]).clip(0, 255).astype(np.uint8)

# ---- ring text texture (clockwise reading, letter tops outward) ----
def make_ring():
    SS = 2; size = 2 * (int(RING_R) + 130)   # canvas must hold the whole ring
    text = (RING_TEXT_1 + ' • ' + RING_TEXT_2 + ' • ') * RING_REPEAT
    ref = ImageFont.truetype(INTER, 100)
    k = sum(ref.getlength(c) for c in text) / 100.0
    n = len(text)
    fs = (2 * math.pi * RING_R * SS) / (k + 0.26 * n)                 # font size (at SS) that fills the circle with 0.26em tracking
    font = ImageFont.truetype(INTER, int(round(fs)))
    adv = [font.getlength(c) for c in text]
    track = (2 * math.pi * RING_R * SS - sum(adv)) / n
    canvas = Image.new('L', (size * SS, size * SS), 0)
    c0 = size * SS / 2.0; T = int(fs * 2.4)
    cum = 0.0
    for ch, a in zip(text, adv):
        mid = cum + a / 2.0; cum += a + track
        if ch == ' ': continue
        phi = mid / (RING_R * SS)
        tile = Image.new('L', (T, T), 0)
        ImageDraw.Draw(tile).text((T / 2, T / 2), ch, font=font, fill=255, anchor='ms')
        tile = tile.rotate(-math.degrees(phi), resample=Image.BICUBIC, center=(T / 2, T / 2))
        px = c0 + RING_R * SS * math.sin(phi) - T / 2; py = c0 - RING_R * SS * math.cos(phi) - T / 2
        canvas.paste(255, (int(round(px)), int(round(py))), tile)
    alpha = np.array(canvas.resize((size, size), Image.LANCZOS))
    print('ring: font %.0f px, tracking %.2f em, %d chars' % (fs / SS, track / fs, n))
    return alpha

def build_stage():
    """frames[k] = flat background + ring rotated by k steps + the two side blocks."""
    panels = np.zeros((H, W, 4), np.uint8)
    p = premult_bgra(render(['g', 'h']))
    y0, x0 = OY, OX
    sy0, sx0 = max(-y0, 0), max(-x0, 0)
    ey, ex = min(H - y0, RS), min(W - x0, RS)
    panels[y0 + sy0:y0 + ey, x0 + sx0:x0 + ex] = p[sy0:ey, sx0:ex]
    ys, xs = np.where(panels[..., 3] > 0)
    pb = panels[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    base = np.zeros((H, W, 3), np.uint8); base[:] = BG[::-1]
    if args.no_ring:
        f = base.copy(); blend(f, xs.min(), ys.min(), pb); return lambda t: f
    alpha = make_ring(); size = alpha.shape[0]
    still = base.copy(); blend(still, xs.min(), ys.min(), pb)
    def frame_at(tmix):
        ra, rs = CFG.ring_state(tmix, RING_WINS)
        if ra <= 0.002 or rs <= 0.02: return still.copy()
        ang = -(tmix * 360.0 * RING_RPM / 60.0) % 360.0                    # negative = clockwise on screen
        M = cv2.getRotationMatrix2D((size / 2, size / 2), ang, rs)
        rot = cv2.warpAffine(alpha, M, (size, size), flags=cv2.INTER_LINEAR)
        if ra < 0.999: rot = (rot * ra).astype(np.uint8)
        rgba = np.dstack([rot, rot, rot, rot])                             # white text, premultiplied
        f = base.copy(); blend(f, W // 2 - size // 2, H // 2 - size // 2, rgba); blend(f, xs.min(), ys.min(), pb)
        return f
    return frame_at

stage_at = build_stage()

# ---- head layers
cards = [layer_card('phone_L', render(['j']), Z['cups'], 'cups'), layer_card('phone_R', render(['i']), Z['cups'], 'cups')]
k = render(['k']); lab, _ = ndi.label(k[..., 3] > 127)
sizes = np.bincount(lab.ravel()); sizes[0] = 0
hair_lab = next(c for c in np.argsort(-sizes)[:5] if sizes[c] > 3000 and np.where(lab == c)[0].min() / S < 100)
hair_mask = ndi.binary_dilation(lab == hair_lab, iterations=3)
hair = k.copy(); hair[..., 3] = np.where(hair_mask, k[..., 3], 0)
face = k.copy(); face[..., 3] = np.where(~hair_mask, k[..., 3], 0)
cards += [layer_card('face', face, Z['face'], 'head'), layer_card('hair', hair, Z['hair'], 'hair')]
mouth_card = layer_card('mouth', render(['m', 'n']), Z['mouth'], 'head')

# ---------------------------------------------------------------- springs (the approved motion)
def unit_peak(w, z):
    wd = w * math.sqrt(1 - z * z); t = math.atan2(wd, z * w) / wd
    return (1 / wd) * math.exp(-z * w * t) * math.sin(wd * t), t

class Spring:
    def __init__(self, hz, zeta, peak):
        self.w, self.z = 2 * math.pi * hz, zeta
        pk, self.t_peak = unit_peak(self.w, zeta); self.gain = peak / pk; self.x = self.v = 0.0
    def kick(self, amt): self.v += self.gain * amt
    def step(self, dt, target=0.0):
        a = -self.w ** 2 * (self.x - target) - 2 * self.z * self.w * self.v
        self.v += a * dt; self.x += self.v * dt

MS = CFG.SET
f_k = MS['stiff'] / 2.6; z_k = MS['damp'] / 0.42                                   # stiffness / damping relative to the approved motion
zt = lambda z: min(0.95, z * z_k)
pitch = Spring(MS['stiff'], zt(0.42), math.radians(MS['depth'])); yaw = Spring(1.5 * f_k, zt(0.35), math.radians(MS['yaw']))
roll = Spring(2.0 * f_k, zt(0.40), math.radians(MS['roll'])); pitch_hair = Spring(1.9 * f_k, zt(0.50), 1.0)
WIND, WIND_DT = float(MS['windup']), 0.20
KZ_F, KZ_B = float(MS['fwd']), float(MS['fwd']) * 22.0 / 32.0
P_REF = math.radians(MS['depth']) * 13.0 / 15.0
SWAY = float(MS['sway'])

def calibrate():
    sp = Spring(MS['stiff'], zt(0.42), math.radians(MS['depth'])); dt = 1.0 / FPS / 16
    sp.kick(-WIND); t = 0.0; kicked = False; best = (-1e9, 0.0)
    while t < 1.6:
        if not kicked and t >= WIND_DT: sp.kick(1.0); kicked = True
        sp.step(dt); t += dt
        if kicked and sp.x > best[0]: best = (sp.x, t)
    return best[1] - WIND_DT, best[0]
LEAD, PEAK_W = calibrate()
PK_SCALE = math.radians(MS['depth']) / PEAK_W if PEAK_W > math.radians(MS['depth']) else 1.0

# ---------------------------------------------------------------- choreography: light / double sections by mix time
beats = sorted(json.load(open(args.events))['beats'], key=lambda b: b['t'])
def is_light(tmix): return any(a <= tmix < b for a, b in LIGHT)
EN = None
if MS['energy'] > 0:
    if not args.energy: sys.exit('settings energy > 0 needs --energy energy.json (make_video.py does this automatically)')
    _e = json.load(open(args.energy)); EN = (np.array(_e['e']), float(_e['hop']))
def en_factor(t):                                  # 1.0 for loud parts, down to 0.45 at full "energy follow" for quiet ones
    if EN is None: return 1.0
    i = min(max(int(t / EN[1]), 0), len(EN[0]) - 1)
    return 1.0 - MS['energy'] * 0.55 * (1.0 - float(EN[0][i]))
events = []; sign = 1.0
for b in beats:
    a_ = b['a']; tm = b['t'] + args.mix_offset; ef = en_factor(b['t'])
    mode = args.grid if args.grid != 'auto' else ('half' if is_light(tm) else 'beat')
    if mode == 'half':
        if b['acc']:
            amp = (0.25 + 0.75 * a_) * PK_SCALE * ef; sign = -sign
            events.append((b['t'] - LEAD - WIND_DT, -WIND * amp, 0.0, 0.0))
            events.append((b['t'] - LEAD, amp, sign * (0.55 + 0.25 * amp), sign * 0.5 * amp))
        else:
            events.append((b['t'] - LEAD, 0.28 * (0.3 + 0.7 * a_) * ef, 0.0, 0.0))
    else:
        amp = (0.25 + 0.75 * a_) * (1.0 if b['acc'] else 0.7) * PK_SCALE * ef; yk = rk = 0.0
        if b['acc']:
            sign = -sign; yk = sign * (0.55 + 0.25 * amp); rk = sign * 0.5 * amp
        events.append((b['t'] - LEAD - WIND_DT, -WIND * amp, 0.0, 0.0)); events.append((b['t'] - LEAD, amp, yk, rk))
events.sort()

# ---------------------------------------------------------------- spiral-eye controls (pre-computed, deterministic in mix time)
CTL_HZ = 120
def smooth(x): x = np.clip(x, 0, 1); return x * x * (3 - 2 * x)
def rnd(*key):
    h = hashlib.md5(('|'.join(map(str, key))).encode()).digest()
    return int.from_bytes(h[:4], 'little') / 2 ** 32

def build_fx_controls(windows, t_hi):
    n = int(t_hi * CTL_HZ) + 2
    t = np.arange(n) / CTL_HZ
    alpha = np.zeros(n); crazy = np.zeros(n)
    for w0, w1 in windows:
        Lw = w1 - w0; fade = min(6.0, 0.2 * Lw)
        m = (t >= w0) & (t <= w1); tt = t[m]
        alpha[m] = smooth((tt - w0) / fade) * smooth((w1 - tt) / fade)
        u = (tt - w0) / Lw
        crazy[m] = np.clip(u / 0.30, 0, 1) ** 1.3 * (1 - smooth((u - 0.88) / 0.12))
    crazy *= CFG.SPIRAL_CRAZY
    bt = np.array([b['t'] + args.mix_offset for b in beats]); acc = np.array([b['acc'] for b in beats])
    bidx = np.searchsorted(bt, t, side='right') - 1                      # last beat at or before each sample
    plen = CFG.SPIRAL_PHRASE
    phrase = np.where(bidx >= 0, bidx // plen, -1)
    c_at = lambda tt_: crazy[min(int(tt_ * CTL_HZ), n - 1)]
    # direction / eye-mode per 16-beat phrase (only becomes wild once the effect is hot)
    dirn = np.ones(n); hR = -np.ones(n)
    flash = np.zeros(n); fcol = np.ones((n, 3)); last_flash = -9.0
    kick_env = np.zeros(n); kick_mult = {}
    def add_flash(tf, dur, col):
        nonlocal last_flash
        if tf - last_flash < max(0.45, 1.0 / max(CFG.SPIRAL_FLASHES, 0.01)) or CFG.SPIRAL_FLASHES <= 0: return
        i0, i1 = int(tf * CTL_HZ), min(int((tf + dur) * CTL_HZ) + 1, n)
        flash[i0:i1] = 1.0; fcol[i0:i1] = col; last_flash = tf
    PAL = [(1, 1, 1)]                                                        # flashes are plain black/white inversions, no colour
    pdir = {}; pmode = {}
    for k_, tb in enumerate(bt):
        if tb >= t_hi or alpha[min(int(tb * CTL_HZ), n - 1)] <= 0: continue
        c = c_at(tb); ph = k_ // plen
        mood = rnd('mood', ph)                                              # >0.72 frenzy burst, 0.30-0.72 normal, below calm
        if ph not in pdir:
            pdir[ph] = -1.0 if (c > 0.5 and rnd('dir', ph) < 0.30) else 1.0
            pmode[ph] = -1.0 if (c > 0.5 and rnd('mode', ph) < 0.35) else 1.0   # -1: right eye keeps the same handedness (not a mirror)
            if k_ % plen == 0 and k_ > 0 and pmode.get(ph - 1, 1.0) != pmode[ph]:
                add_flash(tb, 0.14, PAL[int(rnd('c', ph) * len(PAL))])       # hide the hard switch behind a flash
        frenzy = mood > 0.72
        fp = 0.45 if frenzy else (0.05 if mood > 0.30 else 0.0)
        if c > 0.35 and ((acc[k_] and rnd('flash', k_) < fp) or (frenzy and not acc[k_] and rnd('flash2', k_) < 0.12)):
            add_flash(tb, 0.10 if rnd('len', k_) < 0.7 else 0.45, PAL[int(rnd('col', k_) * len(PAL))])
        i0 = int(tb * CTL_HZ)
        kick_mult[i0] = 1.0 + (0.6 if frenzy else 0.0)
        kern = np.exp(-np.arange(0, int(0.8 * CTL_HZ)) / CTL_HZ / 0.16)
        i1 = min(i0 + len(kern), n)
        kick_env[i0:i1] += c * kick_mult[i0] * kern[:i1 - i0]
    for ph, d_ in pdir.items():
        sel = phrase == ph; dirn[sel] = d_; hR[sel] = -1.0 if pmode[ph] > 0 else 1.0
    # angular integration (turns). base speed rises with the craziness; every beat kicks the spin
    ang = np.zeros(n); w_s = 0.0; w_k = 0.0; a_ = 0.0; dt = 1.0 / CTL_HZ
    for i in range(n):
        if alpha[i] <= 0 and a_ == 0.0 and w_s == 0.0:
            continue
        target = dirn[i] * (CFG.SPIRAL_SPEED + 0.60 * crazy[i])
        w_s += (target - w_s) * dt / 0.5
        w_k *= math.exp(-dt / 0.25)
        if i in kick_mult and alpha[i] > 0: w_k += 0.9 * kick_mult[i] * crazy[i] * dirn[i]
        a_ += (w_s + w_k) * dt; ang[i] = a_
    zoom = 1.0 + 0.10 * (CFG.SPIRAL_ZOOM / 0.18) * kick_env + 0.05 * crazy * np.sin(2 * np.pi * t / 9.0)
    return dict(alpha=alpha, crazy=crazy, ang=ang, zoom=zoom, flash=flash, fcol=fcol, hR=hR)

if args.no_fx:
    FX = None
else:
    wins = FX1
    if not wins: wins = [(-10.0, -9.0)]
    if args.fx1_clip:
        a, b = (float(v) for v in args.fx1_clip.split(':'))
        wins = [(args.mix_offset + args.t0 + a, args.mix_offset + args.t0 + b)]
    FX = build_fx_controls(wins, args.mix_offset + args.t0 + args.dur + 1)

# ---- spiral eye cards (procedural; fitted to the supplied spiral: pitch = R/3.19, arms as wide as the gaps)
PHASE0 = 0.65
class Eye:
    def __init__(self, key):
        cx, cy, R = LENS[key]; self.key, self.cx, self.cy, self.R = key, cx, cy, R
        SS = 2.0; m = 2.0
        self.u0, self.v0, self.u1, self.v1 = cx - R - m, cy - R - m, cx + R + m, cy + R + m
        n = int(round((2 * R + 2 * m) * S * SS)); self.n = n
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        dx, dy = xx - (n - 1) / 2, yy - (n - 1) / 2
        self.r = np.hypot(dx, dy); self.th = np.arctan2(dy, dx) / (2 * np.pi)
        self.Rpx = R * S * SS
        self.mask = np.clip(self.Rpx - self.r + 0.5, 0, 1).astype(np.float32)
        self.p0 = self.Rpx / 3.19
    def card(self, ang, hand, zoom, a_fade, inv, icol):
        p = self.p0 * zoom
        f = (self.r / p + hand * self.th + ang + PHASE0) % 1.0
        sd = np.where(f < 0.5, np.minimum(f, 0.5 - f), -np.minimum(f - 0.5, 1.0 - f))
        w = np.hypot(1.0 / p, 1.0 / (2 * np.pi * np.maximum(self.r, 1.0)))
        cov = np.clip(sd / w + 0.5, 0, 1)
        alpha = self.mask * ((1 - inv) * cov + inv) * a_fade
        ink = np.array([12.0, 12.0, 12.0], np.float32)                       # near-black arms
        rgb_n = np.broadcast_to(ink, cov.shape + (3,))
        rgb_i = (1 - cov)[..., None] * ink + cov[..., None] * (np.array(icol, np.float32)[None, None, :] * 255.0)
        rgb = (1 - inv) * rgb_n + inv * rgb_i
        img = np.dstack([rgb[..., 2] * alpha, rgb[..., 1] * alpha, rgb[..., 0] * alpha, alpha * 255.0])
        return Card('eye' + self.key, img.clip(0, 255).astype(np.uint8), self.u0, self.v0, self.u1, self.v1, Z['spiral'], 'head')
eyes = {'L': Eye('L'), 'R': Eye('R')}
if FX is not None:
    fl = FX['flash']; edges = [i for i in range(1, len(fl)) if fl[i] > 0 and fl[i - 1] == 0]
    lo_i, hi_i = int((args.t0 + args.mix_offset) * CTL_HZ), int((args.t0 + args.mix_offset + args.dur) * CTL_HZ)
    inwin = [round(i / CTL_HZ - args.mix_offset - args.t0, 1) for i in edges if lo_i <= i <= hi_i]
    print('flashes in this clip (clip seconds):', inwin)
    if args.dry: sys.exit(0)

# ---------------------------------------------------------------- projection / compositing
def rot_project(pts, p, yw, rl, dy_units, dz=0.0):
    q = pts - PIVOT; x, y, z = q[:, 0], q[:, 1], q[:, 2]
    x1 = x * math.cos(yw) + z * math.sin(yw); z1 = -x * math.sin(yw) + z * math.cos(yw)
    y2 = y * math.cos(p) + z1 * math.sin(p); z2 = -y * math.sin(p) + z1 * math.cos(p)
    x3 = x1 * math.cos(rl) - y2 * math.sin(rl); y3 = x1 * math.sin(rl) + y2 * math.cos(rl)
    X = x3 + PIVOT[0]; Y = y3 + PIVOT[1] + dy_units; Zc = z2 + PIVOT[2] + dz
    s = D / (D - Zc)
    X = CAM_C[0] + (X - CAM_C[0]) * s; Y = CAM_C[1] + (Y - CAM_C[1]) * s
    return np.stack([X * S + OX, Y * S + OY], 1)

def blit(frame, c, p, yw, rl, dy_units, dz=0.0):
    corners = np.array([[c.u0, c.v0, c.z], [c.u1, c.v0, c.z], [c.u1, c.v1, c.z], [c.u0, c.v1, c.z]])
    dst = rot_project(corners, p, yw, rl, dy_units, dz).astype(np.float32)
    src = np.array([[0, 0], [c.w, 0], [c.w, c.h], [0, c.h]], np.float32)
    bx0, by0 = int(max(dst[:, 0].min() - 2, 0)), int(max(dst[:, 1].min() - 2, 0))
    bx1, by1 = int(min(dst[:, 0].max() + 3, W)), int(min(dst[:, 1].max() + 3, H))
    if bx1 <= bx0 or by1 <= by0: return
    Hm = cv2.getPerspectiveTransform(src, dst - np.array([bx0, by0], np.float32))
    wp = cv2.warpPerspective(c.img, Hm, (bx1 - bx0, by1 - by0), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    a = wp[..., 3:4].astype(np.uint16)
    roi = frame[by0:by1, bx0:bx1].astype(np.uint16)
    frame[by0:by1, bx0:bx1] = (roi * (255 - a) // 255 + wp[..., :3]).clip(0, 255).astype(np.uint8)

# ---------------------------------------------------------------- render
nframes = int(round(args.dur * FPS))
OW, OH = (960, 540) if args.preview else (W, H)
cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{OW}x{OH}', '-r', str(FPS), '-i', '-']
if not args.no_audio:
    cmd += ['-ss', f'{args.t0:.3f}', '-t', f'{args.dur:.3f}', '-i', args.audio, '-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '256k', '-shortest']
cmd += ['-c:v', 'libx264', '-preset', 'veryfast' if args.preview else 'medium', '-crf', '24' if args.preview else '18', '-tune', 'animation', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', args.out]
ff = subprocess.Popen(cmd, stdin=subprocess.PIPE)

SUB = 16; dt_sub = 1.0 / FPS / SUB
t_sim = args.t0 - PREROLL; ei = 0
while ei < len(events) and events[ei][0] < t_sim: ei += 1
def advance(until):
    global t_sim, ei
    while t_sim < until - 1e-9:
        while ei < len(events) and events[ei][0] <= t_sim:
            _, pa, ya, ra = events[ei]; pitch.kick(pa); yaw.kick(ya); roll.kick(ra); ei += 1
        pitch.step(dt_sub); yaw.step(dt_sub); roll.step(dt_sub); pitch_hair.step(dt_sub, pitch.x); t_sim += dt_sub

log = []; fx_frames = 0
for f in range(nframes):
    tabs = args.t0 + f / FPS; tmix = tabs + args.mix_offset
    advance(tabs)
    P = 0.30 * math.tanh(pitch.x / 0.30) + SWAY * math.radians(1.2) * math.sin(2 * math.pi * tabs / 1.818)
    Y = yaw.x + SWAY * math.radians(2.0) * math.sin(2 * math.pi * tabs / 6.5)
    R = roll.x + 0.35 * yaw.x + SWAY * math.radians(0.6) * math.sin(2 * math.pi * tabs / 5.0)
    dy = 0.60 * math.degrees(max(P, 0)) + 0.25 * math.degrees(min(P, 0))
    dz = KZ_F * (P / P_REF) if P > 0 else KZ_B * (P / P_REF)
    hair_dp = HAIR_LAG * (pitch_hair.x - pitch.x)
    frame = stage_at(tmix)
    for c in cards[:2]: blit(frame, c, P, Y, R, dy, dz)
    for c in cards[2:]: blit(frame, c, P + hair_dp if c.group == 'hair' else P, Y, R, dy, dz)
    if FX is not None:
        i = min(int(round(tmix * CTL_HZ)), len(FX['alpha']) - 1)
        if FX['alpha'][i] > 0.003:
            fx_frames += 1
            for key, c_hand, sgn in (('L', 1.0, 1.0), ('R', FX['hR'][i], -1.0)):
                ec = eyes[key].card(sgn * FX['ang'][i], c_hand, FX['zoom'][i], FX['alpha'][i], FX['flash'][i], FX['fcol'][i])
                blit(frame, ec, P, Y, R, dy, dz)
    blit(frame, mouth_card, P, Y, R, dy, dz)
    if args.preview: frame = cv2.resize(frame, (OW, OH), interpolation=cv2.INTER_AREA)
    ff.stdin.write(frame.tobytes())
    log.append((tabs, math.degrees(P), math.degrees(Y), dy))
    if f % 150 == 0: print('frame', f, '/', nframes, flush=True)
ff.stdin.close(); ff.wait()
if args.log: np.savetxt(args.log, np.array(log), delimiter=',', header='t,pitch_deg,yaw_deg,dy_units', comments='')
print('done', args.out, '| frames with spiral eyes:', fx_frames, '| max pitch %.1f max dy %.1f' % (max(l[1] for l in log), max(l[3] for l in log)))
