"""End-to-end gRPC HNSW integration test.

This test boots the compiled `hnsw-server` as a subprocess, indexes the
tiny_python_repo fixture with `HashEmbedder`, then drives a `Search` RPC
against the live server. The whole chain — `EmbeddingsStore` ->
`hnsw-server` SQLite cold-load -> gRPC -> `HnswGrpcClient` — has to work.

Marked `requires_go_hnsw` so the standard `pytest -q` run skips it.
Run via `make test-grpc`.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from reposage.indexer.embedder import HashEmbedder
from reposage.indexer.pipeline import IndexPipeline
from reposage.retrieval.hnsw_client import HnswGrpcClient

pytestmark = pytest.mark.requires_go_hnsw

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "tiny_python_repo"
HNSW_BINARY = REPO_ROOT / "go-hnsw" / "bin" / "hnsw-server"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise TimeoutError(f"hnsw-server didn't bind 127.0.0.1:{port} in {timeout}s")


@pytest.fixture
def served_index(tmp_path: Path) -> Iterator[tuple[Path, str, HashEmbedder]]:
    if not HNSW_BINARY.exists():
        pytest.skip(f"{HNSW_BINARY} not built; run `make hnsw-build`")

    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT, repo)
    db = tmp_path / "index.db"
    embedder = HashEmbedder()
    IndexPipeline(repo=repo, sqlite_path=db, repo_name="tiny", embedder=embedder).run(force=True)

    port = _free_port()
    addr = f"127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            str(HNSW_BINARY),
            "-addr",
            addr,
            "-db",
            str(db),
            "-model",
            embedder.model,
            "-dim",
            str(embedder.dim),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "GOMAXPROCS": "2"},
    )
    try:
        _wait_for_port(port)
        yield db, addr, embedder
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_search_round_trip(served_index: tuple[Path, str, HashEmbedder]) -> None:
    _, addr, embedder = served_index
    client = HnswGrpcClient(
        addr=addr,
        expected_model=embedder.model,
        expected_dim=embedder.dim,
    )
    try:
        assert await client.healthcheck() is True
        # Embed a query that should match something in the fixture.
        vec = embedder.embed(["session timeout user login"])[0].tolist()
        hits = await client.search(vec, top_k=5)
        assert hits, "expected at least one hit from the live server"
        # Hits must be valid chunk_ids (sha1 hex of length 40).
        for h in hits:
            assert len(h.chunk_id) == 40
            assert all(c in "0123456789abcdef" for c in h.chunk_id)
    finally:
        await client.close()
