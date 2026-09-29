#!/usr/bin/env python3
"""
Find all screenshot-like files under: wherever macOS is actually configured
to save new screenshots (read from `defaults com.apple.screencapture
location`, not assumed), plus Desktop/Documents/Downloads/Pictures as
plain files only -- explicitly skips any .photoslibrary package, since
that's Photos.app's internal database and must never be touched via raw
file operations.

Matches filenames containing "screenshot", "screen shot", or "cleanshot"
(case-insensitive), with a .png/.jpg/.jpeg extension.

Writes inventory.json: a list of {path, size, mtime, width, height}.
"""
import json
import os
import subprocess
import time
from pathlib import Path
from PIL import Image


def get_screenshot_location():
    """Wherever macOS is actually configured to save new screenshots
    (`defaults write com.apple.screencapture location ...`). Falls back to
    ~/Desktop, which is the OS default when this has never been changed."""
    try:
        out = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"],
                              capture_output=True, text=True, check=True)
        raw = out.stdout.strip()
        if raw:
            return str(Path(raw).expanduser())
    except subprocess.CalledProcessError:
        pass
    return str(Path.home() / "Desktop")


LOCATIONS = list(dict.fromkeys([  # de-duped, order preserved
    get_screenshot_location(),
    str(Path.home() / "Desktop"),
    str(Path.home() / "Documents"),
    str(Path.home() / "Downloads"),
    str(Path.home() / "Pictures"),
]))
NAME_PATTERNS = ("screenshot", "screen shot", "cleanshot")
EXTENSIONS = (".png", ".jpg", ".jpeg")
OUT_PATH = Path(__file__).parent / "inventory.json"


def is_screenshot_name(filename):
    lower = filename.lower()
    return lower.endswith(EXTENSIONS) and any(p in lower for p in NAME_PATTERNS)


def main():
    items = []
    start = time.time()
    for loc in LOCATIONS:
        for dirpath, dirnames, filenames in os.walk(loc):
            # never descend into a Photos/iPhoto library package
            dirnames[:] = [d for d in dirnames if not d.lower().endswith((".photoslibrary", ".photoslibrary/"))]
            for fn in filenames:
                if not is_screenshot_name(fn):
                    continue
                full = Path(dirpath) / fn
                st = full.stat()
                cloud_only = st.st_size > 0 and st.st_blocks == 0
                width = height = None
                if not cloud_only:
                    try:
                        with Image.open(full) as im:
                            width, height = im.size
                    except Exception as e:
                        print(f"  skipping (unreadable): {full} ({e})", flush=True)
                        continue
                items.append({
                    "path": str(full),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                    "width": width,
                    "height": height,
                    "cloud_only": cloud_only,
                })
                if len(items) % 200 == 0:
                    print(f"  found {len(items)} so far ({time.time()-start:.0f}s)...", flush=True)

    with open(OUT_PATH, "w") as f:
        json.dump(items, f, indent=1)
    n_cloud_only = sum(1 for it in items if it["cloud_only"])
    print(f"\nTotal: {len(items)} screenshot files ({n_cloud_only} cloud-only placeholders, "
          f"dimensions unknown for those). Written to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
