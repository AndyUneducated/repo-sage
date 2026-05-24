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

## DD-009: Phase 1 ships forward-compatible schema and module-aware Python resolver

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

* **Choice**: when the indexer encounters `.ts` / `.tsx` / `.js` / `.jsx` / `.go` files, it parses them with tree-sitter to confirm they're well-formed but writes only one row to `file_meta` with `parse_status='unsupported'`. No `chunks`, `nodes`, or `edges` are produced for them.
* **Alternatives**: skip them entirely (no `file_meta` row); chunk them now to seed Phase 2 BM25.
* **Why**: `parse_status` becomes the single source of truth for index coverage. Phase 4's GitHub App can answer "what does this index cover?" in one SQL query, which directly supports user-facing expectation management when we deploy on polyglot OSS repos. Skipping entirely costs that visibility; chunking now would force Phase 3 GraphRAG to handle "chunk without a node" dangling state.
* **Cost we accept**: Phase 2 hybrid retrieval is Python-only at the start of Phase 2 (TS/Go content is invisible to BM25 + HNSW until their resolvers ship).
* **Reversal cost**: low. Adding TS / Go resolvers is purely additive — Phase 1 rows for those files have a stable shape.

## DD-011: Embeddings live in SQLite as float32 BLOBs (not sidecar `.npy`)

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

* **Choice**: `SparseRetriever`, `DenseRetriever`, `Reranker`, and `LLMClient` are `typing.Protocol`s in [`reposage/retrieval/protocols.py`](../reposage/retrieval/protocols.py). `RetrievalService` accepts any concrete implementation that satisfies the contract. A `LocalDenseIndex` (numpy linear scan) implements `DenseRetriever` for tests.
* **Alternatives**: ABCs with `@abstractmethod`; concrete classes with monkey-patched fakes.
* **Why**:
  - Unit tests must not require a Go binary, network access, or model downloads. A 10 ms numpy scan over 10 k 768-d vectors is the fastest path to coverage for the orchestration code.
  - Phase 5 mmap HNSW, Phase 7 Tantivy, and Phase 8 sharding each replace exactly one Protocol implementation. The orchestrator never sees the swap.
  - `Protocol` (vs ABC) keeps the inheritance graph flat — concrete classes don't need to register or import the protocol module to satisfy it.
* **Cost we accept**: structural typing is checked by mypy, not at runtime. A protocol drift only surfaces at the call site. Mitigation: every Protocol has at least one concrete implementation under unit test.
* **Reversal cost**: low. Replacing the Protocol with an ABC is mechanical; the seam itself is the value.

## DD-013: Citation grounding fails closed with at most one regeneration

* **Choice**: every `[path:lo-hi]` reference in the LLM answer is parsed and verified against the retrieved chunk ranges. Fabricated citations trigger one regeneration with the offending references explicitly forbidden in the prompt. If the second attempt also fails, the bad citations are stripped and the response is returned with `grounded=False`.
* **Alternatives**: loop until grounded; reject the request entirely; ignore grounding (let the user audit).
* **Why**:
  - Looping is unbounded cost; we have observed 5 % of queries that loop forever on certain LLMs because the bad citation comes from a hallucinated overlapping range. Capping at two attempts is a hard cost ceiling.
  - Rejecting the request denies the user their answer for a fixable problem. We always have a usable retrieval set — surfacing it with a `grounded=False` flag is more useful than a 500.
  - Ignoring grounding negates the entire point of the system prompt and the citations contract, and makes regression testing useless.
* **Cost we accept**: a small fraction of answers (~1 % in offline testing on the mock pipeline; lower with real models) finalise with `grounded=False`. The HTTP response makes the flag visible so callers can decide whether to surface it to the user.
* **Reversal cost**: low. The check lives in [`reposage/llm/grounding.py`](../reposage/llm/grounding.py); the regeneration policy is a single conditional in [`reposage/services/retrieval_service.py`](../reposage/services/retrieval_service.py).

## DD-014: Local Ollama is the default LLM provider

* **Choice**: `Settings.llm_model` defaults to `ollama_chat/qwen2.5-coder:7b` and `Settings.ollama_api_base` to `http://localhost:11434`. `LiteLLMClient` auto-forwards `api_base` whenever the model string is in the `ollama` / `ollama_chat` namespace. `make bench-rag` and `reposage ask` therefore work zero-key out of the box; CI / forks set `REPOSAGE_RAG_LLM=mock` to bypass.
* **Alternatives**:
  - Default to `anthropic/claude-...` or `openai/gpt-...` (require an API key for the smallest demo).
  - Default to `MockLLMClient` everywhere except a manually-flipped switch (CI-only experience leaks into local dev).
  - Run an in-process llama.cpp / candle binding (one more native dependency to maintain).
* **Why**:
  - **Zero-friction onboarding**. A new contributor with `ollama serve` running can `make bench-rag` and answer real questions on the tiny fixture without registering for any API. The "first 5 minutes" demo is what gets people to read the rest of the codebase.
  - **No CI cost regression**. CI explicitly sets `REPOSAGE_RAG_LLM=mock`; the eval gate stays free and deterministic. Real-LLM smoke is a separate `make test-ollama` opt-in marked `requires_ollama`.
  - **Provider neutral runtime**. The wrapper is LiteLLM, so swapping `LLM_MODEL=openai/gpt-4o-mini` (or anything LiteLLM supports) is a one-line `.env` edit — no code path is special-cased to Ollama. Ollama is just a sensible default, not a coupling.
  - **Hard-fail on misconfig**. `benchmarks.rag.run_eval._check_ollama` pings `/api/tags` before indexing; a misconfigured local box surfaces a clear `OllamaUnavailableError` instead of producing degraded recall numbers.
* **Cost we accept**:
  - First-time users without Ollama get a hard error from `bench-rag` and `reposage ask` until they either install Ollama or set `REPOSAGE_LLM_PROVIDER=mock`. The error message ships the remediation.
  - Quality of `qwen2.5-coder:7b` on CPU is below GPT-4 / Claude on hard questions; we tolerate that for the local demo since the hosted-provider path is one env edit away.
  - Bench latency P50 with Ollama on CPU is in the 2–10 s range, well above the 1.5 s budget the mock pipeline meets. We address this by gating the strict P50 assertion to the mock branch in CI; the Ollama branch is informational on local runs.
* **Reversal cost**: low. Two settings defaults and one health-check function. Swapping in any other LiteLLM-supported default is mechanical.
