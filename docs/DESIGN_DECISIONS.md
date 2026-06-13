# Design Decisions

Each entry records an irreversible-ish choice, the alternatives we rejected, and the cost we accept by choosing this path. New entries are appended; nothing is rewritten in place.

## How to use this file

This document is **architectural reference**, not a changelog. Readers should be able to open it and answer "what is the current state of X" without reading every entry chronologically.

Each DD ships four lines of metadata after its heading:

- **Status**: one of `active` (current, may still evolve), `implemented` (landed, stable, unlikely to change), `superseded` (replaced — must fill `Superseded-by`), or `proposed` (debated, not yet built).
- **Phase**: the roadmap phase that introduced or owns the decision.
- **Last reviewed**: most recent date the body was re-read against the code.
- **Superseded-by**: `—` unless the status is `superseded`.

When a new decision changes an existing one:

1. Add a new `DD-NNN`; in the body, write `Supersedes: DD-XXX`.
2. Flip the old DD's `Status` to `superseded` and fill its `Superseded-by`.
3. Update the **Current architecture index** below so readers can spot the current shape in one glance.

## Current architecture index

The single-line view of the decisions that describe the system as it is **today**. Anything not listed here is either superseded (search by id) or still proposed.

- DD-001 Self-built Go HNSW — implemented (Phase 2 / 4)
- DD-002 Dual-index retrieval (graph + community + hybrid) — active (Phase 2/3)
- DD-003 Communities on call topology, not directories — active (Phase 3)
- DD-004 SQLite for the symbol graph — implemented (Phase 1)
- DD-006 RRF over weighted-sum fusion — implemented (Phase 2)
- DD-007 LiteLLM for multi-provider LLM routing — active (Phase 2+)
- DD-009 Phase 1 forward-compatible schema — implemented (Phase 1)
- DD-010 TS/JS/Go parse-validated, not extracted — implemented (Phase 1)
- DD-011 Embeddings in SQLite as float32 BLOBs — implemented (Phase 2)
- DD-012 Retrieval seams are `Protocol`s — implemented (Phase 2)
- DD-013 Citation grounding fails closed (one regenerate) — active (Phase 2/3)
- DD-014 Local Ollama is the default LLM provider — active (Phase 2)
- DD-015 Leiden on `call` + `inherit` only — implemented (Phase 3)
- DD-016 Smaller LLM for community summaries — active (Phase 3)
- DD-017 Drop isolated symbols from Leiden subgraph — implemented (Phase 3)
- DD-018 Densify membership before contracting — implemented (Phase 3)
- DD-019 Community-route defaults favour breadth — active (Phase 3)
- DD-024 Single composition root + `REPOSAGE_PROFILE` — active (Phase 3.5 refactor)
- DD-025 `AnswerResult.outcome: RouteOutcome` replaces `route: str` — active (Phase 3.5 refactor)
- DD-026 mmap snapshot + columnar/lazy ids — implemented (Phase 4)
- DD-027 Algorithm 4 heuristic neighbour selection — implemented (Phase 4)
- DD-028 Atomic snapshot writes (tmp + fsync + rename) — implemented (Phase 4)
- DD-029 Snapshot via server lifecycle, not a gRPC RPC (for now) — active (Phase 4)

## DD-001: Self-built HNSW in Go (instead of `hnswlib` / Faiss)

- **Status**: implemented
- **Phase**: 2 (Go server) / 4 (mmap arena)
- **Last reviewed**: 2026-06-12
- **Superseded-by**: —

* **Choice**: implement HNSW from scratch in Go (`go-hnsw/`), exposed to the Python service over gRPC.
* **Alternatives**: `pip install hnswlib`; Faiss as a Python dep; Qdrant or Weaviate as an external service.
* **Why**: a transparent, in-house index lets us instrument every knob and persist with our own mmap-friendly format. The serving binary then has *no* native dep we did not write.
* **Cost we accept**: 1.5–2 weeks of implementation effort and an explicit *N×* gap to Faiss at the same recall. We do not try to "win" the benchmark; we publish the Pareto curve and the explanation.
* **Reversal cost**: low. The Python side talks gRPC, so swapping the backend is a one-day change.

## DD-002: Dual-index retrieval (Symbol Graph + GraphRAG + Hybrid)

- **Status**: active
- **Phase**: 2 (hybrid + graph) / 3 (community)
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: route each question to one of three indexes — deterministic graph adjacency, GraphRAG community summaries, or hybrid vector + BM25.
* **Alternatives**: pure vector RAG; pure BM25; pure graph.
* **Why**: real questions about a code repository fall into qualitatively different shapes. *"Where is `User.login` called?"* has a single correct answer that lives in the graph; *"how do auth and billing interact?"* needs module-level aggregation; *"how is the session timeout configured?"* is a generic semantic search. Forcing all three into one mechanism degrades all three.
* **Cost we accept**: indexing time ~3–5× a vector-only baseline; two more stores to maintain (SQLite for the graph + a community table); a small router LLM call per question.
* **Reversal cost**: medium. Removing the graph or the community path is straightforward; collapsing all three into "just hybrid" loses the differentiator.

## DD-003: Communities on call topology, not file directories

- **Status**: active
- **Phase**: 3
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: run Leiden on the symbol graph itself; do not bias by directory layout.
* **Alternatives**: bias edges that cross folder boundaries; pre-cluster by directory and refine.
* **Why**: file layout is a human convenience that is often wrong about logical modules — a payments helper sitting in `utils/` is part of the billing module, not the utils module. Letting the call graph speak directly produces communities that match how engineers actually think about the system.
* **Cost we accept**: communities are slightly less stable across versions because Leiden is non-deterministic; we mitigate with a fixed seed and report partition stability when we re-index.
* **Reversal cost**: low. The detector is a single class.

## DD-004: SQLite for the symbol graph (instead of Neo4j or DuckDB)

- **Status**: implemented
- **Phase**: 1
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: SQLite with adjacency tables and covering indexes on `(dst, kind)` and `(src, kind)`.
* **Alternatives**: Neo4j, DuckDB, an in-process graph library only.
* **Why**: the symbol graph is sparse and our queries are 1–2 hops. Adjacency tables serve those access patterns at native-SQL speed and ship as a single file. Neo4j adds a daemon for capabilities we do not need; DuckDB does not have a query layer for graph traversal that beats SQLite for this workload.
* **Cost we accept**: complex multi-hop graph algorithms (PageRank-style queries, transitive closures) are awkward in SQL. We do not currently need them.
* **Reversal cost**: medium. The store interface is one file; the schema is the harder thing to migrate.

## DD-005: GitHub App as the primary interface

- **Status**: proposed
- **Phase**: 4
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: ship as a GitHub App that listens on `issue_comment` and `push`.
* **Alternatives**: a web app where users upload a repo; a CLI-only tool; a VS Code extension.
* **Why**: the "install on a public OSS repo, comment on a real PR" demo is a very low-friction way to evaluate quality. There is no upload, no auth dance, no model run before the user sees a real answer. It also matches how every modern code-intelligence tool we benchmark against ships.
* **Cost we accept**: GitHub App lifecycle complexity (JWT minting, installation tokens, rate limits, webhook signature verification). We isolate this in `reposage/bot/`.
* **Reversal cost**: low. Removing the App leaves a perfectly good HTTP API.

## DD-006: RRF over weighted-sum fusion

- **Status**: implemented
- **Phase**: 2
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: fuse HNSW and BM25 rankings with Reciprocal Rank Fusion.
* **Alternatives**: weighted sum of normalised scores.
* **Why**: BM25 scores are unbounded and corpus-dependent; cosine similarities live in a tight range. Any normalisation we pick (min-max, z-score) is sensitive to outliers. RRF only looks at ranks, so it is invariant to score scales and works out of the box.
* **Cost we accept**: RRF cannot express "BM25 is more confident here" — it weights branches equally. In practice this is fine; the reranker recovers the lost calibration.
* **Reversal cost**: trivial. One function in `reposage/retrieval/hybrid.py`.

## DD-007: LiteLLM for multi-provider LLM routing

- **Status**: active
- **Phase**: 2 (introduced) / 3 (community summariser)
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: all LLM calls go through LiteLLM.
* **Alternatives**: native Anthropic / OpenAI SDKs; LangChain.
* **Why**: model swaps are routine — the answering model and the router model are different, and the cheaper summariser model is different again. LiteLLM lets us configure each via a single string and keep our prompt code provider-agnostic.
* **Cost we accept**: one more dependency, occasional shim lag for new model features.
* **Reversal cost**: medium-low. `reposage/llm/client.py` is the one place to change.

## DD-008: Phase-by-phase delivery (see `ROADMAP.md`)

- **Status**: active
- **Phase**: process (cross-cutting)
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: every phase ends with a demoable artefact and a CI signal.
* **Alternatives**: build the whole indexing pipeline first, then layer retrieval, then bot.
* **Why**: building "all the indexing then all the serving" defers the integration risks. Each phase here is small enough to keep the repo green and big enough to publish a measurable result.
* **Cost we accept**: some up-front scaffolding cost (Phase 0).
* **Reversal cost**: n/a — this is process, not architecture.

## DD-009: Phase 1 ships forward-compatible schema and module-aware Python resolver

- **Status**: implemented
- **Phase**: 1
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: even though the roadmap entry for Phase 1 only requires `nodes` + `edges`, the actual implementation also lands `chunks`, `repo_meta`, `file_meta`, and `edges.weight` — and the Python resolver does two-pass module-aware resolution (imports, `self.X`, `cls.X`).
* **Alternatives**: ship the bare minimum and migrate in Phase 2 / 3 / 7 when each table is needed.
* **Why**:
  - Phase 2 will key HNSW vectors on `chunks.chunk_id`; having the table land *now* avoids a schema migration during the hottest implementation phase.
  - Phase 3 Leiden weights `(src, dst)` edge pairs — `edges.weight` is a column the writer can keep zero-cost up to date today rather than as a deferred migration.
  - Phase 7 incremental indexing is gated on per-file `file_sha` / `mtime`. Writing those rows now is two extra columns and one extra `UPSERT` per file; the downstream phase becomes a one-line read.
  - Module-aware resolution is the published water-line for Sourcegraph's `scip-python`. Shipping the simpler "file-local" resolver would have forced us to re-extract Phase 1 outputs once GraphRAG is added.
* **Cost we accept**: ~25% extra Phase 1 implementation effort (storage + resolver) and an additional unit-test surface to maintain.
* **Reversal cost**: low. The new tables are independent — dropping them is a single `DROP TABLE`.

## DD-010: TypeScript / JavaScript / Go files are parse-validated, not extracted, in Phase 1

- **Status**: implemented
- **Phase**: 1
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: when the indexer encounters `.ts` / `.tsx` / `.js` / `.jsx` / `.go` files, it parses them with tree-sitter to confirm they're well-formed but writes only one row to `file_meta` with `parse_status='unsupported'`. No `chunks`, `nodes`, or `edges` are produced for them.
* **Alternatives**: skip them entirely (no `file_meta` row); chunk them now to seed Phase 2 BM25.
* **Why**: `parse_status` becomes the single source of truth for index coverage. Phase 4's GitHub App can answer "what does this index cover?" in one SQL query, which directly supports user-facing expectation management when we deploy on polyglot OSS repos. Skipping entirely costs that visibility; chunking now would force Phase 3 GraphRAG to handle "chunk without a node" dangling state.
* **Cost we accept**: Phase 2 hybrid retrieval is Python-only at the start of Phase 2 (TS/Go content is invisible to BM25 + HNSW until their resolvers ship).
* **Reversal cost**: low. Adding TS / Go resolvers is purely additive — Phase 1 rows for those files have a stable shape.

## DD-011: Embeddings live in SQLite as float32 BLOBs (not sidecar `.npy`)

- **Status**: implemented
- **Phase**: 2
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: Phase 2 stores chunk vectors in an `embeddings(chunk_id PK, model, dim, vector BLOB, created_at)` table inside the same `data/reposage.db` as `chunks`/`nodes`/`edges`.
* **Alternatives**: per-repo `embeddings.npy` written next to the SQLite file; a per-model directory of `.npy` shards; mmap-from-day-one (Phase 5's plan).
* **Why**:
  - **Atomicity**. Embeddings and chunks share one transaction. A crash mid-`reposage index` either leaves the new chunk-and-vector pair both committed or neither — never half a state.
  - **One backup**. The whole index is one file. `cp data/reposage.db backup.db` is a complete checkpoint for the symbol graph, communities, chunks, *and* dense vectors.
  - **Multi-model**. The `model` column lets two encoders coexist on the same `chunk_id`. Phase 7 can index a new bge variant in shadow mode, validate, then flip the default — no migration.
  - **Debugging**. `sqlite3 data/reposage.db 'SELECT chunk_id, dim FROM embeddings LIMIT 5'` is the entire forensic loop.
* **Cost we accept**: cold start of `hnsw-server` is `O(N)` reads of float32 blobs (~100 ms for 10 k vectors, ~2 s for 200 k). Phase 5 will export an mmap-friendly arena to amortise this across restarts.
* **Reversal cost**: low. The export tool that Phase 5 ships is a strict superset of `iter_vectors`; the schema is independent of the on-disk arena format.

## DD-012: Retrieval seams are `Protocol`s, with a `LocalDenseIndex` for unit tests

- **Status**: implemented
- **Phase**: 2
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `SparseRetriever`, `DenseRetriever`, `Reranker`, and `LLMClient` are `typing.Protocol`s in [`reposage/retrieval/protocols.py`](../reposage/retrieval/protocols.py). `RetrievalService` accepts any concrete implementation that satisfies the contract. A `LocalDenseIndex` (numpy linear scan) implements `DenseRetriever` for tests.
* **Alternatives**: ABCs with `@abstractmethod`; concrete classes with monkey-patched fakes.
* **Why**:
  - Unit tests must not require a Go binary, network access, or model downloads. A 10 ms numpy scan over 10 k 768-d vectors is the fastest path to coverage for the orchestration code.
  - Phase 5 mmap HNSW, Phase 7 Tantivy, and Phase 8 sharding each replace exactly one Protocol implementation. The orchestrator never sees the swap.
  - `Protocol` (vs ABC) keeps the inheritance graph flat — concrete classes don't need to register or import the protocol module to satisfy it.
* **Cost we accept**: structural typing is checked by mypy, not at runtime. A protocol drift only surfaces at the call site. Mitigation: every Protocol has at least one concrete implementation under unit test.
* **Reversal cost**: low. Replacing the Protocol with an ABC is mechanical; the seam itself is the value.

## DD-013: Citation grounding fails closed with at most one regeneration

- **Status**: active
- **Phase**: 2 (introduced) / 3 (extended to community route)
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: every `[path:lo-hi]` reference in the LLM answer is parsed and verified against the retrieved chunk ranges. Fabricated citations trigger one regeneration with the offending references explicitly forbidden in the prompt. If the second attempt also fails, the bad citations are stripped and the response is returned with `grounded=False`.
* **Alternatives**: loop until grounded; reject the request entirely; ignore grounding (let the user audit).
* **Why**:
  - Looping is unbounded cost; we have observed 5 % of queries that loop forever on certain LLMs because the bad citation comes from a hallucinated overlapping range. Capping at two attempts is a hard cost ceiling.
  - Rejecting the request denies the user their answer for a fixable problem. We always have a usable retrieval set — surfacing it with a `grounded=False` flag is more useful than a 500.
  - Ignoring grounding negates the entire point of the system prompt and the citations contract, and makes regression testing useless.
* **Cost we accept**: a small fraction of answers (~1 % in offline testing on the mock pipeline; lower with real models) finalise with `grounded=False`. The HTTP response makes the flag visible so callers can decide whether to surface it to the user.
* **Reversal cost**: low. The check lives in [`reposage/llm/grounding.py`](../reposage/llm/grounding.py); the regeneration policy is a single conditional in [`reposage/services/retrieval_service.py`](../reposage/services/retrieval_service.py).

## DD-014: Local Ollama is the default LLM provider

- **Status**: active
- **Phase**: 2
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `Settings.llm_model` defaults to `ollama_chat/qwen2.5-coder:7b` and `Settings.ollama_api_base` to `http://localhost:11434`. `LiteLLMClient` auto-forwards `api_base` whenever the model string is in the `ollama` / `ollama_chat` namespace. `make bench-rag` and `reposage ask` therefore work zero-key out of the box; CI / forks set `REPOSAGE_PROFILE=mock` to bypass.
* **Alternatives**:
  - Default to `anthropic/claude-...` or `openai/gpt-...` (require an API key for the smallest demo).
  - Default to `MockLLMClient` everywhere except a manually-flipped switch (CI-only experience leaks into local dev).
  - Run an in-process llama.cpp / candle binding (one more native dependency to maintain).
* **Why**:
  - **Zero-friction onboarding**. A new contributor with `ollama serve` running can `make bench-rag` and answer real questions on the tiny fixture without registering for any API. The "first 5 minutes" demo is what gets people to read the rest of the codebase.
  - **No CI cost regression**. CI explicitly sets `REPOSAGE_PROFILE=mock`; the eval gate stays free and deterministic. Real-LLM smoke is a separate `make test-ollama` opt-in marked `requires_ollama`.
  - **Provider neutral runtime**. The wrapper is LiteLLM, so swapping `LLM_MODEL=openai/gpt-4o-mini` (or anything LiteLLM supports) is a one-line `.env` edit — no code path is special-cased to Ollama. Ollama is just a sensible default, not a coupling.
  - **Hard-fail on misconfig**. `benchmarks.rag.run_eval._check_ollama` pings `/api/tags` before indexing; a misconfigured local box surfaces a clear `OllamaUnavailableError` instead of producing degraded recall numbers.
* **Cost we accept**:
  - First-time users without Ollama get a hard error from `bench-rag` and `reposage ask` until they either install Ollama or set `REPOSAGE_PROFILE=mock`. The error message ships the remediation.
  - Quality of `qwen2.5-coder:7b` on CPU is below GPT-4 / Claude on hard questions; we tolerate that for the local demo since the hosted-provider path is one env edit away.
  - Bench latency P50 with Ollama on CPU is in the 2–10 s range, well above the 1.5 s budget the mock pipeline meets. We address this by gating the strict P50 assertion to the mock branch in CI; the Ollama branch is informational on local runs.
* **Reversal cost**: low. Two settings defaults and one health-check function. Swapping in any other LiteLLM-supported default is mechanical.

## DD-015: Leiden runs over `call` + `inherit` edges only

- **Status**: implemented
- **Phase**: 3
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `build_igraph` excludes `import` edges from the subgraph fed to `leidenalg.find_partition`. `call` and `inherit` are the only edge kinds that contribute to community membership.
* **Alternatives**:
  - Include `import` edges with low weight.
  - Detect communities on the file-level co-occurrence graph (one node per file).
  - Include all edge kinds equally.
* **Why**:
  - `import` edges are *too* dense in modern Python codebases. A small `utils.logging.log` helper sits on the import path of nearly every module; Leiden collapses everything into one giant community and the partition is useless.
  - Symmetrising the directed `call` graph already produces a topology that mirrors how engineers describe modules (DD-003): edges follow actual coupling, not "file X happens to mention name Y at the top".
  - `inherit` adds tight semantic clustering for class hierarchies — `AdminUser` belongs in the same community as `User`. Excluding it would split each subclass into its own micro-community.
* **Cost we accept**: import-only relationships (e.g. a thin adapter that *only* re-exports another module) are invisible to the detector. In practice such adapters carry no business logic and don't need their own summary; they get pulled into a neighbouring community by their downstream callers.
* **Reversal cost**: low. The edge-kind tuple is a `build_igraph` kwarg; flipping it back to include `import` is a one-line change.

## DD-016: Community summaries use a smaller LLM than answers do

- **Status**: active
- **Phase**: 3
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `Settings.summarizer_model` is a separate setting defaulting to `ollama_chat/qwen2.5-coder:3b`, while `Settings.llm_model` (used at query time) stays at `qwen2.5-coder:7b`. The smaller model writes community titles and 2-3 sentence summaries during indexing; the larger model answers user questions.
* **Alternatives**:
  - Use one model for both. Saves on settings sprawl, but indexing a 50 kLOC repo with `qwen2.5-coder:7b` would take ~10× longer on CPU because hundreds of communities each need a Map-phase call.
  - Skip the LLM entirely for summarisation; build summaries from the symbol names with a deterministic template.
  - Use a non-coding model (e.g. `phi-3-mini`) for further cost savings.
* **Why**:
  - The Map-phase summary is short and structured (`{"title": str, "summary": str}` JSON). A small coding LLM produces that reliably and the post-parse JSON validator catches any drift.
  - Saving 3-5× tokens / latency per community matters because indexing is the dominant cost — once a summary is written and content-sha-cached, it's reused indefinitely.
  - Answer quality at query time depends much more on understanding the user's question and the retrieved code chunks than on the summary phrasing. The larger model still does that work.
  - Keeping the two as separate `Settings` keys means an operator can flip either one independently — e.g. a paid-API user can route both at `claude-3-5-sonnet` without touching code.
* **Cost we accept**: about 5-8 % of `qwen2.5-coder:3b` outputs come back malformed (truncated JSON, prose around the object). The parser falls back to using the first line as the title and the rest as the summary; if even that yields fewer than 20 chars we write `<auto-summary unavailable>` and skip embedding. Quality of the community route is therefore noticeably worse with the small model than the large one, but the route is intended for module-level overview questions where "Authentication module — handles login and session lifecycle" is enough signal.
* **Reversal cost**: low. Pointing `summarizer_model` at `llm_model` (or any other LiteLLM model) is a one-line `.env` edit.

## DD-017: Drop isolated symbols from the Leiden subgraph

- **Status**: implemented
- **Phase**: 3
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `build_igraph` prunes every FQN that has zero `call`/`inherit` edges before handing the graph to `leidenalg`. Pruned nodes remain in the `nodes` table for hybrid retrieval; they simply do not appear in any community.
* **Alternatives**:
  - Keep isolated nodes and let `_merge_small` absorb them into neighbouring communities.
  - Bucket all isolated nodes into one synthetic "Misc" community per repo.
  - Pre-filter at the SQL layer (drop the `nodes` row entirely).
* **Why**:
  - `igraph.Graph.contract_vertices(mapping)` keeps positional vertex ids: an isolated vertex never gets a neighbour and thus never gets re-assigned by `_merge_small`. With contraction this snowballs — every level inherits an empty leftover vertex per orphan, which Leiden treats as its own community. On `tests/fixtures/tiny_python_repo` (47 symbols) the un-pruned run produced **65 communities across 3 levels**, with 22 singletons at level 0. After pruning the partition is **11 communities** sized 6 → 3 → 2 — the structure Leiden was supposed to find. The regression test lives in `tests/unit/test_community_detector.py::test_hierarchy_is_strictly_monotonically_coarser`.
  - The summariser cost scales linearly with community count. Singletons would spend tokens on "this function is named `parse_args` and lives in `utils/cli.py`" — content the chunk store already has.
  - Isolated nodes carry no call-topology signal, so their summary embedding cannot help the community retriever. Letting them through would dilute the retriever's index without ever winning a hit.
* **Cost we accept**: a symbol that participates *only* in `import` relationships (excluded by DD-015) never appears in `graph_context`. Hybrid retrieval still surfaces it via BM25 + dense, so the loss is limited to "this orphan symbol cannot anchor a community-route answer". For the 50 kLOC target repo this is a tiny fraction of nodes (mostly `__init__` module nodes).
* **Reversal cost**: low. The pruning step is one block in `build_igraph`; the `n_dropped_isolated` field on `SubgraphStats` already surfaces how many nodes were affected.

## DD-018: Densify Leiden membership before contracting

- **Status**: implemented
- **Phase**: 3
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `CommunityDetector._densify` renumbers cluster ids 0..K-1 in first-appearance order after both `_leiden` and `_merge_small`. The dense membership is what `_contract` and `_groups_from_partition` agree on.
* **Alternatives**:
  - Trust `leidenalg` to return dense ids and pass membership directly to `igraph.Graph.contract_vertices`.
  - Sort membership by cluster id (preserving gaps) instead of densifying.
* **Why**:
  - `_merge_small` reassigns vertices in undersized clusters to their strongest neighbour, which routinely leaves *gaps* in the cluster-id space ([0, 0, 2, 5, 5, ...]). When `contract_vertices` sees a sparse mapping it keeps the holes as empty vertices (a [0, 0, 2, 2] mapping on a 5-vertex graph yields a 3-vertex contracted graph with vertex 1 as an empty isolate). On the next Leiden pass those empties each become their own community — the exact hierarchy-bloat bug DD-017 also touches.
  - Densifying once gives `_contract` and `_groups_from_partition` the same canonical ordering, so the bijection between "group i at level k" and "vertex i in the contracted graph fed to level k+1" holds. Without it, the `child_to_parent` map in the level loop indexes incoherently and parent-child relationships are silently wrong.
* **Cost we accept**: one O(N) pass per level. Trivial — `_densify` is a single dict-build loop.
* **Reversal cost**: low. If a future igraph version makes `contract_vertices` densify automatically, the helper is one method to remove.

## DD-019: Community-route defaults — favour breadth over speed

- **Status**: active
- **Phase**: 3
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `RetrievalService` defaults to `community_top_k=5` and `community_chunks_per_hit=4`. Earlier drafts shipped `3` and `2` respectively.
* **Alternatives**:
  - Keep the earlier `3 × 2` defaults and rely on operators to tune.
  - Adapt the parameters to the question (use the router's confidence as a proxy for how "wide" the question is).
* **Why**:
  - With the original `3 × 2` defaults the real-LLM bench on `tiny_python_repo` (50 cross-file questions, `qwen2.5:7b` via Ollama) produced **community path_recall=0.377** and **community correctness=0.653** against hybrid's **0.897 / 0.859**. The community route was starved: 3 communities × 2 chunks = 6 chunks worth of context for questions like *"how do auth and billing interact?"*. Hybrid surfaced 8 chunks and won handily.
  - Bumping to `5 × 4` brought community **path_recall to 0.888** and **correctness to 0.843** — within −0.028 of hybrid on the same fixture, while community **citation_recall actually beat hybrid (+5.0pp)**. The route is now competitive, with the residual gap traceable to LLM grounding noise (DD-013) rather than starvation.
  - The cost is a latency hit: community P50 went from 3.7 s → 6.4 s and P95 from 9.2 s → 20.0 s. Community is still inside the GraphRAG envelope (≤ 30 s) and the answers are richer; we trade latency for recall here because operators can always pass `--community-top-k 3` for fast mode.
* **Cost we accept**: ~2× LLM token bill on community-route queries vs the old defaults. The community route is also 2-3× slower than hybrid at P50; we mitigate by surfacing both numbers in `LatencyBreakdown` so the API caller can pick the right route.
* **Reversal cost**: low. Both defaults are simple `__init__` kwargs on `RetrievalService` and CLI flags on the benchmark.

## DD-024: Single composition root + `REPOSAGE_PROFILE`

- **Status**: active
- **Phase**: 3.5 (refactor)
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: a single module — [`reposage/composition.py`](../reposage/composition.py) — owns all "mock vs real" wiring. It is the only place in the codebase that reads any "what backend should I build" environment variable. The one variable it reads, `REPOSAGE_PROFILE`, picks an entire retrieval stack in one shot: `mock` / `local` / `production`. CLI, FastAPI, and benchmarks all build their `RetrievalService` via `composition.build_retrieval_service(...)`. Supersedes the prior implicit conventions around `REPOSAGE_LLM_PROVIDER`, `REPOSAGE_RAG_LLM`, and `REPOSAGE_DENSE` (all three deleted, no compatibility shim).
* **Alternatives**:
  - Keep per-surface `build_*` helpers in `api/dependencies.py`, `cli.py`, and each benchmark, plus three env vars (`REPOSAGE_LLM_PROVIDER`, `REPOSAGE_RAG_LLM`, `REPOSAGE_DENSE`). This is the shape that grew organically over Phases 1-3 and we found ourselves with four parallel mock-wiring implementations and two different definitions of what "mock" means at the dense layer (`api/dependencies.build_dense()` always returned `HnswGrpcClient`; CLI's `_build_cli_dense` switched to `LocalDenseIndex` when `REPOSAGE_DENSE=local`).
  - Use a DI container library (e.g. `dependency-injector`). Powerful but adds a runtime dep, an extra mental model, and a per-Settings YAML/JSON config. Overkill for three profiles.
  - Profile-as-classes (`MockProfile`, `LocalProfile`, `ProductionProfile`). Reads cleanly but adds boilerplate; a flat `if current_profile() == "mock"` ladder is shorter and equally easy to extend.
* **Why**:
  - **One source of truth**. `current_profile()` is the only function in the repo that consults environment for backend selection. Changing the mapping ("local should now use BGE instead of HashEmbedder", "production should use a hosted reranker") is a single-file diff.
  - **CLI / HTTP parity by construction**. Both surfaces call `build_retrieval_service(sqlite_path=...)`. They cannot drift on dense vs gRPC because the choice is made before the call site.
  - **Forward-compatible**. Adding a new profile (`staging`, `hosted-anthropic`) means one line in `_VALID_PROFILES` plus one branch per backend constructor. No call site changes.
  - **Hard-fail on typos**. `REPOSAGE_PROFILE=mok` raises `ValueError` instead of silently defaulting to mock. (Unset still defaults to `mock` so a fresh clone runs out of the box.)
* **Cost we accept**: removing the old env vars is a breaking change for any operator who had `.env` files using them. We accept that — this is a single-author repo with no production deployment, and the migration is one search-and-replace.
* **Reversal cost**: low-medium. Reverting would require re-introducing per-surface builders and three env vars. The protocol-typed seams (DD-012) are unaffected.

## DD-025: `AnswerResult.outcome: RouteOutcome` replaces `route: str`

- **Status**: active
- **Phase**: 3.5 (refactor)
- **Last reviewed**: 2026-05-25
- **Superseded-by**: —

* **Choice**: `AnswerResult` carries a structured `outcome: RouteOutcome(route, degraded_from, degrade_reason)` instead of a flat `route: str`. `route` is restricted to `Literal["graph", "community", "hybrid"]`; the previously-overloaded `"community->hybrid"` concatenation is deleted. The HTTP `AskResponse.outcome` mirrors the same structure (`{route, degraded_from, degrade_reason}`). `_answer_community_fallback_to_hybrid` and its sibling early-return paths are deleted; `RetrievalService.answer()` is rewritten as a single dispatcher over three linear `_run_graph` / `_run_community` / `_run_hybrid` functions, with a `_Degrade` sentinel for the community → hybrid path. Supersedes the route-as-string convention that grew alongside DD-013's regenerate-and-strip policy.
* **Alternatives**:
  - Add a second sibling field (`degraded_from: str | None`) but keep `route: str` flat. Cheaper diff but leaves the documented enum at four values (`graph | community | hybrid | community->hybrid`), which dashboards and assertions had to special-case.
  - Build a per-route subclass hierarchy (`GraphResult`, `CommunityResult`, `HybridResult`). Heavier API contract surface; doesn't compose well with the HTTP schema.
  - Encode degradation in `grounded=False` only. Conflates two different things — `grounded` is about citations, not about which route ran.
* **Why**:
  - **Machine-readable degradation**. Dashboards / structured logs can now `WHERE outcome.degraded_from = 'community'` instead of substring-matching `route LIKE 'community->%'`.
  - **One dispatcher, three linear functions**. The pre-refactor `_answer_community` had three early returns to a `_answer_community_fallback_to_hybrid` helper that itself called `_answer_hybrid` and string-mutated `route` on the result. The new shape is a single dispatcher (`answer`) plus `_run_*` functions that each only handle their own route; degradation is expressed by returning a `_Degrade` sentinel.
  - **Forward-compatible**. A new route (`Literal[...]` extension, e.g. Phase 4 `agentic`) needs one new `_run_*` function. A new degradation source (`degraded_from = "graph"` if graph route fell through) is one literal value extension.
  - **`AnswerResult.route` property preserved**. Most call sites only care about the leaf route; the dataclass exposes `result.route` as a thin `@property` so the CLI / log lines / bench summarise remain ergonomic without re-introducing a second source of truth.
* **Cost we accept**:
  - The HTTP response field renames from `route: str` to `outcome: { route, degraded_from, degrade_reason }`. Any existing dashboard that joins on `route` must update. Acceptable because the repo has no published API consumers yet.
  - One extra dataclass (`RouteOutcome`) and one extra Pydantic model (`Outcome`) per response.
* **Reversal cost**: low. The dispatch shape is independent of the seams (DD-012); flattening `outcome.route` back into `AnswerResult.route` is mechanical if we ever decide structured degradation is overkill.

## DD-026: mmap snapshot with a zero-copy vector arena and columnar/lazy ids

- **Status**: implemented
- **Phase**: 4
- **Last reviewed**: 2026-06-12
- **Superseded-by**: —

* **Choice**: `hnsw.Recover` mmaps the snapshot and aliases the large arrays (the float32 vector arena, the layer-0 CSR `adj0`, the upper-layer blob `adjU`) straight out of the mapping; only the small offset/level arrays are copied. Node ids are stored **columnar** (`idData` bytes + `idOff` offsets) and materialised lazily — a search hit calls `string(idData[idOff[i]:idOff[i+1]])`, so we never build n strings up front. The `idIndex` (id→internal) map is left nil after Recover and rebuilt lazily only if an `Add` arrives. A recovered index is **frozen** (read-only mmap); the first mutation `thaw`s it by copying the aliased regions into owned memory and unmapping.
* **Alternatives**:
  - Eagerly read the whole file with `os.ReadFile` and parse into normal slices. Simple, but copying a 512 MB vector arena (1M × 128) blows the `< 200 ms` reload budget on its own.
  - mmap but eagerly build all n node structs, ids, and the id map. The per-node allocations (≈ 1M slice headers + 1M strings + 1M map inserts) dominate and again threaten the budget.
* **Why**: the Phase 4 exit metric is "reload 1M × 128 with P50 `< 200 ms`". The vector arena is the overwhelming cost; mmap lets the kernel page it in lazily so `Recover` does `O(parse small arrays)` work and returns near-instantly. Columnar/lazy ids and the lazy id map remove the only remaining O(n) per-node costs from the serving path (which is recover-then-Search, never recover-then-Add).
* **Cost we accept**: a recovered index is read-only until thawed; the rare "recover then write" path pays a one-time deep copy. `unsafe` aliasing assumes a little-endian host (asserted at package init) and requires 3-index slicing so a stray `append` cannot write into the read-only mapping.
* **Reversal cost**: low-medium. The format is versioned (`version=2`); falling back to eager reads is a localised change in `persist.go` if mmap ever becomes a liability.

## DD-027: Algorithm 4 heuristic neighbour selection (default on)

- **Status**: implemented
- **Phase**: 4
- **Last reviewed**: 2026-06-12
- **Superseded-by**: —

* **Choice**: select neighbours with the paper's Algorithm 4 (SELECT-NEIGHBORS-HEURISTIC) — keep a candidate only if it is closer to the query than to any already-selected neighbour, with `keepPrunedConnections=true` to refill to M. Both the insert path and reverse-edge trimming use it. `Config.Heuristic` defaults true; the Phase 2 simple selection (Algorithm 3, closest-M) is retained behind `Heuristic=false` for comparison and as a fallback.
* **Alternatives**: keep the simple closest-M selection (what Phase 2 shipped). Cheaper per insert, but it packs near-duplicate edges in clustered regions and gives lower recall at a fixed efSearch.
* **Why**: the heuristic produces an RNG-style (relative neighbourhood graph) pruning — diverse long/short edges — which is what `hnswlib` ships by default and what lifts recall on clustered data like SIFT. Better recall-vs-QPS is exactly the Pareto deliverable.
* **Cost we accept**: a modest constant-factor increase in build time (extra candidate-to-selected distance checks, bounded by the `ef`-sized candidate set).
* **Reversal cost**: trivial — flip `Config.Heuristic`.

## DD-028: Atomic snapshot writes (tmp + fsync + rename)

- **Status**: implemented
- **Phase**: 4
- **Last reviewed**: 2026-06-12
- **Superseded-by**: —

* **Choice**: `Snapshot` streams the full image to `path + ".tmp"`, `fsync`s it, then `os.Rename`s over `path`. The rename is indirected through a package var so tests can simulate a failed commit and assert the previous snapshot survives.
* **Alternatives**: write in place over the live file. A crash mid-write would leave a half-written, unrecoverable snapshot — worse than having no snapshot.
* **Why**: POSIX `rename` within a directory is atomic, so a reader either sees the entire old file or the entire new one. Crash safety beats the marginal cost of a temp file and one extra fsync.
* **Cost we accept**: transient 2× disk for the snapshot during the write; a stale `.tmp` orphan if the process dies between write and rename (cleaned up on the next Snapshot).
* **Reversal cost**: trivial.

## DD-029: Snapshot via server lifecycle, not a gRPC RPC (for now)

- **Status**: active
- **Phase**: 4
- **Last reviewed**: 2026-06-12
- **Superseded-by**: —

* **Choice**: expose snapshot/recover through the `hnsw-server` process lifecycle (`--snapshot` recovers on boot and writes an initial snapshot after a cold SQLite load; `--snapshot-on-exit` persists on graceful shutdown) rather than adding a `Snapshot` gRPC method. `proto/hnsw.proto` is unchanged.
* **Alternatives**: add `rpc Snapshot(...)` to the proto. Cleaner trigger from the Python indexer, but the dev/CI environment lacks `protoc-gen-go` / `protoc-gen-go-grpc`, so regenerating the Go + Python stubs by hand is fragile and out of scope for Phase 4.
* **Why**: the roadmap's Phase 4 deliverables (mmap snapshot/restore, atomic writes, fast reload, bench) are fully satisfied at the lifecycle level. Keeping the proto frozen means the Python `hnsw_client.py` needs zero changes.
* **Cost we accept**: no on-demand snapshot trigger from a running server until the RPC is added; snapshots happen at boot/exit only.
* **Reversal cost**: low. Adding the RPC later is additive — the existing surface and the Python client are unaffected.
