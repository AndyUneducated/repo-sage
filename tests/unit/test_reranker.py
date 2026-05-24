"""Reranker protocol tests.

Two implementations live behind the `Reranker` protocol:

* `MockReranker`: deterministic lexical-overlap scorer used by tests and
  the offline ``REPOSAGE_LLM_PROVIDER=mock`` mode. Cheap and dependency-free.
* `CrossEncoderReranker`: production wrapper around a sentence-transformers
  `CrossEncoder`. We do not download the 350 MB model in CI, so this test
  monkeypatches the import to a stub that returns scripted scores.

Both implementations must:

1. Return at most `top_k` results.
2. Sort descending by score.
3. Return `[]` for an empty candidate list (no model load).
4. Preserve the input `chunk_id` mapping.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Sequence

import pytest
from reposage.retrieval.reranker import CrossEncoderReranker, MockReranker

# ---------------------------------------------------------------- MockReranker


def test_mock_reranker_orders_by_lexical_overlap() -> None:
    rer = MockReranker()
    out = rer.rerank(
        "session timeout",
        [
            ("a", "billing module"),
            ("b", "session timeout helper"),
            ("c", "session middleware"),
        ],
        top_k=10,
    )
    ids = [s.chunk_id for s in out]
    # "session timeout" → b matches both tokens, c matches one, a matches zero.
    assert ids[0] == "b"
    assert "c" in ids[:2]
    assert ids[-1] == "a"


def test_mock_reranker_top_k_truncates() -> None:
    rer = MockReranker()
    out = rer.rerank(
        "x",
        [(f"c{i}", "x" + " filler" * i) for i in range(20)],
        top_k=8,
    )
    assert len(out) == 8


def test_mock_reranker_empty_candidates_returns_empty() -> None:
    rer = MockReranker()
    assert rer.rerank("anything", [], top_k=8) == []


def test_mock_reranker_scores_are_descending() -> None:
    rer = MockReranker()
    out = rer.rerank(
        "alpha beta gamma",
        [
            ("a", "alpha"),
            ("b", "alpha beta"),
            ("c", "alpha beta gamma"),
        ],
        top_k=3,
    )
    scores = [s.score for s in out]
    assert scores == sorted(scores, reverse=True)
    assert out[0].chunk_id == "c"


def test_mock_reranker_preserves_chunk_ids() -> None:
    rer = MockReranker()
    out = rer.rerank("q", [("hash-aaa", "q text"), ("hash-bbb", "other")], top_k=2)
    assert {s.chunk_id for s in out} == {"hash-aaa", "hash-bbb"}


def test_mock_reranker_model_property_stable() -> None:
    rer = MockReranker()
    assert rer.model == "mock-reranker-v1"


# -------------------------------------------------------- CrossEncoderReranker


class _FakeCrossEncoder:
    """Stand-in for ``sentence_transformers.CrossEncoder`` in tests.

    Records the (model_name, pairs) it received and returns a scripted
    score per pair so the reranker logic can be inspected.
    """

    last_pairs: list[list[str]] | None = None
    last_model: str | None = None
    scripted_scores: Sequence[float] = ()

    def __init__(self, model_name: str) -> None:
        type(self).last_model = model_name

    def predict(self, pairs: Sequence[Sequence[str]]) -> list[float]:
        type(self).last_pairs = [list(p) for p in pairs]
        return list(type(self).scripted_scores[: len(pairs)])


@pytest.fixture
def fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch):
    """Inject a fake `sentence_transformers` module before reranker import."""
    fake = types.ModuleType("sentence_transformers")
    fake.CrossEncoder = _FakeCrossEncoder  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
    yield _FakeCrossEncoder
    _FakeCrossEncoder.last_pairs = None
    _FakeCrossEncoder.last_model = None
    _FakeCrossEncoder.scripted_scores = ()


def test_cross_encoder_reranker_uses_configured_model(fake_sentence_transformers) -> None:
    rer = CrossEncoderReranker(model_name="my/test-encoder")
    fake_sentence_transformers.scripted_scores = [0.9, 0.1]
    out = rer.rerank("q", [("a", "x"), ("b", "y")], top_k=2)
    assert fake_sentence_transformers.last_model == "my/test-encoder"
    assert [s.chunk_id for s in out] == ["a", "b"]
    assert rer.model == "my/test-encoder"


def test_cross_encoder_reranker_orders_by_predicted_score(fake_sentence_transformers) -> None:
    rer = CrossEncoderReranker(model_name="dummy")
    fake_sentence_transformers.scripted_scores = [0.1, 0.9, 0.5]
    out = rer.rerank("q", [("a", "x"), ("b", "y"), ("c", "z")], top_k=3)
    assert [s.chunk_id for s in out] == ["b", "c", "a"]
    # Scores must travel through unmodified.
    assert out[0].score == pytest.approx(0.9)


def test_cross_encoder_reranker_top_k_caps(fake_sentence_transformers) -> None:
    rer = CrossEncoderReranker(model_name="dummy")
    fake_sentence_transformers.scripted_scores = [float(i) for i in range(20)]
    cands = [(f"c{i}", "text") for i in range(20)]
    out = rer.rerank("q", cands, top_k=5)
    assert len(out) == 5
    # Highest 5 indices: 19, 18, 17, 16, 15.
    assert [s.chunk_id for s in out] == [f"c{i}" for i in [19, 18, 17, 16, 15]]


def test_cross_encoder_reranker_empty_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty candidate list must not even trigger a model load."""

    # Fail loudly if the reranker tries to import sentence_transformers.
    def _explode(*_: object, **__: object) -> object:
        raise AssertionError("model must not be loaded for empty candidates")

    fake = types.ModuleType("sentence_transformers")
    fake.CrossEncoder = _explode  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake)

    rer = CrossEncoderReranker(model_name="dummy")
    assert rer.rerank("q", [], top_k=8) == []


def test_cross_encoder_reranker_passes_query_in_each_pair(
    fake_sentence_transformers,
) -> None:
    rer = CrossEncoderReranker(model_name="dummy")
    fake_sentence_transformers.scripted_scores = [0.5, 0.5]
    rer.rerank("the question", [("a", "text-a"), ("b", "text-b")], top_k=2)
    pairs = fake_sentence_transformers.last_pairs
    assert pairs is not None
    assert pairs[0][0] == "the question"
    assert pairs[1][0] == "the question"
    assert {pairs[0][1], pairs[1][1]} == {"text-a", "text-b"}
