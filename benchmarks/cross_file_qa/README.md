# Cross-File QA Benchmark

A self-built evaluation set of **200 hand-curated cross-file questions** spanning three open-source repositories (one Python, one TypeScript, one Go). It exists because the publicly available code-QA benchmarks (CodeSearchNet, HumanEval, etc.) are dominated by single-file or function-level questions and do not stress *cross-file reasoning*.

## Question taxonomy

| Bucket | Examples | n | Targeted index |
| --- | --- | --- | --- |
| **Deterministic graph** | "Where is `User.login` called?" / "Which classes inherit from `BaseAuth`?" | 80 | Symbol Graph |
| **Module aggregation** | "How do the auth and billing modules interact?" | 40 | GraphRAG comm. |
| **Semantic / mixed** | "How is the session timeout configured?" | 60 | Hybrid + reranker |
| **Negative / fail-safe** | Questions that cannot be answered by the repo | 20 | Router → "I don't know" |

## Scoring

Three independent metrics, all logged to `results/<timestamp>.csv`:

1. **Citation grounding** — every cited file:line range must exist in the repo HEAD; computed exactly, no LLM in the loop.
2. **Answer correctness** — Ragas `answer_correctness` against reference answers, judged by an evaluator LLM held out from the answer model.
3. **Answer faithfulness** — Ragas `faithfulness` against the retrieved context; this catches hallucinations that happen to match the reference.

## Reproducing a run

```bash
make bench-qa            # uses the .env LLM provider
# or
python -m benchmarks.cross_file_qa.run_eval --repo-set defaults --route auto
```

## Status

* Phase 0 — scaffolding only (this README + an empty `questions.jsonl`).
* Phase 3 — first 50 questions land alongside the GraphRAG implementation.
* Phase 5 — full 200 questions; results published in `docs/BENCHMARKS.md`.
* Phase 6 — wired into CI as a regression gate (≥ baseline accuracy required to merge).
