"""Mixed-modality benchmark: text-only vs the figure->VLM path on InfographicVQA.

Infographics pack their answers into layout, charts, and spatial relationships that survive
poorly in raw OCR text. This benchmark quantifies exactly how much the visual path recovers:

    TEXT-ONLY  = the infographic's OCR text  -> LLM -> answer
    VISUAL     = the infographic image       -> VLM -> answer   (prismdoc's figure->VLM path)

The gap (VISUAL - TEXT-ONLY) is what routing a figure region to a VLM buys you over reading its
extracted text alone -- the core reason prismdoc's ``figures.*`` sub-pipeline exists.

Data: InfographicVQA (validation split, with ground-truth answers) streamed row-by-row from the
Hugging Face datasets-server -- no full dataset download, no RRC registration. Each row carries the
image, the question, the gold answers, and the dataset's own OCR (Amazon Textract) which we use for
the text-only baseline so the comparison is fair (text-only sees *all* the text, just not the layout).

Scoring is a **relaxed** normalized match (lowercase, alphanumeric, gold-in-prediction), not the
official ANLS -- it is a coarse readout of the gap, not a leaderboard number.

The VLM/LLM is any callable that maps ``(prompt, image_path|None) -> str``. The default shells out to
a multimodal CLI; wire ``litellm`` (as the live pipeline does) by passing a custom ``answer_fn``.

Run::

    python -m prismdoc.bench.infovqa --n 80 --out /tmp/infovqa --workers 6

Resumable: results are cached per question id under ``--out``; re-run to continue an interrupted pass.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import ssl
import subprocess
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

DATASET = "lmms-lab%2FDocVQA"
CONFIG = "InfographicVQA"
SPLIT = "validation"
_CTX = ssl._create_unverified_context()

AnswerFn = Callable[[str, "str | None"], str]


def _http_json(url: str, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "prismdoc-bench"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout, context=_CTX).read())


def _http_bytes(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "prismdoc-bench"})
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()


def ocr_text(field: str) -> str:
    """Extract plain LINE text from the dataset's Textract OCR field.

    The field is a Python-list-literal string wrapping one JSON blob whose top-level keys are
    ``PAGE`` / ``LINE`` / ``WORD`` (each a list of blocks); the readable text is the LINE blocks.
    """
    try:
        blob = ast.literal_eval(field)[0]
        j = json.loads(blob)
        return " ".join(b["Text"] for b in j.get("LINE", []) if "Text" in b)
    except Exception:
        return ""


def fetch_rows(n: int) -> list[dict]:
    """Stream the first ``n`` validation rows (image URL, question, gold answers, OCR text)."""
    out: list[dict] = []
    for off in range(0, n, 100):
        length = min(100, n - off)
        url = (
            f"https://datasets-server.huggingface.co/rows?dataset={DATASET}"
            f"&config={CONFIG}&split={SPLIT}&offset={off}&length={length}"
        )
        for it in _http_json(url)["rows"]:
            r = it["row"]
            out.append(
                {
                    "id": r["questionId"],
                    "q": r["question"],
                    "gt": r["answers"],
                    "img": r["image"]["src"],
                    "ocr": ocr_text(r["ocr"]),
                }
            )
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(s).lower()).strip()


def is_correct(pred: str, golds: list[str]) -> bool:
    """Relaxed match: a normalized gold answer equals or is contained in the normalized prediction."""
    p = _norm(pred)
    return any(_norm(g) == p or (_norm(g) and _norm(g) in p) for g in golds)


def cli_answer_fn(model_cmd: list[str]) -> AnswerFn:
    """Build an ``answer_fn`` that shells out to a multimodal CLI (``... "<prompt> @<image>"``)."""

    def answer(prompt: str, image: str | None) -> str:
        args = [*model_cmd, prompt + (f" @{image}" if image else "")]
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=120).stdout.strip()
        except Exception:
            return ""

    return answer


def _evaluate_row(row: dict, image_dir: Path, answer_fn: AnswerFn) -> dict:
    ip = image_dir / f"{row['id']}.jpg"
    if not ip.exists():
        try:
            ip.write_bytes(_http_bytes(row["img"]))
        except Exception as e:  # noqa: BLE001
            return {"id": row["id"], "err": str(e)[:80]}
    text_ans = answer_fn(
        "Using ONLY this OCR text from an infographic, answer with as few words as possible "
        f"(just the answer). OCR: {row['ocr'][:7000]}\nQuestion: {row['q']}\nAnswer:",
        None,
    )
    vlm_ans = answer_fn(
        "Answer this question about the infographic image with as few words as possible "
        f"(just the answer).\nQuestion: {row['q']}\nAnswer:",
        str(ip),
    )
    return {
        "id": row["id"],
        "q": row["q"],
        "gt": row["gt"],
        "text_ok": is_correct(text_ans, row["gt"]),
        "vlm_ok": is_correct(vlm_ans, row["gt"]),
        "text_ans": text_ans[:80],
        "vlm_ans": vlm_ans[:80],
    }


def run(n: int, out: Path, answer_fn: AnswerFn, workers: int = 6) -> dict:
    """Evaluate ``n`` infographics; cache per-id under ``out``; return the aggregate summary."""
    out.mkdir(parents=True, exist_ok=True)
    image_dir = out / "images"
    image_dir.mkdir(exist_ok=True)
    cache = out / "results.jsonl"

    done: dict[int, dict] = {}
    if cache.exists():
        for line in cache.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                done[rec["id"]] = rec

    rows = fetch_rows(n)[:n]
    todo = [r for r in rows if r["id"] not in done]
    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_evaluate_row, r, image_dir, answer_fn) for r in todo}
        for fut in as_completed(futures):
            rec = fut.result()
            with lock:
                with cache.open("a") as fh:
                    fh.write(json.dumps(rec) + "\n")
                done[rec["id"]] = rec

    scored = [done[r["id"]] for r in rows if "text_ok" in done.get(r["id"], {})]
    n_scored = len(scored)
    text_ok = sum(x["text_ok"] for x in scored)
    vlm_ok = sum(x["vlm_ok"] for x in scored)
    return {
        "n": n_scored,
        "text_only_acc": text_ok / n_scored if n_scored else 0.0,
        "visual_acc": vlm_ok / n_scored if n_scored else 0.0,
        "gap_points": (vlm_ok - text_ok) / n_scored * 100 if n_scored else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="InfographicVQA mixed-modality benchmark (text vs figure->VLM)")
    ap.add_argument("--n", type=int, default=80, help="number of infographics")
    ap.add_argument("--out", type=Path, required=True, help="output/cache directory")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument(
        "--model-cmd",
        default="claude -p --dangerously-skip-permissions",
        help="multimodal CLI invoked as '<cmd> \"<prompt> @<image>\"' (default: Claude CLI)",
    )
    args = ap.parse_args()

    summary = run(args.n, args.out, cli_answer_fn(args.model_cmd.split()), args.workers)
    print(f"\n===== InfographicVQA ({SPLIT}, n={summary['n']}) — relaxed match =====")
    print(f"TEXT-ONLY (OCR -> LLM):   {summary['text_only_acc']:.3f}")
    print(f"VISUAL    (figure -> VLM): {summary['visual_acc']:.3f}")
    print(f"GAP recovered by the figure->VLM path: {summary['gap_points']:.1f} points")


if __name__ == "__main__":
    main()
