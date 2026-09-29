#!/usr/bin/env python3
"""Run as a subprocess per file so a stuck read can be killed without
taking down the batch. Prints one JSON line: sha256, dhash, width, height."""
import hashlib
import json
import sys
from PIL import Image
import imagehash

path = sys.argv[1]

h = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)

with Image.open(path) as im:
    width, height = im.size
    dhash = str(imagehash.dhash(im))

print(json.dumps({"sha256": h.hexdigest(), "dhash": dhash, "width": width, "height": height}))
