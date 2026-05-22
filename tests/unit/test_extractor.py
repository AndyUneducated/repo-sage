"""Unit tests for `reposage.indexer.extractor`."""

from __future__ import annotations

from pathlib import Path

from reposage.indexer.extractor import PythonExtractor, module_fqn_for
from reposage.indexer.parser import TreeSitterParser


def _extract(tmp_path: Path, body: bytes, module: str = "pkg.demo"):
    parser = TreeSitterParser()
    path = tmp_path / "demo.py"
    path.write_bytes(body)
    parsed = parser.parse(path)
    assert parsed is not None
    return PythonExtractor().extract(parsed, file_module=module)


def test_module_fqn_for_init(tmp_path: Path) -> None:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    init = pkg / "__init__.py"
    init.write_bytes(b"")
    assert module_fqn_for(tmp_path, init) == "pkg"


def test_module_fqn_for_nested(tmp_path: Path) -> None:
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    f = tmp_path / "pkg" / "sub" / "mod.py"
    f.write_bytes(b"")
    assert module_fqn_for(tmp_path, f) == "pkg.sub.mod"


def test_extracts_module_class_method_function(tmp_path: Path) -> None:
    out = _extract(
        tmp_path,
        b"def top():\n    return 1\n\nclass C:\n    def m(self):\n        return 2\n",
    )
    kinds = sorted((d.kind, d.fqn) for d in out.defs)
    assert ("module", "pkg.demo") in kinds
    assert ("function", "pkg.demo.top") in kinds
    assert ("class", "pkg.demo.C") in kinds
    assert ("method", "pkg.demo.C.m") in kinds


def test_inheritance_edge_recorded(tmp_path: Path) -> None:
    out = _extract(tmp_path, b"class Base:\n    pass\nclass Child(Base):\n    pass\n")
    inherit = [e for e in out.edges if e.kind == "inherit"]
    assert len(inherit) == 1
    assert inherit[0].src_fqn == "pkg.demo.Child"
    assert inherit[0].dst_local == "Base"


def test_call_edge_attributed_to_enclosing_function(tmp_path: Path) -> None:
    out = _extract(
        tmp_path,
        b"def caller():\n    return helper()\n\ndef helper():\n    return 1\n",
    )
    calls = [e for e in out.edges if e.kind == "call"]
    assert len(calls) == 1
    assert calls[0].src_fqn == "pkg.demo.caller"
    assert calls[0].dst_local == "helper"


def test_imports_record_all_three_forms(tmp_path: Path) -> None:
    out = _extract(
        tmp_path,
        b"import os\nimport os.path as op\nfrom collections import OrderedDict, deque as dq\n",
    )
    bindings = {(b.local_name, b.target_fqn) for b in out.imports}
    assert ("os", "os") in bindings
    assert ("op", "os.path") in bindings
    assert ("OrderedDict", "collections.OrderedDict") in bindings
    assert ("dq", "collections.deque") in bindings
    # Each statement also produces an `import` edge.
    import_edges = [e for e in out.edges if e.kind == "import"]
    assert len(import_edges) == 3


def test_self_dot_calls_attribute_to_method(tmp_path: Path) -> None:
    out = _extract(
        tmp_path,
        b"class C:\n    def a(self):\n        return self.b()\n    def b(self):\n        return 1\n",
    )
    self_calls = [e for e in out.edges if e.kind == "call" and "self" in e.dst_local]
    assert any(e.src_fqn == "pkg.demo.C.a" and e.dst_local == "self.b" for e in self_calls)
