"""Run the cross-file QA benchmark and emit a CSV row per question.

Usage::

    python -m benchmarks.cross_file_qa.run_eval --repo-set defaults --route auto

This is a stub harness; real LLM calls land in Phase 3.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

QUESTIONS_PATH = Path(__file__).parent / "questions.jsonl"


def load_questions(path: Path = QUESTIONS_PATH) -> list[dict]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Cross-file QA benchmark")
    parser.add_argument("--repo-set", default="defaults", help="Named repo set to evaluate against.")
    parser.add_argument("--route", default="auto", choices=["auto", "graph", "community", "hybrid"])
    parser.add_argument("--out", type=Path, default=Path("benchmarks/cross_file_qa/results/latest.csv"))
    args = parser.parse_args()

    questions = load_questions()
    print(f"Loaded {len(questions)} questions; repo_set={args.repo_set} route={args.route}")
    print(f"(stub) results would be written to {args.out}")


if __name__ == "__main__":
    main()
