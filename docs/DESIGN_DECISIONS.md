# Design Decisions

Each entry records an irreversible-ish choice, the alternatives we rejected, and the cost we accept by choosing this path. New entries are appended; nothing is rewritten in place.

## DD-001: Self-built HNSW in Go (instead of `hnswlib` / Faiss)

* **Choice**: implement HNSW from scratch in Go (`go-hnsw/`), exposed to the Python service over gRPC.
* **Alternatives**: `pip install hnswlib`; Faiss as a Python dep; Qdrant or Weaviate as an external service.
* **Why**: a transparent, in-house index lets us instrument every knob and persist with our own mmap-friendly format. The serving binary then has *no* native dep we did not write.
* **Cost we accept**: 1.5–2 weeks of implementation effort and an explicit *N×* gap to Faiss at the same recall. We do not try to "win" the benchmark; we publish the Pareto curve and the explanation.
* **Reversal cost**: low. The Python side talks gRPC, so swapping the backend is a one-day change.

## DD-002: Dual-index retrieval (Symbol Graph + GraphRAG + Hybrid)

* **Choice**: route each question to one of three indexes — deterministic graph adjacency, GraphRAG community summaries, or hybrid vector + BM25.
* **Alternatives**: pure vector RAG; pure BM25; pure graph.
* **Why**: real questions about a code repository fall into qualitatively different shapes. *"Where is `User.login` called?"* has a single correct answer that lives in the graph; *"how do auth and billing interact?"* needs module-level aggregation; *"how is the session timeout configured?"* is a generic semantic search. Forcing all three into one mechanism degrades all three.
* **Cost we accept**: indexing time ~3–5× a vector-only baseline; two more stores to maintain (SQLite for the graph + a community table); a small router LLM call per question.
* **Reversal cost**: medium. Removing the graph or the community path is straightforward; collapsing all three into "just hybrid" loses the differentiator.

## DD-003: Communities on call topology, not file directories

* **Choice**: run Leiden on the symbol graph itself; do not bias by directory layout.
* **Alternatives**: bias edges that cross folder boundaries; pre-cluster by directory and refine.
* **Why**: file layout is a human convenience that is often wrong about logical modules — a payments helper sitting in `utils/` is part of the billing module, not the utils module. Letting the call graph speak directly produces communities that match how engineers actually think about the system.
* **Cost we accept**: communities are slightly less stable across versions because Leiden is non-deterministic; we mitigate with a fixed seed and report partition stability when we re-index.
* **Reversal cost**: low. The detector is a single class.

## DD-004: SQLite for the symbol graph (instead of Neo4j or DuckDB)

* **Choice**: SQLite with adjacency tables and covering indexes on `(dst, kind)` and `(src, kind)`.
* **Alternatives**: Neo4j, DuckDB, an in-process graph library only.
* **Why**: the symbol graph is sparse and our queries are 1–2 hops. Adjacency tables serve those access patterns at native-SQL speed and ship as a single file. Neo4j adds a daemon for capabilities we do not need; DuckDB does not have a query layer for graph traversal that beats SQLite for this workload.
* **Cost we accept**: complex multi-hop graph algorithms (PageRank-style queries, transitive closures) are awkward in SQL. We do not currently need them.
* **Reversal cost**: medium. The store interface is one file; the schema is the harder thing to migrate.

## DD-005: GitHub App as the primary interface

* **Choice**: ship as a GitHub App that listens on `issue_comment` and `push`.
* **Alternatives**: a web app where users upload a repo; a CLI-only tool; a VS Code extension.
* **Why**: the "install on a public OSS repo, comment on a real PR" demo is a very low-friction way to evaluate quality. There is no upload, no auth dance, no model run before the user sees a real answer. It also matches how every modern code-intelligence tool we benchmark against ships.
* **Cost we accept**: GitHub App lifecycle complexity (JWT minting, installation tokens, rate limits, webhook signature verification). We isolate this in `reposage/bot/`.
* **Reversal cost**: low. Removing the App leaves a perfectly good HTTP API.

## DD-006: RRF over weighted-sum fusion

* **Choice**: fuse HNSW and BM25 rankings with Reciprocal Rank Fusion.
* **Alternatives**: weighted sum of normalised scores.
* **Why**: BM25 scores are unbounded and corpus-dependent; cosine similarities live in a tight range. Any normalisation we pick (min-max, z-score) is sensitive to outliers. RRF only looks at ranks, so it is invariant to score scales and works out of the box.
* **Cost we accept**: RRF cannot express "BM25 is more confident here" — it weights branches equally. In practice this is fine; the reranker recovers the lost calibration.
* **Reversal cost**: trivial. One function in `reposage/retrieval/hybrid.py`.

## DD-007: LiteLLM for multi-provider LLM routing

* **Choice**: all LLM calls go through LiteLLM.
* **Alternatives**: native Anthropic / OpenAI SDKs; LangChain.
* **Why**: model swaps are routine — the answering model and the router model are different, and the cheaper summariser model is different again. LiteLLM lets us configure each via a single string and keep our prompt code provider-agnostic.
* **Cost we accept**: one more dependency, occasional shim lag for new model features.
* **Reversal cost**: medium-low. `reposage/llm/client.py` is the one place to change.

## DD-008: Phase-by-phase delivery (see `ROADMAP.md`)

* **Choice**: every phase ends with a demoable artefact and a CI signal.
* **Alternatives**: build the whole indexing pipeline first, then layer retrieval, then bot.
* **Why**: building "all the indexing then all the serving" defers the integration risks. Each phase here is small enough to keep the repo green and big enough to publish a measurable result.
* **Cost we accept**: some up-front scaffolding cost (Phase 0).
* **Reversal cost**: n/a — this is process, not architecture.
