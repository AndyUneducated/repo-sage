"""HnswGrpcClient dim/model validation tests.

Phase 2 plan §3 explicitly requires the client to call ``Stats()`` on its
first connection and reject a server whose dim or model differs from the
client's expectation. This is what stops a Phase 7 model swap from
silently corrupting search results when one side rolls forward and the
other doesn't.

The tests stub `HnswServiceStub` so they do not need a Go binary or even
an open network port.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from reposage.proto import hnsw_pb2
from reposage.retrieval.hnsw_client import HnswGrpcClient


class _FakeStub:
    """Stub for ``HnswServiceStub`` that returns scripted Stats/Search/Add."""

    def __init__(
        self,
        *,
        stats: hnsw_pb2.StatsResponse | None = None,
        search_hits: Sequence[tuple[str, float]] = (),
        add_size: int = 0,
        stats_raises: Exception | None = None,
    ) -> None:
        self._stats = stats
        self._search_hits = list(search_hits)
        self._add_size = add_size
        self._stats_raises = stats_raises
        self.calls: list[str] = []

    async def Stats(self, _req: Any) -> Any:
        self.calls.append("Stats")
        if self._stats_raises is not None:
            raise self._stats_raises
        return self._stats

    async def Search(self, req: Any) -> Any:
        self.calls.append("Search")
        return hnsw_pb2.SearchResponse(
            hits=[hnsw_pb2.SearchHit(id=cid, distance=score) for cid, score in self._search_hits]
        )

    async def Add(self, req: Any) -> Any:
        self.calls.append("Add")
        return hnsw_pb2.AddResponse(size=self._add_size)


class _FakeChannel:
    async def close(self) -> None:
        return None


def _patch_grpc(monkeypatch: pytest.MonkeyPatch, stub: _FakeStub) -> None:
    """Replace `grpc.aio.insecure_channel` and the stub factory."""
    import reposage.retrieval.hnsw_client as mod  # noqa: PLC0415

    monkeypatch.setattr(
        mod.grpc.aio,
        "insecure_channel",
        lambda _addr: _FakeChannel(),
    )
    monkeypatch.setattr(
        mod.hnsw_pb2_grpc,
        "HnswServiceStub",
        lambda _channel: stub,
    )


@pytest.mark.asyncio
async def test_healthcheck_passes_when_dim_and_model_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=10, dim=768, model="test-model"),
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="test-model", expected_dim=768)
    assert await client.healthcheck() is True
    assert "Stats" in stub.calls
    await client.close()


@pytest.mark.asyncio
async def test_healthcheck_returns_false_on_dim_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=0, dim=512, model="test-model"),
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="test-model", expected_dim=768)
    assert await client.healthcheck() is False
    await client.close()


@pytest.mark.asyncio
async def test_search_raises_on_dim_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The dim guard fires the moment any RPC tries to connect."""
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=0, dim=512, model="m"),
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="m", expected_dim=768)
    with pytest.raises(RuntimeError, match="dim=512"):
        await client.search([0.0] * 768, top_k=5)


@pytest.mark.asyncio
async def test_search_raises_on_model_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=0, dim=768, model="server-model"),
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="client-model", expected_dim=768)
    with pytest.raises(RuntimeError, match="model="):
        await client.search([0.0] * 768, top_k=5)


@pytest.mark.asyncio
async def test_empty_server_model_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server may report empty model (legacy case); client must NOT reject."""
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=0, dim=768, model=""),
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="anything", expected_dim=768)
    assert await client.healthcheck() is True


@pytest.mark.asyncio
async def test_search_returns_scored_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=2, dim=4, model="m"),
        search_hits=[("a", 0.9), ("b", 0.5)],
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="m", expected_dim=4)
    out = await client.search([0.1, 0.2, 0.3, 0.4], top_k=2)
    assert [(s.chunk_id, s.score) for s in out] == [
        ("a", pytest.approx(0.9)),
        ("b", pytest.approx(0.5)),
    ]


@pytest.mark.asyncio
async def test_add_returns_size(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _FakeStub(
        stats=hnsw_pb2.StatsResponse(size=0, dim=4, model="m"),
        add_size=5,
    )
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="m", expected_dim=4)
    size = await client.add("chunk-1", [0.0, 0.0, 0.0, 0.0])
    assert size == 5


@pytest.mark.asyncio
async def test_healthcheck_returns_false_when_stats_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _FakeStub(stats_raises=ConnectionRefusedError("nope"))
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="m", expected_dim=4)
    assert await client.healthcheck() is False


@pytest.mark.asyncio
async def test_stats_called_only_once_per_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The client caches the stub after the first connect; Stats is one-shot."""
    stub = _FakeStub(stats=hnsw_pb2.StatsResponse(size=0, dim=4, model="m"))
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="m", expected_dim=4)
    await client.search([0.0] * 4, top_k=1)
    await client.search([0.0] * 4, top_k=1)
    assert stub.calls.count("Stats") == 1


@pytest.mark.asyncio
async def test_close_resets_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    """After close(), a subsequent search re-runs the Stats validation."""
    stub = _FakeStub(stats=hnsw_pb2.StatsResponse(size=0, dim=4, model="m"))
    _patch_grpc(monkeypatch, stub)
    client = HnswGrpcClient(addr="ignored", expected_model="m", expected_dim=4)
    await client.search([0.0] * 4, top_k=1)
    await client.close()
    await client.search([0.0] * 4, top_k=1)
    assert stub.calls.count("Stats") == 2
