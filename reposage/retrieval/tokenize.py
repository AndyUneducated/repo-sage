"""Shared code-aware tokeniser for sparse retrieval.

Extracted so the rank-bm25 backend (`bm25.py`) and the Phase 6 Tantivy
backend share one tokenisation contract — otherwise swapping the sparse engine
would silently shift recall (DD-035).

Tokenisation is intentionally aggressive: code identifiers like
``User.login`` and ``require_auth`` must split into ``[user, login]`` and
``[require, auth]`` so they can score against natural-language questions.
We split on every non-alphanumeric character, lowercase, drop tokens shorter
than 2 characters, and drop purely-numeric tokens.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Tokenise code-shaped text into sparse-retrieval tokens.

    ``check_password`` -> ``[check, password]``; ``CreditCard`` ->
    ``[creditcard]`` (single CamelCase token; cheap and good enough);
    ``HTTP/2`` -> ``[http]`` (the ``2`` is dropped as purely-numeric).
    """
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1 and not t.isdigit()]
