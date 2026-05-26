"""Phase 2 RAG benchmark gate.

Runs the 20-question hybrid RAG benchmark and asserts the ROADMAP exit
criteria. The LLM is selected by `benchmarks.rag.run_eval.make_llm`:

* Default: real LiteLLM via Ollama (DD-014). Local devs need a running
  Ollama daemon; the runner pings it and fails fast if absent.
* `REPOSAGE_PROFILE=mock`: explicit fallback used by CI / forked PRs
  without an Ollama box. Same thresholds still apply because the mock
  pipeline is a determinism-pinned proxy for the wiring.

The test honors both branches transparently.
"""

from __future__ import annotations

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
from reposage.composition import current_profile


@pytest.mark.asyncio
async def test_rag_bench_meets_phase2_thresholds() -> None:
    # `async def` (not `asyncio.run`) so pytest-asyncio owns the event loop:
    # LiteLLM's global LoggingWorker binds to this loop, our `await llm.aclose()`
    # drains it in `finally`, and pytest-asyncio's `run_until_complete` tears
    # the loop down without the orphan-coroutine warning that `asyncio.run`
    # produces. (See `LiteLLMClient.aclose` for the full diagnosis.)
    is_mock = current_profile() == "mock"
    try:
        llm = make_llm()
    except OllamaUnavailableError as exc:
        if is_mock:
            raise  # mock branch should never raise; bug if it does
        pytest.skip(str(exc))

    questions = load_questions(Path(DEFAULT_QUESTIONS))

    try:
        results = await run_eval(
            questions=questions,
            repo=Path(DEFAULT_FIXTURE),
            repo_name="tiny",
            llm=llm,
            top_k=8,
        )
    finally:
        await llm.aclose()

    summary = summarise(results)

    # P50 budget. The 1.5 s ROADMAP target is for the retrieval+grounding
    # path — measured cleanly under the mock LLM. With a CPU-bound Ollama
    # model the LLM call alone takes seconds, so we relax to a coarse
    # ceiling that still catches obviously-wedged daemons (DD-014).
    p50_budget = 1500 if is_mock else 60_000
    assert summary["p50_ms"] < p50_budget, summary

    # Citation legality threshold mirrors `benchmarks.rag.run_eval.main`:
    # the mock pipeline is deterministic so we require 1.0, while a real
    # local LLM (7B-class) is allowed a small fraction of unrecoverable
    # hallucinations after the regenerate-and-strip pass (DD-013). Keeping
    # the test in lockstep with the runner avoids the "test asserts 1.0,
    # runner asserts 0.90" inconsistency that hid real grounder bugs.
    citation_threshold = 1.0 if is_mock else 0.90
    assert summary["citation_legal_rate"] >= citation_threshold, summary

    # File-level recall threshold: under the mock pipeline this is a hard
    # 80 %. Under a real LLM, retrieval is unchanged — the LLM is downstream
    # of recall — so the same bar applies.
    assert summary["recall_at_k"] >= 0.80, summary
