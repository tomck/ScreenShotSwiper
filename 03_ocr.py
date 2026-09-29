#!/usr/bin/env python3
"""
Run Vision framework OCR on every representative (non-duplicate) file
from dedup_report.csv. Single long-running process (spawning a fresh
process per image reintroduces the slow per-resolution model-compile
cost). A per-image SIGALRM timeout is a best-effort safety net against
a pathological hang, but the real expectation is this just takes hours.

Resumable: paths already in ocr_results.jsonl are skipped on rerun.
"""
import csv
import json
import signal
import time
from pathlib import Path
from Vision import VNImageRequestHandler, VNRecognizeTextRequest
from Foundation import NSURL

HERE = Path(__file__).parent
REPORT = HERE / "dedup_report.csv"
OUT_PATH = HERE / "ocr_results.jsonl"
PER_IMAGE_TIMEOUT = 120


class TimeoutError_(Exception):
    pass


def _alarm_handler(signum, frame):
    raise TimeoutError_()


def ocr(path):
    url = NSURL.fileURLWithPath_(path)
    handler = VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    request = VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(1)
    success, error = handler.performRequests_error_([request], None)
    if not success:
        raise RuntimeError(str(error))
    return [obs.topCandidates_(1)[0].string() for obs in request.results() if obs.topCandidates_(1)]


def load_done():
    done = set()
    if OUT_PATH.exists():
        with open(OUT_PATH) as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(json.loads(line)["path"])
    return done


def main():
    reps = []
    with open(REPORT, newline="") as f:
        for row in csv.DictReader(f):
            if row["is_representative"] == "True" and row["dup_type"] != "ERROR":
                reps.append(row["path"])

    done = load_done()
    todo = [p for p in reps if p not in done]
    print(f"{len(reps)} representative files total, {len(done)} already OCR'd, {len(todo)} remaining.", flush=True)

    signal.signal(signal.SIGALRM, _alarm_handler)
    out = open(OUT_PATH, "a")
    start = time.time()
    for i, path in enumerate(todo, 1):
        t0 = time.time()
        signal.alarm(PER_IMAGE_TIMEOUT)
        try:
            text_lines = ocr(path)
            record = {"path": path, "text": text_lines}
        except TimeoutError_:
            record = {"path": path, "error": f"timed out after {PER_IMAGE_TIMEOUT}s"}
        except Exception as e:
            record = {"path": path, "error": str(e)}
        finally:
            signal.alarm(0)
        out.write(json.dumps(record) + "\n")
        out.flush()
        elapsed_total = time.time() - start
        avg = elapsed_total / i
        remaining = (len(todo) - i) * avg
        status = "ERROR: " + record["error"] if "error" in record else f"{len(record['text'])} lines"
        print(f"[{i}/{len(todo)}] {time.time()-t0:.1f}s ({status}) "
              f"-- ~{remaining/60:.0f} min remaining -- {path}", flush=True)
    out.close()
    print("\nOCR pass complete.", flush=True)


if __name__ == "__main__":
    main()
