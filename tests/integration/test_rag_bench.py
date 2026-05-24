"""Phase 2 RAG benchmark gate.

Runs the 20-question hybrid RAG benchmark and asserts the ROADMAP exit
criteria. The LLM is selected by `benchmarks.rag.run_eval.make_llm`:

* Default: real LiteLLM via Ollama (DD-014). Local devs need a running
  Ollama daemon; the runner pings it and fails fast if absent.
* `REPOSAGE_RAG_LLM=mock`: explicit fallback used by CI / forked PRs
  without an Ollama box. Same thresholds still apply because the mock
  pipeline is a determinism-pinned proxy for the wiring.

The test honors both branches transparently.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from benchmarks.rag.run_eval import (
    DEFAULT_FIXTURE,
    DEFAULT_QUESTIONS,
    OllamaUnavailableError,
    load_questions,
    make_llm,
    run_eval,
    summarise,
)


def test_rag_bench_meets_phase2_thresholds() -> None:
    try:
        llm = make_llm()
    except OllamaUnavailableError as exc:
        if os.environ.get("REPOSAGE_RAG_LLM", "").lower() == "mock":
            raise  # mock branch should never raise; bug if it does
        pytest.skip(str(exc))

    is_mock = os.environ.get("REPOSAGE_RAG_LLM", "").lower() == "mock"

    questions = load_questions(Path(DEFAULT_QUESTIONS))
    results = asyncio.run(
        run_eval(
            questions=questions,
            repo=Path(DEFAULT_FIXTURE),
            repo_name="tiny",
            llm=llm,
            top_k=8,
        )
    )
    summary = summarise(results)

    # P50 budget. The 1.5 s ROADMAP target is for the retrieval+grounding
    # path — measured cleanly under the mock LLM. With a CPU-bound Ollama
    # model the LLM call alone takes seconds, so we relax to a coarse
    # ceiling that still catches obviously-wedged daemons (DD-014).
    p50_budget = 1500 if is_mock else 60_000
    assert summary["p50_ms"] < p50_budget, summary

    # Citation legality is provider-agnostic: the grounder must never let
    # a fabricated citation slip through, mock or not.
    assert summary["citation_legal_rate"] == 1.0, summary

    # File-level recall threshold: under the mock pipeline this is a hard
    # 80 %. Under a real LLM, retrieval is unchanged — the LLM is downstream
    # of recall — so the same bar applies.
    assert summary["recall_at_k"] >= 0.80, summary
