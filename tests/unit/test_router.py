"""Unit tests for `reposage.retrieval.router`."""

from __future__ import annotations

from reposage.retrieval.router import QueryRouter


def test_detects_dotted_symbol() -> None:
    r = QueryRouter()
    assert r.detect_symbol("where is User.login called?") == "User.login"
    assert r.detect_symbol("does pkg.mod.Foo.bar handle errors?") == "pkg.mod.Foo.bar"


def test_detects_snake_case_function() -> None:
    r = QueryRouter()
    assert r.detect_symbol("where is require_auth called?") == "require_auth"
    assert r.detect_symbol("list callers of make_session please") == "make_session"


def test_detects_call_shape() -> None:
    r = QueryRouter()
    assert r.detect_symbol("how often does login(x) happen?") == "login"


def test_prose_with_no_identifiers_returns_none() -> None:
    r = QueryRouter()
    assert r.detect_symbol("how does authentication work overall?") is None
    assert r.detect_symbol("explain the session lifecycle") is None


def test_route_sync_returns_graph_for_symbolic() -> None:
    r = QueryRouter()
    decision = r.route_sync("where is User.login called?")
    assert decision.name == "graph"
    assert decision.symbol == "User.login"
    assert decision.confidence == 1.0


def test_route_sync_falls_back_to_hybrid_for_non_symbolic() -> None:
    """Phase 2: with no LLM available, the heuristic safety net returns hybrid.

    Phase 1 raised NotImplementedError; Phase 2 makes hybrid the default
    so the CLI/HTTP path can always serve an answer (DD-013 guards quality
    via grounding, not by failing the request).
    """
    r = QueryRouter()
    decision = r.route_sync("how does authentication work overall?")
    assert decision.name == "hybrid"
    assert decision.symbol is None
    assert 0.0 <= decision.confidence <= 1.0
