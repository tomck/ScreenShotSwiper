#!/usr/bin/env python3
"""
Join inventory.json + dedup_report.csv + ocr_results.jsonl into one local
HTML report. Never published anywhere -- opened directly in a browser via
file://, since screenshots can contain sensitive personal/financial info.
"""
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent
OUT_PATH = HERE / "report.html"

KEYWORDS = [
    "social security", "ssn", "passport", "driver's license", "drivers license",
    "date of birth", "routing number", "account number", "confirmation number",
    "invoice", "receipt", "tax return", "w-2", "1099", "ein", "password",
    "security code", "cvv", "insurance", "policy number", "vin", "bill of sale",
    "subpoena", "diagnosis", "prescription", "bank statement", "credit card",
]
KEYWORD_RES = [(kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)) for kw in KEYWORDS]
SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# require real card-style grouping (4-4-4-4 or 4-4-4-3) in the ORIGINAL text,
# not digits merged together after stripping unrelated spaces
CC_RE = re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{3,4}\b")


def file_uri(path):
    return "file://" + quote(str(path))


def fmt_date(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def fmt_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def find_keyword_hits(text):
    hits = [kw for kw, pattern in KEYWORD_RES if pattern.search(text)]
    if SSN_RE.search(text):
        hits.append("ssn-pattern")
    if CC_RE.search(text):
        hits.append("card-number-pattern")
    return hits


def main():
    inventory = {it["path"]: it for it in json.load(open(HERE / "inventory.json"))}

    dedup_rows = list(csv.DictReader(open(HERE / "dedup_report.csv", newline="")))
    reps = [r for r in dedup_rows if r["is_representative"] == "True" and r["dup_type"] != "ERROR"]
    dup_rows_by_group = {}
    for r in dedup_rows:
        if r["is_representative"] == "False":
            dup_rows_by_group.setdefault(r["group_id"], []).append(r)

    ocr = {}
    for line in open(HERE / "ocr_results.jsonl"):
        rec = json.loads(line)
        ocr[rec["path"]] = rec

    records = []
    for r in reps:
        path = r["path"]
        inv = inventory.get(path, {})
        ocr_rec = ocr.get(path, {})
        text_lines = ocr_rec.get("text", [])
        text = "\n".join(text_lines)
        dups = dup_rows_by_group.get(r["group_id"], [])
        dup_size_total = sum(inventory.get(d["path"], {}).get("size", 0) for d in dups)
        records.append({
            "path": path,
            "size": inv.get("size", 0),
            "mtime": inv.get("mtime", 0),
            "width": inv.get("width"),
            "height": inv.get("height"),
            "ocr_error": ocr_rec.get("error"),
            "text": text,
            "keyword_hits": find_keyword_hits(text) if text else [],
            "dup_paths": [d["path"] for d in dups],
            "dup_size_total": dup_size_total,
        })

    records.sort(key=lambda x: x["mtime"], reverse=True)

    total_dup_files = sum(len(r["dup_paths"]) for r in records)
    total_dup_size = sum(r["dup_size_total"] for r in records)
    flagged = [r for r in records if r["keyword_hits"]]
    broken = [r for r in records if r["ocr_error"]]
    clean = [r for r in records if not r["keyword_hits"] and not r["ocr_error"]]

    def row_html(r, show_dups=False):
        thumb = (f'<a href="{file_uri(r["path"])}" target="_blank">'
                 f'<img src="{file_uri(r["path"])}" loading="lazy" class="thumb"></a>')
        dims = f'{r["width"]}x{r["height"]}' if r["width"] else "?"
        kw = ", ".join(r["keyword_hits"]) if r["keyword_hits"] else ""
        text_preview = (r["text"][:300] + "…") if len(r["text"]) > 300 else r["text"]
        dup_html = ""
        if show_dups and r["dup_paths"]:
            items = "".join(
                f'<li><a href="{file_uri(p)}">{Path(p).name}</a> ({fmt_size(inventory.get(p, {}).get("size", 0))})</li>'
                for p in r["dup_paths"]
            )
            dup_html = f'<details><summary>{len(r["dup_paths"])} duplicate(s), {fmt_size(r["dup_size_total"])}</summary><ul>{items}</ul></details>'
        return f"""<tr>
            <td>{thumb}</td>
            <td class="path"><a href="{file_uri(r['path'])}">{Path(r['path']).name}</a><br><span class="dim">{Path(r['path']).parent}</span></td>
            <td>{fmt_date(r['mtime'])}</td>
            <td>{dims}</td>
            <td>{fmt_size(r['size'])}</td>
            <td class="kw">{kw}</td>
            <td class="ocr">{text_preview}</td>
            <td>{dup_html}</td>
        </tr>"""

    def section(title, desc, rows, show_dups=False):
        if not rows:
            return f"<h2>{title}</h2><p class='desc'>{desc}</p><p><em>none</em></p>"
        body = "\n".join(row_html(r, show_dups) for r in rows)
        return f"""<h2>{title} <span class="count">({len(rows)})</span></h2>
        <p class="desc">{desc}</p>
        <table>
        <thead><tr><th></th><th>File</th><th>Date</th><th>Size (px)</th><th>Bytes</th><th>Flags</th><th>OCR text</th><th>Duplicates</th></tr></thead>
        <tbody>{body}</tbody>
        </table>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Screenshot Triage Report</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 24px; background: #fafafa; color: #222; }}
  h1 {{ margin-bottom: 4px; }}
  .summary {{ background: #fff; border: 1px solid #ddd; border-radius: 8px; padding: 16px; margin-bottom: 24px; }}
  .summary b {{ font-size: 1.1em; }}
  h2 {{ margin-top: 36px; border-bottom: 2px solid #ccc; padding-bottom: 4px; }}
  .count {{ color: #888; font-weight: normal; }}
  .desc {{ color: #555; font-size: 0.9em; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; }}
  th, td {{ border-bottom: 1px solid #eee; padding: 6px 8px; text-align: left; vertical-align: top; font-size: 0.85em; }}
  th {{ background: #f0f0f0; position: sticky; top: 0; }}
  .thumb {{ max-width: 90px; max-height: 90px; border: 1px solid #ccc; }}
  .path {{ max-width: 260px; word-break: break-word; }}
  .dim {{ color: #999; font-size: 0.85em; }}
  .kw {{ color: #b00; font-weight: bold; max-width: 140px; }}
  .ocr {{ max-width: 340px; white-space: pre-wrap; color: #444; }}
  #filter {{ padding: 8px; width: 100%; max-width: 400px; margin-bottom: 12px; font-size: 1em; }}
</style>
</head><body>
<h1>Screenshot Triage Report</h1>
<div class="summary">
  <b>{len(records)}</b> unique screenshots reviewed &middot;
  <b>{total_dup_files}</b> exact/near-duplicate files found (<b>{fmt_size(total_dup_size)}</b> reclaimable by removing them) &middot;
  <b>{len(flagged)}</b> flagged for possibly sensitive/important content &middot;
  <b>{len(broken)}</b> unreadable/corrupt
  <br><br>
  Nothing has been deleted. This report is local only -- links open the real files on disk.
</div>

{section("Flagged: possibly sensitive or important",
         "Matched keywords like SSN/passport/account numbers or similar patterns in the OCR text. Review before doing anything with these.",
         flagged, show_dups=True)}

{section("Unreadable / corrupt",
         "Vision couldn't read these at all (e.g. near-zero-pixel images) -- almost certainly safe to delete.",
         broken)}

<h2>Everything else <span class="count">({len(clean)})</span></h2>
<p class="desc">No keyword flags. Sorted newest first. Expand "Duplicates" per row to see (and eventually remove) exact/near-duplicate copies. Use the search box to filter by filename or OCR text.</p>
<input id="filter" type="text" placeholder="Filter by filename or text...">
<table id="cleantable">
<thead><tr><th></th><th>File</th><th>Date</th><th>Size (px)</th><th>Bytes</th><th>Flags</th><th>OCR text</th><th>Duplicates</th></tr></thead>
<tbody>
{''.join(row_html(r, show_dups=True) for r in clean)}
</tbody>
</table>

<script>
document.getElementById('filter').addEventListener('input', function() {{
  const q = this.value.toLowerCase();
  document.querySelectorAll('#cleantable tbody tr').forEach(function(tr) {{
    tr.style.display = tr.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}});
</script>
</body></html>"""

    OUT_PATH.write_text(html)
    print(f"Report written to {OUT_PATH}")
    print(f"  {len(records)} unique, {total_dup_files} duplicates ({fmt_size(total_dup_size)}), "
          f"{len(flagged)} flagged, {len(broken)} broken, {len(clean)} unclassified")


if __name__ == "__main__":
    main()
