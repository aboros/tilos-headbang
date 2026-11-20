#!/usr/bin/env python3
"""Tilos headbang video: mp3 in, 1080p30 mp4 out.

    python3 make_video.py mix.mp3 out.mp4

Steps: decode audio -> detect beats (events.json) -> render the video in parallel chunks -> join + add the original audio.
Everything lands in a work folder (default: <out>_work). Re-running the same command resumes: finished steps and finished
chunks are skipped. Delete the work folder to start from scratch.
"""
import argparse, hashlib, json, math, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
for _i, _a in enumerate(sys.argv):                 # --settings must be known before tilos_config is imported
    if _a == '--settings' and _i + 1 < len(sys.argv): os.environ['TILOS_SETTINGS'] = os.path.abspath(sys.argv[_i + 1])
    elif _a.startswith('--settings='): os.environ['TILOS_SETTINGS'] = os.path.abspath(_a.split('=', 1)[1])
import tilos_config as CFG

FPS = 30
ap = argparse.ArgumentParser()
ap.add_argument('mp3'); ap.add_argument('out')
ap.add_argument('--svg', default=os.path.join(HERE, 'assets', 'tilos-logo.svg'))
ap.add_argument('--settings', help='settings json (default: settings.json next to this script)')
ap.add_argument('--preview', action='store_true', help='fast 540p preview; renders 20 s unless --limit is given')
ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 4) - 2), help='parallel render processes')
ap.add_argument('--chunk', type=float, default=60.0, help='seconds per render chunk')
ap.add_argument('--work', help='work folder')
ap.add_argument('--start', type=float, default=0.0, help='render only from this second (testing)')
ap.add_argument('--limit', type=float, help='render only this many seconds (testing)')
ap.add_argument('--spiral-windows', help='override the surprise windows, mix seconds A:B,C:D (testing)')
ap.add_argument('--bpm', type=float, default=125.0, help='tempo prior for the beat tracker')
ap.add_argument('--show-schedule', action='store_true', help='print the surprise/ring schedule and quit')
args = ap.parse_args()
if args.preview and args.limit is None: args.limit = 20.0


def run(cmd, **kw):
    r = subprocess.run(cmd, **kw)
    if r.returncode: sys.exit('failed: ' + ' '.join(map(str, cmd[:4])) + ' ...')
    return r


for tool in ('ffmpeg', 'ffprobe', 'rsvg-convert'):
    if not shutil.which(tool) and not args.show_schedule:
        sys.exit(f'{tool} not found - see README (brew install ffmpeg librsvg)')

if args.show_schedule:
    total = args.limit or 3600.0
else:
    total = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', args.mp3]))
sp, rg = CFG.schedule(total)
if args.spiral_windows: sp = [tuple(float(v) for v in w.split(':')) for w in args.spiral_windows.split(',')]
print('settings:', CFG.SETTINGS_FILE if os.path.exists(CFG.SETTINGS_FILE) else 'built-in defaults')
print('audio: %s (%.1f s)' % (CFG.fmt(total), total))
print('spiral eyes :', ', '.join('%s-%s' % (CFG.fmt(a), CFG.fmt(b)) for a, b in sp))
print('text ring   : always on')
print('lighter mood:', ', '.join('%s-%s' % (CFG.fmt(a), CFG.fmt(b)) for a, b in CFG.LIGHT))
if args.show_schedule: sys.exit(0)

work = args.work or os.path.splitext(args.out)[0] + '_work'
os.makedirs(work, exist_ok=True)
t_start = time.time()

# 1) audio for analysis
wav = os.path.join(work, 'mix22k.wav')
if not os.path.exists(wav):
    print('[1/4] decoding audio ...', flush=True)
    run(['ffmpeg', '-y', '-loglevel', 'error', '-i', args.mp3, '-ac', '1', '-ar', '22050', '-c:a', 'pcm_s16le', wav + '.tmp.wav'])
    os.replace(wav + '.tmp.wav', wav)

# 2) beats
ev = os.path.join(work, 'events.json')
if not os.path.exists(ev):
    print('[2/4] detecting beats (about 1 minute, needs ~4 GB RAM) ...', flush=True)
    run([sys.executable, os.path.join(HERE, 'detect_hits.py'), wav, ev + '.tmp', '--offset', '0.006', '--bpm', str(args.bpm), '--lam', '150'])
    os.replace(ev + '.tmp', ev)
info = json.load(open(ev))['info']
print('      %d beats found' % len(json.load(open(ev))['beats']))

# 2b) loudness curve for the "energy follow" setting (fast)
en = None
if CFG.SET['energy'] > 0:
    en = os.path.join(work, 'energy.json')
    if not os.path.exists(en):
        print('      measuring loudness ...', flush=True)
        run([sys.executable, os.path.join(HERE, 'energy.py'), wav, en])

# 3) chunks (whole frames, so the pieces join exactly)
f_start = int(round(args.start * FPS))
f_end = int(total * FPS) if args.limit is None else min(int(total * FPS), f_start + int(round(args.limit * FPS)))
cf = max(FPS, int(round(args.chunk * FPS)))
jobs = []
_sig = hashlib.sha1(json.dumps([CFG.SET, sp], sort_keys=True).encode() + open(args.svg, 'rb').read() + open(os.path.join(HERE, 'headbang_fx.py'), 'rb').read()
                    + open(os.path.join(HERE, 'tilos_config.py'), 'rb').read()).hexdigest()[:8]     # changed settings/code -> new chunk names, no stale reuse
suffix = '_p' if args.preview else ''
for i, f0 in enumerate(range(f_start, f_end, cf)):
    f1 = min(f0 + cf, f_end)
    jobs.append((i, f0, f1, os.path.join(work, 'part_%s_%07d_%07d%s.mp4' % (_sig, f0, f1, suffix))))
print('[3/4] rendering %d chunks with %d parallel workers ...' % (len(jobs), args.workers), flush=True)


def render_chunk(job):
    i, f0, f1, path = job
    if os.path.exists(path): return i, 'cached'
    tmp = path + '.tmp.mp4'
    cmd = [sys.executable, os.path.join(HERE, 'headbang_fx.py'), args.svg, ev, args.mp3, tmp,
           '--t0', '%.6f' % (f0 / FPS), '--dur', '%.6f' % ((f1 - f0) / FPS), '--total', str(total), '--no-audio']
    if en: cmd += ['--energy', en]
    if args.preview: cmd += ['--preview']
    if args.spiral_windows: cmd += ['--spiral-windows', args.spiral_windows]
    with open(os.path.join(work, 'part_%07d.log' % f0), 'w') as lg:
        r = subprocess.run(cmd, stdout=lg, stderr=subprocess.STDOUT, env=dict(os.environ, OMP_NUM_THREADS='1', OPENCV_FOR_THREADS_NUM='1'))
    if r.returncode: raise RuntimeError('chunk %d failed, see %s' % (i, os.path.join(work, 'part_%07d.log' % f0)))
    os.replace(tmp, path)
    return i, 'ok'


done = 0
with ThreadPoolExecutor(args.workers) as ex:
    futs = [ex.submit(render_chunk, j) for j in jobs]
    for fu in as_completed(futs):
        fu.result(); done += 1
        el = time.time() - t_start
        print('      chunk %d/%d done  (elapsed %d min)' % (done, len(jobs), el // 60), flush=True)

# 4) join + original audio
print('[4/4] joining chunks and adding the original audio ...', flush=True)
lst = os.path.join(work, 'parts.txt')
with open(lst, 'w') as f:
    for _, _, _, p in jobs: f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', lst,
       '-ss', '%.6f' % (f_start / FPS), '-t', '%.6f' % ((f_end - f_start) / FPS), '-i', args.mp3,
       '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '256k', '-shortest', '-movflags', '+faststart', args.out]
run(cmd)
print('done: %s   (total %d min)' % (args.out, (time.time() - t_start) // 60))
