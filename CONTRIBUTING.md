# 贡献指南 / Contributing

欢迎为 RepoSage 提 issue 或 PR —— 尤其是这些方向：

- 给 `go-hnsw/` 写更多 ANN 基准（高维 / 大规模 / 不同分布）。
- 在 `benchmarks/cross_file_qa/` 里补充新语言（Rust / Java / Kotlin …）的问答样本。
- 改进 GraphRAG 社区摘要的质量（更好的 Leiden 参数、更好的摘要 prompt）。
- 接入新的 LLM provider（通过 `reposage.llm` 的 LiteLLM 抽象）。

## 1. 本地开发环境

```bash
# 克隆 & 安装 Python 依赖（推荐 uv）
git clone https://github.com/AndyUneducated/repo-sage.git
cd repo-sage
make install

# 编译 Go HNSW
make hnsw-build

# 启动本地 dev 栈
make dev
```

要求：

- Python 3.12+（推荐用 [`uv`](https://docs.astral.sh/uv/) 管理）
- Go 1.22+
- SQLite（系统自带即可）
- 本地 LLM 推理：可选 Ollama，或在 `.env` 里配 LiteLLM 兼容的云端 provider

## 2. 提交前自检（与 CI 一致）

```bash
make lint        # ruff + go vet + gofmt
make typecheck   # mypy + go build
make test        # pytest + go test ./...
```

`pre-commit` 钩子在仓库根目录已就位，安装一次即可：

```bash
pre-commit install
```

## 3. 写代码的几条约定

- **不要把生成的索引产物 commit 进来。** `*.db`、`*.idx`、embedding cache 都已经在 `.gitignore`。
- **跨进程 / 跨语言的边界要写契约**。Python 与 `go-hnsw` 之间的协议改动需同步两侧测试。
- **重大设计决策**写到 [`docs/DESIGN_DECISIONS.md`](docs/DESIGN_DECISIONS.md)，按 ADR 风格追加；阶段性进展写到 [`docs/ROADMAP.md`](docs/ROADMAP.md)。
- **基准结果**要可复现：在 [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) 写清硬件、参数、随机种子。

## 4. Commit 信息

- 用英文简短说明，遵循 conventional commits 风格（`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`）。
- 一个 commit 只做一件事；跨子模块的改动尽量拆开。
- PR 描述里包含：**为什么改 / 改了什么 / 怎么测**。

## 5. CI 必须通过

每个 PR 都会跑以下 workflow，全部要绿才会合：

- `ci-python.yml`（Ruff + mypy + pytest）
- `ci-go.yml`（`go vet` + `go test ./...`）
- `lint.yml`（pre-commit）

如果你的改动会修改 ANN 基准或 cross-file QA 评测结果，请在 PR 描述里贴上前后对比。

## 6. 较大的提案

如果你打算加新的索引（除现有的 HNSW / BM25 / Symbol Graph / GraphRAG 之外）、
换 embedding 模型、或重写 query router，**先开 issue 讨论**，避免你写了很多代码后才发现方向不合。

—— 感谢贡献！
