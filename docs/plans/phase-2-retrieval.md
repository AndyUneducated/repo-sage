# Phase 2 — 检索 v1：端到端混合 RAG（技术方案）

> 本文档与 [`docs/ROADMAP.md`](../ROADMAP.md) 第 2 阶段对应。
> 创建日期：2026-05-24。
> 同期讨论副本另存于 `~/.cursor/plans/phase-2_hybrid_rag_*.plan.md`。

## 1. 目标对齐

路线图 Phase 2 退出指标：

- `reposage ask --route hybrid` 与 `POST /ask` 返回带 `[path:start-end]` 引用的答案。
- 链路：`bge-en-v1.5` 嵌入 → 自建 `go-hnsw` gRPC（M=16, efC=200, efS=64）+ `rank-bm25` → RRF 融合（k=60）→ `bge-reranker-v2-m3` 重排 → LiteLLM 生成 → Citation grounding 校验。
- 50 kLOC 仓库 P50 端到端 < 1.5 s；20 题手工质检通过率 100 %。
- 演示：`langchain_classic` 上正确回答 “How is the session timeout configured?”。

## 2. 行业标准对齐

| 选择 | 引用 / 默认 |
| --- | --- |
| HNSW 参数 | M=16, efConstruction=200, efSearch=64（Malkov & Yashunin 2018 推荐） |
| RRF k | 60（Cormack et al. 2009 标准） |
| 嵌入模型 | bge-en-v1.5 768-d（FlagEmbedding 官方） |
| Reranker | bge-reranker-v2-m3（轻量 cross-encoder） |
| LLM 抽象 | LiteLLM（DD-007） |
| IPC | gRPC + Protobuf（DD-001 自建 HNSW 已留口） |
| HTTP 契约 | Pydantic v2，响应保留 `graph_context: Optional` 字段给 Phase 3 |

## 3. 前后向兼容设计

- 四个 Protocol（[`reposage/retrieval/protocols.py`](../../reposage/retrieval/protocols.py)）：`SparseRetriever`、`DenseRetriever`、`Reranker`、`LLMClient`。Phase 7 的 Tantivy、Phase 5 的 mmap HNSW、Phase 8 的多副本，全部从这里换实现（DD-012）。
- 嵌入存 SQLite，多 `model` 列并存，Phase 7 模型升级时灰度切换（DD-011）。
- `embeddings.dim` 显式存盘并在启动时校验，模型尺寸不一致即报错。
- Phase 5 mmap：写一次性 `export-snapshot` 工具从 `embeddings` 表导出 arena，不需要回改 Phase 2 的代码。
- 单测用 [`LocalDenseIndex`](../../reposage/retrieval/local_dense.py)（纯 numpy 线性扫描）跳过 Go gRPC，CI Python 阶段不需要 Go 工具链；`make test-grpc` 才启动真实 server。

## 4. 数据流（含原子性边界）

`reposage index` Phase 1 已在写 `chunks`。Phase 2 在同一 SQLite 事务里追加 `embeddings`：

```sql
CREATE TABLE IF NOT EXISTS embeddings(
  chunk_id   TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vector     BLOB NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS embeddings_model ON embeddings(model);
```

`hnsw-server` 启动：批量 `SELECT chunk_id, vector FROM embeddings WHERE model=?` → 调 `Add(idx, vec)` 灌入；启动 1× O(N log N) HNSW 构建。

## 5. 关键文件

### 5.1 新建（Python）

- [`reposage/retrieval/protocols.py`](../../reposage/retrieval/protocols.py)：`SparseRetriever`/`DenseRetriever`/`Reranker`/`LLMClient` Protocol。
- [`reposage/storage/embeddings_store.py`](../../reposage/storage/embeddings_store.py)：embeddings 表 CRUD + 模型/维度校验。
- [`reposage/retrieval/local_dense.py`](../../reposage/retrieval/local_dense.py)：`LocalDenseIndex`（numpy 线性扫描）。
- [`reposage/services/retrieval_service.py`](../../reposage/services/retrieval_service.py)：`RetrievalService` 编排器。
- [`reposage/llm/grounding.py`](../../reposage/llm/grounding.py)：`extract_citations` + `verify_grounding`，未引用必删（drop-and-regenerate）。
- [`reposage/proto/hnsw_pb2.py`](../../reposage/proto/hnsw_pb2.py)（buf/protoc 生成）。

### 5.2 实现（Python，桩件 → 正式）

- [`reposage/indexer/embedder.py`](../../reposage/indexer/embedder.py)：`BgeEmbedder`（懒加载 sentence-transformers）+ `HashEmbedder`（CI/测试 fake）。
- [`reposage/indexer/pipeline.py`](../../reposage/indexer/pipeline.py)：在 `_index_file` 后对新增 chunks 调 `embedder.embed` → `embeddings_store.upsert`，与 chunks 共用一次事务。
- [`reposage/retrieval/hnsw_client.py`](../../reposage/retrieval/hnsw_client.py)：gRPC `DenseRetriever` 实现；启动时调 `Stats` 验证 dim 与配置一致。
- [`reposage/retrieval/bm25.py`](../../reposage/retrieval/bm25.py)：`SparseRetriever` 实现；分词 `[A-Za-z0-9]+` → 小写 → 长度 ≥ 2 且非纯数字；启动时全量 scan `chunks` 建内存倒排。
- [`reposage/retrieval/hybrid.py`](../../reposage/retrieval/hybrid.py)：并行 dense/sparse top-50 → RRF k=60 融合 → 取 top-20 → 重排 → top-8。
- [`reposage/retrieval/reranker.py`](../../reposage/retrieval/reranker.py)：`CrossEncoderReranker`（生产）+ `MockReranker`（CI）。
- [`reposage/llm/client.py`](../../reposage/llm/client.py)：`LiteLLMClient`（生产）+ `MockLLMClient`（CI 无密钥）。
- [`reposage/llm/prompts.py`](../../reposage/llm/prompts.py)：系统提示明令 “只能基于 `<retrieved_chunk>`，引用必须形如 `[path:start-end]`”。
- [`reposage/retrieval/router.py`](../../reposage/retrieval/router.py)：扩展 `route`：先跑 graph fast-path，未命中走 LLM intent 分类（少量 token，输出 `graph|hybrid|community`）。
- [`reposage/api/routes/ask.py`](../../reposage/api/routes/ask.py)：`POST /ask` → `RetrievalService.answer(question)` → 返回 `{answer, citations[], route, latency_ms, grounded, graph_context}`。
- [`reposage/cli.py`](../../reposage/cli.py)：`ask --route hybrid/auto/graph/community` 走 `RetrievalService`；新增 `reposage serve` 启动 FastAPI（uvicorn）。

### 5.3 新建（Go）

- [`proto/hnsw.proto`](../../proto/hnsw.proto)：`Add(id, vec)`、`BulkLoad(stream)`、`Search(vec, k, ef)`、`Stats() -> (size, dim, model, M, efC, efS)`。
- [`go-hnsw/cmd/server/main.go`](../../go-hnsw/cmd/server/main.go)：flags `-addr -db -model -dim -m -ef-construction -ef-search`，启动时打开 SQLite，BulkLoad 后开始服务。
- [`go-hnsw/internal/grpcserver/server.go`](../../go-hnsw/internal/grpcserver/server.go)：把 gRPC 请求映射到 `hnsw.Index`。
- [`go-hnsw/internal/grpcserver/sqlite_load.go`](../../go-hnsw/internal/grpcserver/sqlite_load.go)：从 `embeddings` 表冷启动加载（pure-Go SQLite 驱动）。
- [`go-hnsw/insert.go`](../../go-hnsw/insert.go) / [`search.go`](../../go-hnsw/search.go) / [`internal/heap/heap.go`](../../go-hnsw/internal/heap/heap.go)：补齐 Algorithm 1 / 5。

### 5.4 配置 / 工具

- [`pyproject.toml`](../../pyproject.toml)：补 `sentence-transformers`、`grpcio`、`grpcio-tools`、`protobuf`、`fastapi`、`uvicorn`、`litellm`、`rank-bm25`。
- [`Makefile`](../../Makefile)：`proto-gen`、`hnsw-build`、`hnsw-run`、`bench-rag`、`bench-rag LARGE=1`、`test-grpc`。
- [`.github/workflows/ci-go.yml`](../../.github/workflows/ci-go.yml)：构建 `cmd/server`，跑 `go test ./... -race`。
- [`.github/workflows/eval-gate.yml`](../../.github/workflows/eval-gate.yml)：每 PR 都跑 `bench-rag`（mock LLM，免密钥）；周一 cron 跑真实 LLM。

## 6. /ask 响应契约（前向兼容）

```json
{
  "question": "...",
  "answer": "Session timeout is set in [auth/sessions.py:12-25] ...",
  "citations": [
    {"path": "auth/sessions.py", "start_line": 12, "end_line": 25}
  ],
  "route": "hybrid",
  "grounded": true,
  "latency_ms": {
    "embed_ms": 0, "retrieve_ms": 1, "rerank_ms": 0, "llm_ms": 1, "total_ms": 2
  },
  "graph_context": null
}
```

`graph_context` 字段保留给 Phase 3 GraphRAG，Phase 2 始终为 `null`。

## 7. 测试矩阵

### 单测（`pytest tests/unit`，不依赖 Go / 大模型）

- `test_embeddings_store.py`：dim/model 校验、CASCADE 删除、批流式读取。
- `test_local_dense.py`：纯 numpy 实现的 top-k 召回正确、零向量保护。
- `test_bm25.py`：分词器、`User.login` → `["user", "login"]`、SQLite 冷启动。
- `test_hybrid_rrf.py`：RRF 边界（同序、不同序、空输入、k 影响）。
- `test_grounding.py`：合法引用通过 / 越界 / 不存在 path / 行号反转。
- `test_router_llm.py`：`mock` LLMClient 决策 `hybrid`/`graph`/`community`，并校验 LLM 失败时的 hybrid 兜底。
- `test_prompts.py`：模板填充与去毒（`Never invent`）。
- `test_router.py`：保留 Phase 1 启发式行为，`route_sync` 现在为非符号问题返回 `hybrid`（兜底）。

### 集成

- `tests/integration/test_ask_e2e.py`：`tiny_python_repo` + `HashEmbedder` + `LocalDenseIndex` + `MockLLMClient` 端到端：`RetrievalService.answer` 返回合法引用、`POST /ask` 走 FastAPI TestClient、grounder 拒绝伪造引用。
- `tests/integration/test_grpc_hnsw.py`（`pytest.mark.requires_go_hnsw`）：启动子进程 `cmd/server`，从 `embeddings` 冷加载后能命中已知 chunk。
- `tests/integration/test_rag_bench.py`：把 20 题质检嵌进 pytest，断言 P50 < 1500 ms、citation 100 % 合法、recall@8 ≥ 0.80。

### Bench

- [`benchmarks/rag/python_20.jsonl`](../../benchmarks/rag/python_20.jsonl)：20 题手工 ground truth（问题、期望命中文件集合）。
- [`benchmarks/rag/run_eval.py`](../../benchmarks/rag/run_eval.py)：跑 `tiny_python_repo`（默认）或 `--large`（50 kLOC，`REPOSAGE_LARGE_REPO` 指定）。输出 P50/P95 延迟、文件级 recall@k、citation 合法率。
- 当前指标：P50 ≈ 0 ms（mock LLM）/ recall@8 = 1.000 / citation 合法率 = 1.000，留出 1.5 s 头部预算给真实 LLM。

## 8. 非目标（Phase 2 不做）

- 持久化 HNSW（Phase 5）。
- 增量删除（Phase 7）。
- HNSW 多线程构建/查询（Phase 6）。
- Tantivy（Phase 7）。
- TS/Go 实际嵌入（与 Phase 1 一致，仅在 file_meta 标 `unsupported`，跳过 embedding）。
- 流式响应（Phase 6）。

## 9. 风险与对策

- **Reranker 在 CPU 上拖慢 P50**：限制重排候选 ≤ 20；超出时打 metric 在日志报警；Phase 6 才上 batch。
- **冷启动 BM25 全量 scan 慢**：Phase 2 仓库 ≤ 50 kLOC，~10 k chunks，scan < 200 ms；超过则在 [`reposage/retrieval/bm25.py`](../../reposage/retrieval/bm25.py) 改用 `pyarrow` 流式读，但 Phase 2 不做。
- **CI 没有 LLM 密钥**：默认走 `mock` provider，仅 `eval-gate` 周一 cron 加 `OPENAI_API_KEY` 跑真实 20 题质量门。
- **gRPC 连不上**：CLI 启动时 `Stats()` ping 失败 → 抛出 “server dim X != client expected Y” 报错并提示 `make hnsw-run`。

## 10. 演示命令

### 本地一键端到端（默认 mock LLM，无需密钥）

```bash
REPOSAGE_PROFILE=mock \
  python -m reposage.cli index --repo tests/fixtures/tiny_python_repo --force

REPOSAGE_PROFILE=mock \
  python -m reposage.cli ask "How is the session opened against User.login?" --route hybrid
```

### 用真实 bge + LiteLLM

```bash
REPOSAGE_PROFILE=local \
  python -m reposage.cli index --repo path/to/your/repo --force
make hnsw-run            # 终端 1
REPOSAGE_PROFILE=production \
  python -m reposage.cli ask "How is the session timeout configured?" --route hybrid
```

### 退出指标全量回放

```bash
make lint && make typecheck
make test                 # python unit + integration（跳过 grpc mark）
make test-grpc            # 启动 hnsw-server 子进程跑 grpc 集成
make bench-graph          # Phase 1 30 题，precision >= 0.90
make bench-rag            # Phase 2 20 题，P50 < 1.5s, recall@8 >= 0.80, citations = 100%
make hnsw-test            # Go race detector
```
