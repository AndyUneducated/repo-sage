from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from reposage.config import Settings, get_settings


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Iterator[Settings]:
    """Settings rooted at a temp directory; clears the lru_cache when done."""
    s = Settings(
        sqlite_path=tmp_path / "reposage.db",
        hnsw_data_dir=tmp_path / "hnsw",
        bm25_index_dir=tmp_path / "bm25",
    )
    get_settings.cache_clear()
    yield s
    get_settings.cache_clear()
