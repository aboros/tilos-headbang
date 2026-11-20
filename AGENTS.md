# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.

## What this is

`tilos-headbang` turns a DJ-mix mp3 into a 1080p / 30 fps video: a logo head (SVG, with headphones and round glasses) headbangs
to the detected beats, a white text ring rotates behind it, and in a few "surprise" windows the glasses turn into hypnotic
spirals. It runs locally (Python + ffmpeg, no GPU, no network) and renders an hour-long mix in parallel chunks that join
seamlessly. Primary target: macOS on Apple Silicon, breakbeat mixes around 124-126 BPM in 4/4. Reference output: https://www.youtube.com/watch?v=WLPPujY9PTI Repo: https://github.com/aboros/tilos-headbang

Two deliverables live here:
1. **The renderer** (Python scripts in the repo root): the source of truth for what the video looks like.
2. **The tuning playground** (`docs/index.html`, hosted with GitHub Pages at https://aboros.github.io/tilos-headbang/):
   a browser preview where the user tunes settings, then copies them into `settings.json` for the renderer.

## Layout

| Path | Role |
|---|---|
| `make_video.py` | Orchestrator: decode audio, beat detection, energy curve, parallel chunk render, concat + original audio. Resumable. |
| `headbang_fx.py` | Renders one time window (`--t0`, `--dur`): rig, springs, spiral eyes, ring. Called once per chunk as a subprocess. |
| `detect_hits.py` | Beat/accent detection (scipy only): STFT band flux, DP beat tracker, accent via snare-minus-kick. Writes `events.json`. |
| `energy.py` | Loudness curve (`energy.json`) for the "energy follow" setting. |
| `tilos_config.py` | `DEFAULTS` for every setting, `settings.json` loading, the timeline (`LIGHT`, `SPIRAL_FIXED`, `SEED`), `schedule()`. |
| `settings.json` | Default settings; produced by the playground's "Copy settings". |
| `assets/` | `tilos-logo.svg`, `Inter-SemiBold.otf` (+ OFL licence). |
| `docs/index.html` | The playground: ONE self-contained file (HTML + CSS + JS + the logo path data inline). |

## Setup and quick checks

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt   # plus ffmpeg and librsvg (rsvg-convert)
python make_video.py mix.mp3 preview.mp4 --preview --start 255 --work ~/tilos_work       # 540p, 20 s, ~15 s to render
python make_video.py mix.mp3 x.mp4 --show-schedule                                       # prints the surprise schedule only
```
There is no test suite yet. Verify changes by rendering a short `--preview` and looking at frames, and by the checks below.
Check flags against the argparse definitions in the code rather than assuming them.

## Rules that keep the renderer correct

- **Chunks must join invisibly.** Every chunk is rendered by a separate process and concatenated with `-c copy`. Therefore:
  all motion and effect state must be a deterministic function of absolute time (mix time) and beat index. Randomness uses the
  hash-based `rnd(...)` keyed on beat/phrase indices; never use `random`/`np.random` global state in anything that affects
  frames. Spring state is re-derived by a `PREROLL` warm-up (2.5 s) before each chunk; spiral controls are recomputed from t=0
  up to the chunk end. Chunk boundaries are whole frames (`make_video.py` works in frame counts). If you add any stateful
  effect, give it a warm-up or make it a pure function of time, and re-run a seam test (below).
- **Two time bases.** Beat times in `events.json` are file time. `--mix-offset` maps an excerpt into mix time (used only for
  testing with excerpts). Section logic (`LIGHT`, spiral windows) is in mix time. Don't mix them up.
- **Chunk cache fingerprint.** Chunk file names include a hash of settings, spiral windows, SVG, `headbang_fx.py` and
  `tilos_config.py`. If you add another input that changes frames, include it in that hash.
- **Settings are one contract in three places.** Adding or changing a setting means updating together:
  `DEFAULTS` in `tilos_config.py` (and its consumer in `headbang_fx.py`), `DEF` and `SPEC` in `docs/index.html`, `settings.json`,
  and the settings table in `README.md`. Unknown keys in `settings.json` are ignored on purpose (the playground exports
  preview-only keys such as `bpm`, `mode`, `prev`, `loud`).
- **Playground is an approximation.** It draws the head with 2D canvas transforms and a simplified rig; the renderer uses a
  pseudo-3D perspective rig (homographies on layered cards). Keep their semantics aligned (same units, same meaning of each
  setting), but the renderer wins any disagreement. Never claim pixel parity.
- **Dependencies stay minimal:** numpy, scipy, opencv-python-headless, lxml, Pillow, plus ffmpeg and rsvg-convert. No librosa,
  no torch, no network access at render time. Beat detection is intentionally scipy-only.
- **Performance budget:** about 2 CPU-seconds per video second. Chunk processes run with `OMP_NUM_THREADS=1`; parallelism comes
  from the number of chunks in flight. Avoid per-frame work that scales with the full mix length. A full-hour detection needs
  about 4 GB RAM; don't increase that.
- **Photosensitivity.** Spiral flashes are limited to the lens area and to about 2 per second with at least 0.45 s spacing, and
  are plain black/white inversions. Don't raise those limits or add full-screen flashes; the user explicitly rejected red and
  coloured flashes.
- **The motion character is approved.** The user signed off on: lean back, then forward and closest to the camera exactly on the
  beat; heavy nod every 2nd beat in "light" sections, every beat elsewhere; accent beats (from snare-minus-kick evidence) get
  the yaw/roll kicks; spring-damper dynamics with the peak calibrated to land on the beat (about +-10 ms). Settings scale this
  motion; don't change its character without asking.
- **Defaults are decisions, not accidents** (for example `sp_crazy` 0.15, ring radius 900 px so the ring is cropped at the top and
  bottom, ring always visible, two ring texts repeated 4 times). Don't change defaults unless asked.

## Rules for the playground (`docs/index.html`)

- Keep it a single static file that works from `file://` and GitHub Pages: no build step, no framework, no bundler. The only
  external resource is the Inter font from Google Fonts; everything must still work if it fails to load.
- The logo geometry is embedded as the `PATHS` constant (ids `g`,`h` background blocks, `i`,`j` headphones, `k` face with hair,
  `m`,`n` mouth). If `assets/tilos-logo.svg` changes, regenerate that constant from it.
- "Copy settings" must keep producing JSON that `tilos_config.py` accepts. The "Load" box must accept what "Copy" produces.
- Clipboard, audio and similar APIs need a user gesture and can throw: wrap in try/catch.

## Verifying changes

- **Seam test** (any change touching motion, springs, spiral controls or the ring): render a 12 s window once and as two 6 s
  windows (same `--t0` split, same flags, `--no-audio`), decode both and compare frames. The mean absolute difference should
  stay at encoder-noise level (a few tenths of 255). Beat/effect jumps at the seam mean a state bug.
- **Beat alignment:** `--log file.csv` writes per-frame pitch/yaw; the pitch peaks should sit within about 10 ms of the beats.
- **Excerpt testing:** run `make_video.py` on a short mp3 with `--spiral-windows A:B` to force an effect window into the excerpt
  (mix time = file time unless `--mix-offset` is used with `headbang_fx.py` directly).
- Look at actual frames (extract with ffmpeg) for visual changes; the lens spirals, the ring crop and the head centering are
  easy to break silently.

## Conventions

- Python: plain scripts, no package structure; keep modules importable from the repo root (they insert their own directory into
  `sys.path`). Prefer small, readable functions; comments explain why, not what.
- Do not commit audio/video, `.venv`, work folders (`*_work/`) or `__pycache__` (see `.gitignore`).
- Keep the README accurate: it is the user-facing manual (setup with a venv, tuning workflow, options, settings reference).
- Licences: code is MIT (`LICENSE`); Inter is OFL (`assets/Inter-LICENSE.txt`); the logo belongs to Tilos Radio and is not
  covered by MIT. Keep those notices when moving files.

## Known limitations and ideas

- The full pipeline has been run end to end on the real 60-minute mix it was built for; the result is published at
  https://www.youtube.com/watch?v=WLPPujY9PTI (a useful reference for what the output should look like). For other mixes,
  sanity-check a full-length render: beat tracking in breakdowns and track transitions is the weakest part.
- Energy follow scales amplitude between 45 % and 100 % from a smoothed loudness curve; there is no per-section override yet.
- Spiral flashes and direction changes only start above `sp_crazy` of about 0.35 (thresholds are in `build_fx_controls`).
- Playground: could show the actual surprise/ring schedule, and could preview audio-synced motion by loading an mp3 locally.
