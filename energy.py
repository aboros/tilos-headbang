#!/usr/bin/env python3
"""Loudness curve of the mix for the "energy follow" setting.   python3 energy.py mix22k.wav energy.json
Writes {"hop": 0.5, "e": [...]}: one value per 0.5 s, 0 = quietest typical passages, 1 = loudest, smoothed over ~6 s."""
import json, sys
import numpy as np
from scipy.io import wavfile
from scipy.ndimage import gaussian_filter1d

def main(wav, out, hop=0.5, win=1.0, smooth_s=3.0):
    sr, x = wavfile.read(wav)
    x = x.astype(np.float32) / (32768.0 if x.dtype == np.int16 else 1.0)
    if x.ndim > 1: x = x.mean(axis=1)
    h, w = int(sr * hop), int(sr * win)
    n = max(1, (len(x) - w) // h + 1)
    rms = np.array([np.sqrt(np.mean(x[i * h:i * h + w] ** 2) + 1e-12) for i in range(n)])
    db = gaussian_filter1d(20 * np.log10(rms), smooth_s / hop)
    lo, hi = np.percentile(db, 10), np.percentile(db, 90)
    e = np.clip((db - lo) / max(hi - lo, 1e-6), 0, 1)
    json.dump({'hop': hop, 'e': [round(float(v), 3) for v in e]}, open(out, 'w'))
    print('energy: %d values, 10th pct %.1f dB, 90th pct %.1f dB' % (n, lo, hi))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
