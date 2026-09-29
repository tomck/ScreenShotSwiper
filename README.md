# ScreenShotSwiper

A local, private pipeline for triaging years of accumulated screenshots on
macOS: find them, dedupe them, OCR them, flag anything that looks
sensitive, then review what's left in a Tinder-style swipe UI — keep,
toss, or mark important.

Everything runs **entirely on your Mac**. No screenshot, filename, or
extracted text is ever sent anywhere — OCR uses Apple's built-in Vision
framework, not a cloud API.

## Why

If you're like most people, you have thousands of screenshots scattered
across Desktop/Documents/Downloads/Pictures going back years, with no
idea which ones are actual duplicates, which are worth keeping, and which
are safe to delete. This tool builds you a fast, low-effort way to plow
through them.

## Requirements

- macOS (uses the Vision framework for on-device OCR — this will not work
  on other platforms)
- Python 3.9+

## Setup

```bash
git clone https://github.com/tomck/ScreenShotSwiper.git
cd ScreenShotSwiper
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## Usage

Run the pipeline stages in order. Each stage is resumable — safe to
interrupt and rerun.

```bash
./venv/bin/python3 01_inventory.py   # finds screenshot-like files
./venv/bin/python3 02_dedup.py       # hashes everything, groups exact/near-duplicates
./venv/bin/python3 03_ocr.py         # OCRs every unique (non-duplicate) screenshot
./venv/bin/python3 swiper_server.py  # starts the review UI at http://127.0.0.1:5731
```

Then open **http://127.0.0.1:5731** and start swiping:

| Action | Keyboard | Drag |
|---|---|---|
| Keep | → | drag right |
| Toss (to `~/.Trash`) | ← | drag left |
| Mark important (to `~/Pictures/Important Screenshots`) | ↑ | drag up |

Tossing a screenshot also trashes its exact/near-duplicate copies. Your
progress is saved incrementally (`decisions.jsonl`), so you can close the
tab and pick up later exactly where you left off. There's an **Undo**
button if you swipe the wrong way.

### Alternative: static report instead of swiping

If you'd rather just get a searchable HTML table (no interactive
review/file-moving), run:

```bash
./venv/bin/python3 04_build_report.py
open report.html
```

## How it works

1. **`01_inventory.py`** — finds screenshot-like files. Checks
   `defaults read com.apple.screencapture location` to include wherever
   *your* Mac actually saves new screenshots (it isn't always
   `~/Desktop`), plus Desktop/Documents/Downloads/Pictures. Skips
   `.photoslibrary` packages entirely — Photos.app's library must never
   be touched via raw file operations. Also detects "cloud-only"
   placeholder files (iCloud Drive optimized storage) via `st_blocks == 0`
   so it never hangs trying to read one.
2. **`02_dedup.py`** — SHA-256 for exact duplicates, perceptual hashing
   (dHash) for near-duplicates (e.g. three screenshots of the same error
   taken seconds apart). Reading each file's bytes also transparently
   downloads any cloud-only placeholders.
3. **`03_ocr.py`** — runs Vision's text recognition on every
   non-duplicate screenshot, single long-running process (spawning a
   fresh process per image is much slower — Vision pays a one-time
   model warm-up cost, not a per-image one).
4. **`swiper_server.py`** / **`swiper.html`** — a small local Flask
   server + swipe UI. Also flags screenshots whose OCR text matches
   patterns for SSNs, account numbers, passwords, credit-card-style
   groupings, etc., so you know to look twice before swiping.

## Customizing

- **Where it looks for screenshots**: edit `LOCATIONS` in
  `01_inventory.py`.
- **Filename matching**: edit `NAME_PATTERNS` in `01_inventory.py`
  (defaults to filenames containing "screenshot", "screen shot", or
  "cleanshot").
- **Sensitive-content keywords**: edit `KEYWORDS` in
  `04_build_report.py` / `swiper_server.py`.
- **Where "important" screenshots go**: edit `IMPORTANT_DIR` in
  `swiper_server.py`.

## License

MIT — see [LICENSE](LICENSE).
