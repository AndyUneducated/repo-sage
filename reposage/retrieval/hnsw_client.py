"""Python gRPC client for the in-house ``hnsw-server``.

The client implements `DenseRetriever`. Phase 6 will swap the underlying
transport for a streaming Search RPC; the public interface is what we
preserve.

We use grpc.aio so the FastAPI request handler can run dense + sparse
retrieval concurrently (the whole point of the hybrid retriever).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence

import grpc

from reposage.config import get_settings
from reposage.proto import hnsw_pb2, hnsw_pb2_grpc
from reposage.retrieval.protocols import ScoredId


class HnswGrpcClient:
    """Async gRPC client. Lazy-connects on first use; reused across requests."""

    def __init__(
        self,
        addr: str | None = None,
        *,
        expected_model: str | None = None,
        expected_dim: int | None = None,
    ) -> None:
        settings = get_settings()
        self.addr = addr or settings.hnsw_grpc_addr
        self._expected_model = expected_model or settings.embed_model
        self._expected_dim = expected_dim or settings.embed_dim
        self._channel: grpc.aio.Channel | None = None
        self._stub: hnsw_pb2_grpc.HnswServiceStub | None = None
        self._stats: hnsw_pb2.StatsResponse | None = None

    @property
    def model(self) -> str:
        return self._expected_model

    @property
    def dim(self) -> int:
        return self._expected_dim

    async def _connect(self) -> hnsw_pb2_grpc.HnswServiceStub:
        if self._stub is not None:
            return self._stub
        self._channel = grpc.aio.insecure_channel(self.addr)
        self._stub = hnsw_pb2_grpc.HnswServiceStub(self._channel)  # type: ignore[no-untyped-call]
        # Sanity-check: model and dim must match before we ever ask the
        # server for a search. Phase 5/7 model swaps will rely on this
        # contract.
        stats = await self._stub.Stats(hnsw_pb2.StatsRequest())
        if stats.dim != self._expected_dim:
            raise RuntimeError(
                f"hnsw-server dim={stats.dim} != client expected={self._expected_dim}"
            )
        if stats.model and stats.model != self._expected_model:
            raise RuntimeError(
                f"hnsw-server model={stats.model!r} != client expected={self._expected_model!r}"
            )
        self._stats = stats
        return self._stub

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None
        self._stats = None

    async def healthcheck(self) -> bool:
        try:
            await self._connect()
        except Exception:
            return False
        return True

    async def search(
        self,
        query_vector: Sequence[float],
        top_k: int = 50,
        ef_search: int | None = None,
    ) -> list[ScoredId]:
        stub = await self._connect()
        req = hnsw_pb2.SearchRequest(
            vector=list(query_vector),
            top_k=top_k,
            ef_search=ef_search or 0,
        )
        resp = await stub.Search(req)
        return [ScoredId(chunk_id=h.id, score=h.distance) for h in resp.hits]

    async def add(self, chunk_id: str, vector: Sequence[float]) -> int:
        stub = await self._connect()
        resp = await stub.Add(hnsw_pb2.AddRequest(id=chunk_id, vector=list(vector)))
        return int(resp.size)

    async def bulk_load(self, items: Iterable[tuple[str, Sequence[float]]]) -> int:
        """Stream many vectors in one client-streaming ``BulkLoad`` RPC.

        The server buffers and inserts them via ``Index.AddBatch`` (one write
        lock per flush), so this is the batch-upsert path the indexer should
        use for a cold load instead of a per-vector ``add`` loop. Returns the
        number of vectors the server reports inserted.
        """
        stub = await self._connect()

        async def _requests() -> AsyncIterator[hnsw_pb2.AddRequest]:
            for chunk_id, vector in items:
                yield hnsw_pb2.AddRequest(id=chunk_id, vector=list(vector))

        resp = await stub.BulkLoad(_requests())
        return int(resp.inserted)


# Backwards-compatible alias kept for code that imported the old stub name.
HNSWClient = HnswGrpcClient
