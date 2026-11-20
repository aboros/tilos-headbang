#!/usr/bin/env python3
"""Detect kick/snare-like hits in a mono WAV. Output: JSON events + optional diagnostic plot.

usage: python3 -I detect_hits.py in.wav out.json [--plot plot.png --t0 10 --t1 24] [--thr 1.0] [--min-gap 0.07]
"""
import sys, json, argparse
import numpy as np
from scipy.io import wavfile
from scipy import signal, ndimage

ap = argparse.ArgumentParser()
ap.add_argument('wav'); ap.add_argument('out')
ap.add_argument('--plot'); ap.add_argument('--t0', type=float, default=0); ap.add_argument('--t1', type=float, default=14)
ap.add_argument('--thr', type=float, default=1.0)          # peak threshold on normalised onset strength
ap.add_argument('--min-gap', type=float, default=0.07)     # seconds
ap.add_argument('--offset', type=float, default=0.0)       # timing correction in seconds (added to detected times)
ap.add_argument('--bpm', type=float, default=125.0)        # tempo prior for the beat grid (mix is ~124-126)
ap.add_argument('--debug-accent', action='store_true')
ap.add_argument('--lam', type=float, default=150.0)        # beat-grid stiffness (higher = steadier grid)
a = ap.parse_args()

N, HOP = 1024, 256
sr, x = wavfile.read(a.wav)
x = x.astype(np.float32) / 32768.0
f, t, Z = signal.stft(x, fs=sr, nperseg=N, noverlap=N - HOP, window='hann', boundary=None, padded=False)
mag = np.abs(Z)
fps = sr / HOP
L = np.log1p(100.0 * mag / np.percentile(mag, 99.9))
flux = np.maximum(np.diff(L, axis=1, prepend=L[:, :1]), 0)          # half-wave rectified spectral flux
tf = t                                                                 # frame centre times (s)

def band(lo, hi):
    m = (f >= lo) & (f < hi)
    return flux[m].sum(0) / max(m.sum(), 1)

bands = {'low': band(30, 150), 'mid': band(150, 600), 'high': band(1200, 6000)}

def local_norm(e, sigma_s=4.0):
    scale = ndimage.gaussian_filter1d(e, sigma_s * fps, mode='nearest')
    floor = np.percentile(scale, 20) * 0.7 + 1e-9                      # don't amplify near-silence
    return e / (scale + floor)

bn = {k: local_norm(v) for k, v in bands.items()}
# combined onset strength: kick (low) and snare (mid + high) both count
comb = np.maximum(bn['low'], 0.5 * bn['mid'] + 0.8 * bn['high'])
comb = ndimage.gaussian_filter1d(comb, 0.8)                            # ~10 ms smoothing

min_dist = max(int(a.min_gap * fps), 1)
pk, props = signal.find_peaks(comb, height=a.thr, distance=min_dist, prominence=0.5 * a.thr)
events = []
for i in pk:
    low = bn['low'][max(i - 1, 0):i + 2].max()
    snr = (0.5 * bn['mid'] + 0.8 * bn['high'])[max(i - 1, 0):i + 2].max()
    kind = 'K' if low >= snr else 'S'
    s = float(comb[i])
    w = float(np.clip((s - a.thr) / (3.0 * a.thr), 0.1, 1.0))          # strength 1x..4x threshold -> 0.1..1
    events.append({'t': round(float(tf[i]) + a.offset, 4), 'w': round(w, 3), 'kind': kind, 's': round(s, 2)})

# ---------------------------------------------------------------- beat grid (dynamic programming, tempo prior)
def track_beats(env, fps, bpm, lam):
    P = 60.0 / bpm * fps
    lo, hi = int(round(0.78 * P)), int(round(1.28 * P))
    taus = np.arange(lo, hi + 1)
    pen = -lam * np.log(taus / P) ** 2
    n = len(env)
    score = env.astype(np.float64).copy()
    prev = np.full(n, -1, np.int64)
    for t in range(lo, n):
        idx = t - taus
        ok = idx >= 0
        cand = score[idx[ok]] + pen[ok]
        j = int(np.argmax(cand))
        if cand[j] > 0:
            score[t] = env[t] + cand[j]
            prev[t] = idx[ok][j]
    start = n - 1 - int(np.argmax(score[max(n - hi - 1, 0):][::-1]))
    chain = []
    t = start
    while t >= 0:
        chain.append(t); t = prev[t]
    return np.array(chain[::-1])

snare_n = 0.5 * bn['mid'] + 0.8 * bn['high']
beat_idx = track_beats(np.minimum(comb, 4.0), fps, a.bpm, a.lam)
nb = len(beat_idx)
def near_max(arr, i, r=5): return float(arr[max(i - r, 0):i + r + 1].max())
S_b = np.array([near_max(snare_n, i) for i in beat_idx])
C_b = np.array([near_max(comb, i, 7) for i in beat_idx])
# accent parity: which of the two alternating beats carries the snare/backbeat energy (sticky, local window)
signs = np.where(np.arange(nb) % 2 == 0, 1.0, -1.0)
W8 = 16
K_b = np.array([near_max(bn['low'], i) for i in beat_idx])
D_b = S_b - K_b                                                          # snare-heavy beats positive, kick-heavy negative
par = 1.0
acc = np.zeros(nb, bool)
conf = np.zeros(nb)
for i in range(nb):
    lo_, hi_ = max(i - W8, 0), min(i + W8 + 1, nb)
    B = float((D_b[lo_:hi_] * signs[lo_:hi_]).sum()); tot = float(np.abs(D_b[lo_:hi_]).sum()) + 1e-9
    r = B / tot
    conf[i] = abs(r)
    if i == 0: par = 1.0 if r >= 0 else -1.0
    elif abs(r) > 0.12 and (1.0 if r > 0 else -1.0) != par: par = 1.0 if r > 0 else -1.0
    acc[i] = (signs[i] * par) > 0
if a.debug_accent:
    K_b = np.array([near_max(bn['low'], i) for i in beat_idx])
    feats = {'snare': S_b, 'kick': K_b, 'total': C_b, 'snare-kick': S_b - K_b}
    for nm, v in feats.items():
        out = []
        for Wn in (8, 16):
            rr = []
            for i in range(nb):
                lo_, hi_ = max(i - Wn, 0), min(i + Wn + 1, nb)
                rr.append(abs((v[lo_:hi_] * signs[lo_:hi_]).sum()) / (np.abs(v[lo_:hi_]).sum() + 1e-9))
            out.append('W%d mean|r|=%.2f' % (Wn, np.mean(rr)))
        g = (v * signs).sum() / np.abs(v).sum()
        print('  %-11s %s  global r=%+.2f' % (nm, ' '.join(out), g))
act = np.clip((C_b - 1.0) / 2.0, 0.0, 1.0)                             # how much real drum energy sits on this beat
beats = [{'t': round(float(tf[i]) + a.offset, 4), 'acc': bool(acc[k]), 'a': round(float(act[k]), 3), 'c': round(float(conf[k]), 3)} for k, i in enumerate(beat_idx)]
ivl = np.diff([b['t'] for b in beats])
dur = len(x) / sr
info = {'duration': dur, 'n_events': len(events), 'events_per_sec': len(events) / dur,
        'n_beats': nb, 'beat_interval_mean_s': float(ivl.mean()), 'beat_bpm_mean': float(60 / ivl.mean()),
        'beat_interval_min_s': float(ivl.min()), 'beat_interval_max_s': float(ivl.max()), 'beat_interval_std_ms': float(ivl.std() * 1e3),
        'beats_with_hit_pct': float(100 * (C_b > 1.0).mean()), 'accent_flips': int((np.diff(acc.astype(int)) == 0).sum()), 'accent_conf_mean': float(conf.mean()), 'accent_conf_min': float(conf.min())}
json.dump({'info': info, 'events': events, 'beats': beats}, open(a.out, 'w'))
print(json.dumps(info))

if a.plot:
    import matplotlib; matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    m = (tf >= a.t0) & (tf <= a.t1)
    fig, ax = plt.subplots(3, 1, figsize=(16, 9), sharex=True)
    fm = f <= 6000
    ax[0].pcolormesh(tf[m], f[fm], L[fm][:, m], shading='auto', cmap='magma')
    ax[0].set_ylabel('Hz (0-6k)')
    for k, c in (('low', 'tab:red'), ('mid', 'tab:green'), ('high', 'tab:blue')):
        ax[1].plot(tf[m], bn[k][m], c, lw=0.8, label=k)
    ax[1].legend(loc='upper right'); ax[1].set_ylabel('norm. flux')
    ax[2].plot(tf[m], comb[m], 'k', lw=0.9); ax[2].axhline(a.thr, color='gray', ls='--', lw=0.7)
    for e in events:
        if a.t0 <= e['t'] <= a.t1:
            ax[2].plot(e['t'] - a.offset, e['s'], 'o', color='tab:red' if e['kind'] == 'K' else 'tab:blue', ms=4 + 8 * e['w'])
            for q in (0, 1): ax[q].axvline(e['t'] - a.offset, color='w' if q == 0 else 'gray', lw=0.4, alpha=0.6)
    for b in beats:
        if a.t0 <= b['t'] <= a.t1:
            for q in (0, 2):
                ax[q].axvline(b['t'] - a.offset, color='orange' if b['acc'] else 'cyan', lw=2.2 if b['acc'] else 1.0,
                              alpha=0.9 if b['acc'] else 0.7, ymax=1.0)
    ax[2].set_ylabel('onset strength (orange = accent beat, cyan = other beat)'); ax[2].set_xlabel('s')
    plt.tight_layout(); plt.savefig(a.plot, dpi=80)
