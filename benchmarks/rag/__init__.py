"""Phase 2 hybrid-RAG benchmark.

Twenty questions over the bundled `tiny_python_repo` fixture (with the
option of swapping in a 50 kLOC repo via `REPOSAGE_LARGE_REPO`).

Three numbers come out:
* file-level `recall@5` — the gold answer set is a list of paths; we count
  hits in the top-5 chunks returned by the hybrid retriever.
* citation legality rate — fraction of LLM answers whose every citation
  matches a retrieved chunk (must be 100%; the grounder enforces it).
* P50 / P95 wall time — the question budget is < 1500 ms P50.
"""
