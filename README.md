# Tilos headbang

Turns a DJ-mix mp3 into a 1080p / 30 fps YouTube video: the radio-show logo (a head with headphones) headbangs to the beat,
a text ring turns behind it, and a few times during the mix the glasses become hypnotic spirals. It runs locally, needs no
GPU and no paid service, and renders an hour-long mix in chunks that join seamlessly. The audio of your mp3 is used as it
is (only encoded to AAC 256k).

**Example:** the full 60-minute mix this was built for, rendered with these scripts: https://www.youtube.com/watch?v=WLPPujY9PTI

**Live playground:** [fine-tune the look in your browser first](https://aboros.github.io/tilos-headbang/) -
tweak the head motion, spirals and text ring, press *Copy settings*, and save the result as `settings.json` (see
[Tune the look](#2-tune-the-look)). The page is a single self-contained file in this repo, `docs/index.html`: it is served by GitHub Pages and also works offline when opened in any browser.

## What you get

- A head that nods on the beat: it leans back, then forward and closest to the camera exactly on the hit. Beats are detected
  from the audio (works for steady 4/4 music, e.g. breakbeat 120-130 BPM).
- Two rhythms: nod on every beat, or heavier on every 2nd beat for "lighter" sections you define.
- Optional *energy follow*: nods get softer in quiet passages and full-size in loud ones.
- "Surprise" spiral eyes in 30 s windows (fixed times plus random extra ones), with fades and a few black/white flashes.
- A white text ring (two texts, repeatable) that rotates slowly the whole time.

## 1. Setup

You need Python 3.9+, `ffmpeg` and `rsvg-convert` (from librsvg) available on the command line.

**macOS (Homebrew)**
```bash
brew install ffmpeg librsvg python
```
**Debian / Ubuntu**
```bash
sudo apt install ffmpeg librsvg2-bin python3-venv
```

Then create a virtual environment and install the Python dependencies:

```bash
git clone https://github.com/aboros/tilos-headbang.git
cd tilos-headbang
python3 -m venv .venv
source .venv/bin/activate          # (macOS / Linux; Windows is not tested)
pip install -r requirements.txt
```

Next time, only `source .venv/bin/activate` again in a new terminal. Check the setup:

```bash
ffmpeg -version | head -1 && rsvg-convert --version && python -c "import cv2, scipy, lxml, PIL; print('python deps ok')"
```

## 2. Tune the look

1. Open the [live playground](https://aboros.github.io/tilos-headbang/) (or `docs/index.html`).
2. Play with the sliders. *Play surprise* shows a full spiral-eye window sped up.
3. Press **Copy settings** and save the text as `settings.json` in this folder (the repo ships one with sensible defaults).
   `--settings other.json` uses a different file, and the playground's **Load** box restores a saved one.
4. Check it with the real renderer. A fast 540p preview of 20 s:

```bash
python make_video.py mix.mp3 preview.mp4 --preview --start 255 --work ~/tilos_work
```
(`--start` is in seconds; 255 is just before the first spiral surprise at 4:20.)

The playground head is a simplified 2D version of the real 3D head, so use it to judge feel and ratios, and confirm with `--preview`.

## 3. Render the video

```bash
caffeinate -i python make_video.py mix.mp3 tilos-mix.mp4 --work ~/tilos_work     # macOS; drop "caffeinate -i" elsewhere
```
`--show-schedule` only prints when the surprises happen, without rendering.

What happens: decode the audio, detect the beats (about a minute, needs ~4 GB of free RAM), measure loudness if *energy follow* is on, render in 60 s chunks on all CPU cores but two, then join the chunks and add your original audio. Expect roughly 15-40 minutes for an hour on an Apple Silicon laptop (an estimate; it depends on the CPU).

If it stops or crashes, run the same command again: finished steps and chunks are reused. Chunk files carry a fingerprint of the
settings and code, so changing a setting never reuses stale chunks. Delete the work folder when you are done.

### Options

| Option | Meaning |
|---|---|
| `--settings FILE` | Settings file (default: `settings.json` next to the scripts) |
| `--preview` | 540p fast encode; renders 20 s unless `--limit` is given |
| `--start S`, `--limit S` | Render only a part of the mix (seconds) |
| `--workers N` | Parallel render processes (default: CPU cores - 2) |
| `--chunk S` | Chunk length in seconds (default 60) |
| `--work DIR` | Work folder; reuse it across runs to share the beat analysis |
| `--bpm N` | Tempo hint for the beat tracker (default 125) |
| `--show-schedule` | Print the surprise schedule and quit |

## Settings reference (`settings.json`)

| Key | Meaning |
|---|---|
| `depth`, `windup`, `fwd`, `yaw`, `roll`, `sway` | Reference *maximum* of the motion: nod depth (deg), lean-back before the nod, lean toward the camera, side turn and tilt on accents (deg), idle sway. Each beat is scaled by its accent and the rhythm, so nothing is a constant swing. |
| `stiff`, `damp` | Nod snappiness (Hz, higher = quicker) and damping (lower = bouncier) |
| `energy` | 0..1. 0 = every beat equally strong; 1 = quiet passages nod down to 45 % |
| `sp_crazy`, `sp_speed`, `sp_zoom`, `sp_flashes`, `sp_phrase`, `sp_len` | Spiral eyes: intensity (flashes and direction changes start above ~0.35), base spin (rev/s), zoom pulses, max flashes per second, beats between direction changes, window length (s) |
| `rg_text`, `rg_text2`, `rg_rep`, `rg_rpm`, `rg_r` | Text ring: the two texts, how often the pair repeats around the ring, rotation (rpm), radius (px) |
| `scale` | Size of the whole composition |
| `bpm`, `mode`, `prev`, `loud` | Playground-only (preview); ignored by the renderer |

## Timeline (`tilos_config.py`)

Times are seconds from the start of the mix.
- `LIGHT`: sections where the head nods on every 2nd beat. Everywhere else it nods on every beat.
- `SPIRAL_FIXED`: fixed start times of surprises. `SPIRAL_RANDOM`: how many extra ones are added in the largest gaps;
  `SEED` changes where.

## Files

| File | Purpose |
|---|---|
| `make_video.py` | The command you run: decode, detect, render in parallel chunks, join, add audio |
| `headbang_fx.py` | Renders one time window of the video |
| `detect_hits.py` | Beat and accent detection (scipy only) |
| `energy.py` | Loudness curve for *energy follow* |
| `tilos_config.py`, `settings.json` | Timeline and tunable settings |
| `assets/` | The logo SVG and the Inter SemiBold font for the ring text (SIL Open Font License, see `assets/Inter-LICENSE.txt`) |
| `docs/index.html` | The browser playground (single file, hosted with GitHub Pages) |
| `LICENSE` | MIT licence for the code |

## Using your own logo

Replace `assets/tilos-logo.svg` (or pass `--svg`). The renderer expects the path ids of the original file (`g`,`h` background
blocks, `i`,`j` headphones, `k` face with hair, `m`,`n` mouth) and the lens positions in `LENS` in `headbang_fx.py`.

## Hosting the playground (GitHub Pages)

In the repository settings open **Pages**, choose *Deploy from a branch*, branch `main`, folder `/docs`. The playground then
lives at https://aboros.github.io/tilos-headbang/ . There is no build step; edit `docs/index.html` and push.

## Licence

The code is released under the [MIT licence](LICENSE). The bundled font (Inter) is under the SIL Open Font License
(`assets/Inter-LICENSE.txt`). The logo in `assets/tilos-logo.svg` belongs to its owners (Tilos Radio) and is **not** covered
by the MIT licence; it is included as the example the renderer was built for.
