"""gRPC/HTTP client to the in-house `go-hnsw` server.

We isolate transport here so that the Python side never imports a vector
library directly — everything goes through the Go service we wrote.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from reposage.config import get_settings


class HNSWClient:
    def __init__(self, addr: str | None = None) -> None:
        settings = get_settings()
        self.addr = addr or settings.hnsw_grpc_addr

    async def upsert(self, ids: Sequence[str], vectors: np.ndarray) -> None:
        raise NotImplementedError

    async def search(
        self,
        query: np.ndarray,
        top_k: int = 50,
        ef_search: int | None = None,
    ) -> list[tuple[str, float]]:
        raise NotImplementedError

    async def healthcheck(self) -> bool:
        raise NotImplementedError
