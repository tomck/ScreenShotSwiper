#!/usr/bin/env python3
"""
ScreenShotSwiper backend. Serves the swipe UI and handles the actual file
operations. Binds to 127.0.0.1 only -- never expose this beyond localhost,
these are your personal screenshots.

Actions:
  keep      -> no file move, just recorded
  toss      -> move file (+ its duplicates) to ~/.Trash
  important -> move file to ~/Pictures/Important Screenshots
"""
import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_file, abort

HERE = Path(__file__).parent
TRASH_DIR = Path.home() / ".Trash"
IMPORTANT_DIR = Path.home() / "Pictures" / "Important Screenshots"
DECISIONS_PATH = HERE / "decisions.jsonl"

KEYWORDS = [
    "social security", "ssn", "passport", "driver's license", "drivers license",
    "date of birth", "routing number", "account number", "confirmation number",
    "invoice", "receipt", "tax return", "w-2", "1099", "ein", "password",
    "security code", "cvv", "insurance", "policy number", "vin", "bill of sale",
    "subpoena", "diagnosis", "prescription", "bank statement", "credit card",
    "american express", "amex",
]
KEYWORD_RES = [(kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)) for kw in KEYWORDS]
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# Any run of 13-19 digits, loosely grouped with spaces/dashes (or not grouped at
# all) -- covers Visa/Mastercard/Discover's 4-4-4-4, Amex's 4-6-5, Diners' 4-6-4,
# and anything else, since we don't hardcode a brand's layout. Luhn validation
# below is what actually keeps this precise instead of matching arbitrary digit
# runs (order numbers, chart axes, etc.).
CARD_CANDIDATE_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def luhn_valid(digits):
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def has_card_number(text):
    for m in CARD_CANDIDATE_RE.finditer(text):
        digits = re.sub(r"[ -]", "", m.group())
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            return True
    return False


def find_keyword_hits(text):
    hits = [kw for kw, pattern in KEYWORD_RES if pattern.search(text)]
    if SSN_RE.search(text):
        hits.append("ssn-pattern")
    if has_card_number(text):
        hits.append("card-number-pattern")
    return hits


def unique_dest(directory, filename):
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 2
    while (directory / f"{stem} {n}{suffix}").exists():
        n += 1
    return directory / f"{stem} {n}{suffix}"


def load_records():
    inventory = {it["path"]: it for it in json.load(open(HERE / "inventory.json"))}
    dedup_rows = list(csv.DictReader(open(HERE / "dedup_report.csv", newline="")))
    reps = [r for r in dedup_rows if r["is_representative"] == "True" and r["dup_type"] != "ERROR"]
    dup_by_group = {}
    for r in dedup_rows:
        if r["is_representative"] == "False":
            dup_by_group.setdefault(r["group_id"], []).append(r["path"])

    ocr_path = HERE / "ocr_results.jsonl"
    ocr_available = ocr_path.exists()
    ocr = {}
    if ocr_available:
        for line in open(ocr_path):
            rec = json.loads(line)
            ocr[rec["path"]] = rec

    records = []
    for r in reps:
        path = r["path"]
        inv = inventory.get(path, {})
        ocr_rec = ocr.get(path, {})
        text = "\n".join(ocr_rec.get("text", []))
        dups = dup_by_group.get(r["group_id"], [])
        records.append({
            "path": path,
            "size": inv.get("size", 0),
            "mtime": inv.get("mtime", 0),
            "width": inv.get("width"),
            "height": inv.get("height"),
            "ocr_error": ocr_rec.get("error"),
            "text": text,
            "keyword_hits": find_keyword_hits(text) if text else [],
            "dup_paths": dups,
        })
    records.sort(key=lambda x: x["mtime"], reverse=True)
    return records, ocr_available


def load_decisions():
    decided = {}
    if DECISIONS_PATH.exists():
        with open(DECISIONS_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    rec = json.loads(line)
                    decided[rec["path"]] = rec
    return decided


def append_decision(rec):
    with open(DECISIONS_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")


app = Flask(__name__)
ALL_RECORDS, OCR_ENABLED = load_records()
BY_PATH = {r["path"]: r for r in ALL_RECORDS}


@app.route("/")
def index():
    return send_file(HERE / "swiper.html")


@app.route("/api/queue")
def queue():
    decided = load_decisions()
    remaining = [r for r in ALL_RECORDS if r["path"] not in decided]
    total = len(ALL_RECORDS)
    tallies = {"keep": 0, "toss": 0, "important": 0}
    for rec in decided.values():
        if rec["action"] in tallies:
            tallies[rec["action"]] += 1
    out = []
    for r in remaining[:50]:  # only need a lookahead buffer, not the whole pile
        out.append({
            "path": r["path"],
            "filename": Path(r["path"]).name,
            "date": datetime.fromtimestamp(r["mtime"]).strftime("%Y-%m-%d") if r["mtime"] else "",
            "width": r["width"], "height": r["height"], "size": r["size"],
            "text": r["text"][:500],
            "keyword_hits": r["keyword_hits"],
            "ocr_error": r["ocr_error"],
            "dup_count": len(r["dup_paths"]),
        })
    return jsonify({"items": out, "remaining": len(remaining), "total": total, "tallies": tallies,
                     "ocr_enabled": OCR_ENABLED})


def summarize(rec):
    return {
        "path": rec["path"],
        "filename": Path(rec["path"]).name,
        "date": datetime.fromtimestamp(rec["mtime"]).strftime("%Y-%m-%d") if rec["mtime"] else "",
        "width": rec["width"], "height": rec["height"], "size": rec["size"],
        "text": rec["text"][:300],
        "keyword_hits": rec["keyword_hits"],
        "dup_count": len(rec["dup_paths"]),
    }


@app.route("/api/search")
def search():
    if not OCR_ENABLED:
        return jsonify({"results": []})
    q = request.args.get("q", "").strip().lower()
    if len(q) < 2:
        return jsonify({"results": []})
    decided = load_decisions()
    matches = []
    for r in ALL_RECORDS:
        if r["path"] in decided:
            continue
        if q in r["text"].lower() or q in Path(r["path"]).name.lower():
            matches.append(r)
    matches.sort(key=lambda x: x["mtime"], reverse=True)
    return jsonify({"results": [summarize(r) for r in matches[:30]], "total_matches": len(matches)})


@app.route("/image")
def image():
    path = request.args.get("path")
    rec = BY_PATH.get(path)
    if rec is None:
        abort(404)
    return send_file(path)


@app.route("/api/decide", methods=["POST"])
def decide():
    data = request.get_json()
    path, action = data["path"], data["action"]
    rec = BY_PATH.get(path)
    if rec is None:
        abort(404)
    if load_decisions().get(path):
        return jsonify({"ok": True, "note": "already decided"})

    moved_to = None
    if action == "toss":
        dest = unique_dest(TRASH_DIR, Path(path).name)
        shutil.move(path, dest)
        moved_to = str(dest)
        for dup in rec["dup_paths"]:
            if Path(dup).exists():
                dup_dest = unique_dest(TRASH_DIR, Path(dup).name)
                shutil.move(dup, dup_dest)
                append_decision({"path": dup, "action": "toss", "reason": f"duplicate-of {path}",
                                  "moved_to": str(dup_dest), "ts": datetime.now().isoformat()})
    elif action == "important":
        dest = unique_dest(IMPORTANT_DIR, Path(path).name)
        shutil.move(path, dest)
        moved_to = str(dest)
    elif action == "keep":
        pass
    else:
        abort(400)

    append_decision({"path": path, "action": action, "moved_to": moved_to, "ts": datetime.now().isoformat()})
    return jsonify({"ok": True})


@app.route("/api/undo", methods=["POST"])
def undo():
    if not DECISIONS_PATH.exists():
        return jsonify({"ok": False, "error": "nothing to undo"})
    lines = DECISIONS_PATH.read_text().splitlines()
    if not lines:
        return jsonify({"ok": False, "error": "nothing to undo"})
    last = json.loads(lines[-1])
    if last.get("moved_to") and Path(last["moved_to"]).exists():
        shutil.move(last["moved_to"], last["path"])
    DECISIONS_PATH.write_text("\n".join(lines[:-1]) + ("\n" if len(lines) > 1 else ""))
    return jsonify({"ok": True, "restored": last["path"]})


if __name__ == "__main__":
    print(f"{len(ALL_RECORDS)} total screenshots loaded.")
    IMPORTANT_DIR.mkdir(parents=True, exist_ok=True)
    app.run(host="127.0.0.1", port=5731, debug=False)
