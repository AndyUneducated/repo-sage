"""Unit tests for the shared sparse-retrieval tokeniser (Phase 6, DD-035)."""

from __future__ import annotations

from reposage.retrieval import bm25
from reposage.retrieval.tokenize import tokenize


def test_splits_snake_and_dotted_identifiers() -> None:
    assert tokenize("check_password") == ["check", "password"]
    assert tokenize("User.login") == ["user", "login"]
    assert tokenize("require_auth()") == ["require", "auth"]


def test_drops_short_and_numeric_tokens() -> None:
    # single chars dropped, purely-numeric dropped, alnum kept lowercased
    assert tokenize("a HTTP/2 v2 sha256") == ["http", "v2", "sha256"]


def test_camelcase_stays_single_token() -> None:
    assert tokenize("CreditCard") == ["creditcard"]


def test_empty_and_symbol_only() -> None:
    assert tokenize("") == []
    assert tokenize("--- === ...") == []


def test_bm25_reexports_shared_tokenize() -> None:
    # The rank-bm25 backend must use the exact same口径 as Tantivy will.
    assert bm25.tokenize is tokenize
