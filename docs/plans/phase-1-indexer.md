# Phase 1 — 索引器 v1：tree-sitter + 符号图（技术方案）

> 本文是本仓库 Phase 1 的最终技术方案，与 [`docs/ROADMAP.md`](../ROADMAP.md) 第 1 阶段对应。
> 创建日期：2026-05-21。
> 同期讨论副本另存于 `~/.cursor/plans/phase_1_indexer_*.plan.md`。

## 目标对齐

路线图 Phase 1 的退出指标：

- `reposage index <repo>` 把 Python 仓库写入 SQLite。
- `reposage ask --route graph "where is X called?"` 在 10 kLOC 夹具上能返回 `file:line` 列表。
- 30 题手工评分 ≥ 90% 精确率；50 kLOC 索引 < 60 s。

本次方案在路线图基础上做两处**前置增强**：

1. **模块感知的 Python FQN 解析**（对齐 Sourcegraph `scip-python` v1 的水位）。
2. **Phase 2 / 3 / 7 用到的 schema 一次性加进去**：`chunks`、`repo_meta`、`file_meta`、`edges.weight`，避免后续侵入式迁移。

## 行业标准对齐要点

- **tree-sitter 包替换**：`pyproject.toml` 当前用的 `tree-sitter-languages` 自 2024 年起停止维护，社区现以 `tree-sitter-language-pack` 为准；同时把 `tree-sitter` 升到 `>=0.23` 配套的新 API。
- **AST 感知分块**：函数 / 方法 / 类 / 顶层语句为单元，超长函数按行级分子块（`max_lines=80`、`overlap=4`），与 Cursor / Cody / Copilot Workspace 一致。
- **SCIP 风格 FQN（简化版）**：保持 `pkg.module.Class.method` 字符串格式，但在 `nodes` 表加 `language` 列，TS / Go 接入时用 `<lang>:` 前缀消歧；不引入完整 SCIP protobuf。
- **两遍解析**（cross-file resolution 标准做法）：第一遍收集所有 `def` FQN 建模块符号表；第二遍把 `call` / `inherit` / `import` 边落到已知 FQN，未解析的保留为 `<unresolved:name>` 以便 Phase 3 兜底。
- **CSR 风格的反向邻接**：SQLite 上等价做法是 `INDEX edges_dst_kind ON edges(dst, kind)`。

## 数据流

```
walk repo → parse → chunk     → ChunkStore
                  → extract → resolver → nodes / edges
walk repo → file_meta（含 file_sha / mtime / parse_status）
```

## SQLite schema（最终态）

完整字段说明同时落到 [`docs/INDEX_SCHEMA.md`](../INDEX_SCHEMA.md)；这里只列结构。

```sql
-- 符号图主体
CREATE TABLE nodes(
  fqn TEXT PRIMARY KEY,
  kind TEXT NOT NULL,           -- module|class|function|method|variable
  language TEXT NOT NULL,       -- python|typescript|go
  repo TEXT NOT NULL,
  path TEXT NOT NULL,
  start_line INT NOT NULL,
  end_line INT NOT NULL
);

CREATE TABLE edges(
  src TEXT NOT NULL,
  dst TEXT NOT NULL,            -- 可能是 '<unresolved:foo>'
  kind TEXT NOT NULL,           -- def|call|inherit|import
  src_path TEXT NOT NULL,
  src_line INT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,   -- Phase 3 Leiden 用
  PRIMARY KEY (src, dst, kind, src_line)
);
CREATE INDEX edges_dst_kind ON edges(dst, kind);
CREATE INDEX edges_src_kind ON edges(src, kind);

-- Chunk（Phase 2 HNSW 直接接，避免迁移）
CREATE TABLE chunks(
  chunk_id TEXT PRIMARY KEY,    -- sha1(repo|path|start|end|text)
  repo TEXT NOT NULL,
  path TEXT NOT NULL,
  language TEXT NOT NULL,
  start_line INT NOT NULL,
  end_line INT NOT NULL,
  symbol TEXT,
  parent_symbol TEXT,
  text TEXT NOT NULL,
  file_sha TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX chunks_repo_path ON chunks(repo, path);
CREATE INDEX chunks_symbol ON chunks(symbol);

-- 增量索引元数据（Phase 7 直接用，Phase 1 仅写不读）
CREATE TABLE repo_meta(
  repo TEXT PRIMARY KEY,
  head_sha TEXT,
  default_branch TEXT,
  last_indexed_at INTEGER NOT NULL
);

CREATE TABLE file_meta(
  repo TEXT NOT NULL,
  path TEXT NOT NULL,
  file_sha TEXT NOT NULL,
  mtime INTEGER NOT NULL,
  parse_status TEXT NOT NULL,   -- ok|parse_error|unsupported
  last_indexed_at INTEGER NOT NULL,
  PRIMARY KEY (repo, path)
);
```

## 关键文件改动

- [`pyproject.toml`](../../pyproject.toml)：移除 `tree-sitter-languages`，加入 `tree-sitter-language-pack`、`tree-sitter>=0.23`；mypy override 同步改名。
- [`reposage/indexer/parser.py`](../../reposage/indexer/parser.py)：用 `tree_sitter_language_pack.get_language` 缓存 grammar；`parse(path)` 读 bytes → 选 grammar → 返回 `ParseResult`；解码失败/不支持返回 `None`，错误进 `file_meta.parse_status`。
- [`reposage/indexer/chunker.py`](../../reposage/indexer/chunker.py)：递归找 `function_definition` / `class_definition` / 顶层语句；超长函数 `_split_long(text, max_lines, overlap)`；`chunk_id = sha1(repo|path|start|end|text).hexdigest()`。
- 新增 `reposage/indexer/extractor.py`：执行 `.scm` 查询，输出中间结构 `RawEdge(kind, local_name, src_node, src_path, src_line)`，与 FQN 解析解耦。
- 新增 `reposage/indexer/python_resolver.py`：Python 专用模块感知解析器；接口设计成 `LanguageResolver`，TS / Go 后续 Phase 直接挂钩。
- [`reposage/indexer/symbol_graph.py`](../../reposage/indexer/symbol_graph.py)：`SymbolNode` 加 `language` 字段；保留内存版供单测用。
- [`reposage/storage/sqlite_graph.py`](../../reposage/storage/sqlite_graph.py)：实现 schema/upsert/反向邻接查询；`PRAGMA journal_mode=WAL`、`synchronous=NORMAL`。
- 新增 `reposage/storage/chunk_store.py`：与 `SQLiteSymbolGraphStore` 共用一个 DB 文件。
- [`reposage/indexer/pipeline.py`](../../reposage/indexer/pipeline.py)：`run(force)` 走 walk → file_sha 比对 → parse → chunk → extract → resolve → 入库 → 返回 `IndexManifest`；解析失败不阻断、写进 `manifest.failures`。
- [`reposage/retrieval/router.py`](../../reposage/retrieval/router.py)：加 graph fast-path；正则识别 `Foo.bar` → `QueryRoute(name="graph", confidence=1.0, reason="symbolic")`，其它路由暂返回 `NotImplementedError`（Phase 2 接管）。
- [`reposage/cli.py`](../../reposage/cli.py)：`index` 调起 `IndexPipeline`；`ask --route graph` 调起 `SQLiteSymbolGraphStore.callers_of` 并 Rich 表格打印。

## 测试与基准

- `tests/unit/`
  - `test_parser.py`：Python/TS/Go 各跑一段 fixture 字节串，断言 `Tree.root_node.has_error is False`。
  - `test_chunker.py`：构造一个 200 行函数，断言切成 3 块，行号闭区间且重叠 4 行；空文件返回空列表。
  - `test_python_resolver.py`：5 组 `(import_form, call_site, expected_fqn)` 断言。
  - `test_sqlite_graph.py`：roundtrip + `callers_of` 反向邻接命中。
  - `test_chunk_store.py`：`chunk_id` 稳定性 + 同 `(repo, path)` 重写覆盖语义。
- 新增 `tests/fixtures/tiny_python_repo/`：约 12 个文件、1.5 kLOC，含跨模块继承 / 调用 / 导入。
- 新增 `tests/integration/test_index_e2e.py`：跑 `IndexPipeline` over fixture，断言 `manifest.n_symbols / n_edges` 与黄金值一致。
- 新增 `benchmarks/graph_queries/python_30.jsonl`：30 题 `{"question", "expected": [{"path","line"}]}`；新增 `benchmarks/graph_queries/run_eval.py` 输出 precision；CI 中作为 `make bench-graph` 目标，Phase 1 退出指标硬门槛。
- 大仓库性能：`make bench-graph LARGE=1` 跑 50 kLOC 检出（CI 中跳过；本地用），断言 wall time < 60 s。

## TS / Go 行为定义

> 与用户已确认。

Phase 1 遇到 `.ts` / `.tsx` / `.js` / `.jsx` / `.go` 文件时：

1. 调 tree-sitter 解析一次，确保 grammar 不崩。
2. 在 `file_meta` 写一行 `parse_status='unsupported'`，路径与 `mtime` / `file_sha` 一并写入。
3. **不写 `chunks` / `nodes` / `edges`**。
4. Phase 1.5 / Phase 7 接 TS 或 Go resolver 时，扫 `file_meta` 中所有 `parse_status='unsupported'` 行重做。

## 退出验证步骤

按顺序跑：

1. `make lint && make typecheck`
2. `pytest -q tests/unit tests/integration`
3. `make bench-graph` → precision ≥ 0.90
4. `make bench-graph LARGE=1` → wall time < 60 s（本地 4 核）
5. 手工演示：`reposage index --repo tests/fixtures/tiny_python_repo` → `reposage ask --route graph "where is User.login called?"`，输出非空。

## 显式不做（推迟到后续 Phase）

- TypeScript / Go 抽边、嵌入 / HNSW 写入、Leiden / 社区摘要、LLM 生成回答均**不做**；本 Phase 的 `ask --route graph` 直接打表，**不走 LLM**（与 [`docs/DESIGN_DECISIONS.md`](../DESIGN_DECISIONS.md) DD-002 一致）。

## 演示命令

### 一行命令端到端验证

```bash
reposage index --repo tests/fixtures/tiny_python_repo --force \
  && reposage ask "where is User.login called?" --route graph
```

或者通过 `python -m`：

```bash
python -m reposage.cli index --repo tests/fixtures/tiny_python_repo --force \
  && python -m reposage.cli ask "where is User.login called?" --route graph
```

### 退出指标全量回放

```bash
# 1) lint + typecheck
make lint && make typecheck

# 2) 全量 pytest（含 30 题门禁）
make test

# 3) 显式跑一遍 30 题基准（与 pytest 中的 test_graph_bench 同口径）
make bench-graph                  # precision >= 0.90 才退出 0

# 4) 50 kLOC 性能检查（指向任意 50 kLOC+ 的 Python checkout）
REPOSAGE_LARGE_REPO=.venv/lib/python3.12/site-packages/langchain_classic \
  make bench-graph LARGE=1        # wall time < 60s 才退出 0

# 5) Go 侧 race detector
make hnsw-test
```

### 本地几个常用 ask 示例

> 先跑一次 `reposage index --repo tests/fixtures/tiny_python_repo --force`，下面这些命令都会出表格输出。

```bash
reposage ask "where is User.login called?"        --route graph
reposage ask "who calls require_auth?"             --route graph
reposage ask "list callers of Invoice.issue"       --route graph
reposage ask "where is utils.logging.log called?"  --route graph
reposage ask "who calls AdminUser.has_admin_flag?" --route graph
```
