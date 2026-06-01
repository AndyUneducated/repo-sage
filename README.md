# RepoSage

> 仓库级代码问答系统，采用**双索引**设计（Symbol Graph + GraphRAG），并搭配一个**从零自研的 Go HNSW** 向量存储。以 GitHub App 的形式交付。

[![CI · Python](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-python.yml/badge.svg)](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-python.yml)
[![CI · Go HNSW](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-go.yml/badge.svg)](https://github.com/AndyUneducated/repo-sage/actions/workflows/ci-go.yml)
[![Lint](https://github.com/AndyUneducated/repo-sage/actions/workflows/lint.yml/badge.svg)](https://github.com/AndyUneducated/repo-sage/actions/workflows/lint.yml)
[![codecov](https://img.shields.io/badge/coverage-pending-lightgrey.svg)](https://codecov.io)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Go 1.22+](https://img.shields.io/badge/go-1.22+-00ADD8.svg?logo=go&logoColor=white)](https://go.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![tree-sitter](https://img.shields.io/badge/tree--sitter-AST-green.svg)](https://tree-sitter.github.io/)
[![SQLite](https://img.shields.io/badge/SQLite-symbol_graph-003B57.svg?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![HNSW](https://img.shields.io/badge/HNSW-from_scratch-orange.svg)](./go-hnsw)
[![GraphRAG](https://img.shields.io/badge/GraphRAG-Leiden-purple.svg)](https://microsoft.github.io/graphrag/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-traced-425CC7.svg?logo=opentelemetry&logoColor=white)](https://opentelemetry.io/)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen.svg?logo=pre-commit)](https://pre-commit.com/)

---

## 为什么做 RepoSage

新人加入一个 50 万行的代码仓库后，会问出三类完全不同的问题。一种工具答不好全部三类，所以 RepoSage 给每一类配一条专门的检索路径（route）：

| 问题类型 | 它真正想要什么 | 为什么纯向量 RAG（vector RAG）不够 | 对应路径 |
| --- | --- | --- | --- |
| *"`User.login()` 在哪些地方被调用？"* | 确定的事实查询（graph query） | top-k 相似度会漏掉跨文件、反射式的调用边——这是事实题，不是语义题 | **graph** |
| *"改 session timeout 要动哪里？"* | 跨文件、跨片段的语义检索 | 单靠原始 embedding 不准，需要混合检索 + 重排序器（reranker） | **hybrid** |
| *"auth 模块和 billing 模块怎么通信？"* | 模块级（module-level）的归纳总结 | 5–10 个 chunk 拼不出一条模块边界，需要先把模块"摘要"好 | **community** |

一句话：**先判断问题属于哪一类，再走对应的索引**，而不是把所有问题都塞进同一个向量库硬答。

下面这张图是「一个问题进来后怎么被回答」的全流程：

```mermaid
flowchart TD
  Q["用户提问"] --> R{"Query Router<br/>判断问题类型"}
  R -->|"含具体符号 (FQN)<br/>如 User.login"| G["graph 路径<br/>查 SQLite 邻接表"]
  R -->|"模块级归纳问题"| C["community 路径<br/>取相关社区摘要"]
  R -->|"其它语义问题 (兜底)"| H["hybrid 路径<br/>HNSW + BM25 + reranker"]
  G --> A["拼装上下文 → LLM<br/>生成带 file:line 引用的答案"]
  C --> A
  H --> A
  A --> V["Grounding 校验<br/>引用必须真实存在"]
  V --> OUT["GitHub App 回复 / CLI 输出"]
```

## 整体架构（一张图）

```mermaid
graph LR
  subgraph Indexer
    P[tree-sitter Parser] --> C[Chunker]
    C --> E[Embedder bge-en-v1.5]
    P --> S[Symbol Graph<br/>def · call · inherit · import]
    S --> G[GraphRAG<br/>Leiden community detection]
  end
  subgraph Stores
    HN[(go-hnsw<br/>self-built, mmap)]
    BM[(BM25)]
    SG[(SQLite<br/>symbol graph)]
    SU[(Community summaries)]
  end
  E --> HN
  C --> BM
  S --> SG
  G --> SU
  subgraph Online
    Q[Query Router] --> HY[Hybrid Retrieval<br/>HNSW + BM25 + RRF + reranker]
    Q --> SQ[Symbol-graph adjacency]
    Q --> CS[Community summary]
    HY & SQ & CS --> L[LLM] --> CITE[file:line citations] --> BOT[GitHub App reply]
  end
  HN -.- HY
  BM -.- HY
  SG -.- SQ
  SU -.- CS
```

完整的架构长文见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
按阶段拆分的交付计划见 [`docs/ROADMAP.md`](docs/ROADMAP.md)。
关键设计取舍见 [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md)。
基准测试（HNSW vs Faiss on SIFT-1M、200 题跨文件 QA）见 [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md)。

## 有什么新东西

| 亮点 | 说明 |
| --- | --- |
| **`go-hnsw/`：从零自研的 HNSW** | 用 Go 实现 Malkov & Yashunin 2018 的 HNSW，带 `mmap` 持久化。在 SIFT-1M 上与 Faiss 做对照，沿 `M` / `efConstruction` / `efSearch` 报告 QPS、内存、P99 延迟。本身是一个可独立 `go get` 的 Go module。 |
| **双索引检索（dual-index）** | Symbol Graph（确定性）+ GraphRAG 社区摘要（聚合）+ 向量/BM25 混合检索（语义兜底），由一个轻量 query router 选路。**目标**：在自建 200 题跨文件基准上，相比纯向量基线把回答准确率提升 ≥ 25%（见 [ROADMAP](docs/ROADMAP.md) Phase 3 退出指标；实测数字以 [BENCHMARKS](docs/BENCHMARKS.md) 为唯一来源，当前为 pending）。 |
| **自建评测 harness** | 横跨 Python / TypeScript / Go、共 200 题人工标注的跨文件问答集；用 Ragas + 自定义引用对齐校验打分，并接入 CI 作为回归门（regression gate）。 |
| **代码智能栈的"读"侧** | 与负责"写"侧（重构 / mutation）的姐妹项目共享同一份索引格式。 |

## 快速开始

> 完整的安装步骤（包括模型下载、tree-sitter 语法）会在 Phase 1 加到 `docs/SETUP.md`。

```bash
# 1. 克隆 & 安装 Python 依赖（推荐 uv）
git clone https://github.com/AndyUneducated/repo-sage.git
cd repo-sage
make install

# 2. 编译 Go HNSW 服务
make hnsw-build

# 3. 启动本地 dev 栈（FastAPI + go-hnsw + SQLite）
make dev

# 4. 给一个仓库建索引
python -m reposage.cli index --repo /path/to/your/repo

# 5. 问问题
python -m reposage.cli ask "where is User.login called?"
```

> **零密钥起步**：默认 profile 是 `mock`（全部用确定性假实现，不需要任何 API key 或 Go 二进制），适合第一次跑通流程。换成 `local`（真实模型、本地 Ollama）或 `production`（接 gRPC + 云端 LLM）只需改一个环境变量 `REPOSAGE_PROFILE`，详见 [`docs/SETUP.md`](docs/SETUP.md)。

## 仓库结构

```
repo-sage/
├── reposage/               # Python 服务（FastAPI、indexer、retrieval、bot）
│   ├── api/                # FastAPI 路由与 schema
│   ├── indexer/            # tree-sitter 解析、chunking、embedding、symbol graph
│   │   └── graphrag/       # Leiden 社区检测 + LLM 摘要
│   ├── retrieval/          # 混合检索、query router、reranker
│   ├── storage/            # SQLite symbol graph、community store
│   ├── bot/                # GitHub App webhook + 引用构造器
│   ├── llm/                # 基于 LiteLLM 的多 provider 客户端
│   └── observability/      # OpenTelemetry 接线
├── go-hnsw/                # 自建的 HNSW Go module（可独立 OSS）
│   ├── cmd/server/         # 提供给 Python 端的 gRPC / HTTP server
│   └── cmd/bench/          # SIFT-1M 基准测试 harness
├── benchmarks/
│   ├── cross_file_qa/      # 200 题跨文件 QA 基准 + Ragas
│   └── sift1m/             # ANN 基准（HNSW vs Faiss）
├── docs/                   # 架构、roadmap、决策、基准
├── scripts/                # 一次性的 dev 脚本
└── .github/workflows/      # CI: Python / Go / lint / eval-gate
```

## 项目状态

项目正在积极开发中。按阶段的交付计划见 [`docs/ROADMAP.md`](docs/ROADMAP.md)，进行中的里程碑见 issue tracker。

## 贡献

欢迎 issue / PR — 详细流程见 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## License

Apache 2.0，详见 [`LICENSE`](./LICENSE)。
