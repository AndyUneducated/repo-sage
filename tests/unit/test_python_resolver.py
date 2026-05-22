"""Unit tests for `reposage.indexer.python_resolver`."""

from __future__ import annotations

from pathlib import Path

from reposage.indexer.extractor import PythonExtractor, module_fqn_for
from reposage.indexer.parser import TreeSitterParser
from reposage.indexer.python_resolver import PythonModuleResolver, is_unresolved, unresolved


def _resolve(tmp_path: Path, files: dict[str, bytes]):
    parser = TreeSitterParser()
    extractor = PythonExtractor()
    extractions = []
    for rel, body in files.items():
        full = tmp_path / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(body)
        parsed = parser.parse(full)
        if parsed is None:
            continue
        extractions.append(extractor.extract(parsed, file_module=module_fqn_for(tmp_path, full)))
    return PythonModuleResolver(repo="demo").resolve(extractions)


def test_call_via_import_resolves_to_full_fqn(tmp_path: Path) -> None:
    graph = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/auth.py": b"class User:\n    def login(self):\n        return None\n",
            "pkg/api.py": b"from pkg.auth import User\ndef call_login(u):\n    return User.login(u)\n",
        },
    )
    call_edges = [e for e in graph.edges if e.kind == "call"]
    targets = [e.dst for e in call_edges]
    assert "pkg.auth.User.login" in targets


def test_aliased_import_resolves(tmp_path: Path) -> None:
    graph = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/auth.py": b"class User:\n    def login(self):\n        return None\n",
            "pkg/api.py": b"from pkg.auth import User as U\ndef call_login(u):\n    return U.login(u)\n",
        },
    )
    targets = {e.dst for e in graph.edges if e.kind == "call"}
    assert "pkg.auth.User.login" in targets


def test_self_dot_method_resolves(tmp_path: Path) -> None:
    graph = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/auth.py": b"class C:\n    def a(self):\n        return self.b()\n    def b(self):\n        return 1\n",
        },
    )
    targets = {e.dst for e in graph.edges if e.kind == "call"}
    assert "pkg.auth.C.b" in targets


def test_inherit_edge_resolves(tmp_path: Path) -> None:
    graph = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/auth.py": b"class Base:\n    pass\nclass Child(Base):\n    pass\n",
        },
    )
    inherits = {(e.src, e.dst) for e in graph.edges if e.kind == "inherit"}
    assert ("pkg.auth.Child", "pkg.auth.Base") in inherits


def test_unresolved_local_variable_call_is_marked(tmp_path: Path) -> None:
    graph = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/auth.py": b"class User:\n    def login(self):\n        return None\n",
            "pkg/api.py": (
                b"from pkg.auth import User\n"
                b"def callsite():\n"
                b"    u = User()\n"
                b"    return u.login()\n"
            ),
        },
    )
    targets = [e.dst for e in graph.edges if e.kind == "call"]
    # `u.login` cannot be resolved without type inference.
    assert any(is_unresolved(t) and "u.login" in t for t in targets)


def test_def_edges_synthesised_from_fqn_chain(tmp_path: Path) -> None:
    graph = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": b"",
            "pkg/auth.py": b"class User:\n    def login(self):\n        return None\n",
        },
    )
    def_edges = {(e.src, e.dst) for e in graph.edges if e.kind == "def"}
    assert ("pkg.auth", "pkg.auth.User") in def_edges
    assert ("pkg.auth.User", "pkg.auth.User.login") in def_edges


def test_unresolved_helpers() -> None:
    assert is_unresolved(unresolved("foo")) is True
    assert is_unresolved("pkg.foo") is False
