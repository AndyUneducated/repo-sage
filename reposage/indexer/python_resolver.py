"""Two-pass FQN resolver for Python.

Pass 1 — collect every `RawDef` from every file into a global FQN table.

Pass 2 — for each file, walk its `RawEdge`s and turn the unresolved
``dst_local`` into a real FQN by consulting:

1. The file's import bindings (``ImportBinding`` rows).
2. The file's own top-level defs (``module.X`` is in scope inside its module).
3. ``self.X`` and ``cls.X`` shortcuts when the call site lives inside a
   class method.

Anything we cannot resolve is emitted with destination
``<unresolved:original_name>`` so downstream consumers can still count it
and the GraphRAG community step has something to bucket. We do not run
type inference, so calls on locally-bound names (e.g. ``u = User(); u.x()``)
remain unresolved by design.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from reposage.indexer.extractor import FileExtraction, RawDef, RawEdge
from reposage.indexer.symbol_graph import SymbolEdge, SymbolNode

UNRESOLVED_PREFIX = "<unresolved:"
UNRESOLVED_SUFFIX = ">"


def unresolved(name: str) -> str:
    return f"{UNRESOLVED_PREFIX}{name}{UNRESOLVED_SUFFIX}"


def is_unresolved(fqn: str) -> bool:
    return fqn.startswith(UNRESOLVED_PREFIX) and fqn.endswith(UNRESOLVED_SUFFIX)


@dataclass(slots=True, frozen=True)
class ResolvedGraph:
    nodes: tuple[SymbolNode, ...]
    edges: tuple[SymbolEdge, ...]


class PythonModuleResolver:
    """Resolve Python `RawEdge`s into `SymbolEdge`s with real FQNs."""

    def __init__(self, repo: str) -> None:
        self.repo = repo

    def resolve(self, extractions: Sequence[FileExtraction]) -> ResolvedGraph:
        # ---- Pass 1: global symbol table ---------------------------------
        all_fqns: set[str] = set()
        # Map module FQN -> set of locally-defined names (top-level, plus
        # classes; method names are *not* exposed at module level).
        module_locals: dict[str, set[str]] = {}
        nodes: list[SymbolNode] = []

        for ext in extractions:
            mod = ext.file_module
            module_locals.setdefault(mod, set())
            for d in ext.defs:
                all_fqns.add(d.fqn)
                nodes.append(self._to_node(d))
                if (
                    d.kind in ("class", "function")
                    and d.fqn.startswith(f"{mod}.")
                    and "." not in d.fqn[len(mod) + 1 :]
                ):
                    module_locals[mod].add(d.fqn[len(mod) + 1 :])
                elif d.kind == "module":
                    pass  # already captured by mod itself

        # ---- Pass 2: per-file resolution ---------------------------------
        edges: list[SymbolEdge] = []
        for ext in extractions:
            edges.extend(self._resolve_file(ext, all_fqns, module_locals))

        # Synthesise `def` edges from FQN parent chains. These are
        # deterministic: for every node `pkg.a.b`, parent is `pkg.a` (if it
        # exists as another node).
        edges.extend(self._derive_def_edges(nodes))

        return ResolvedGraph(nodes=tuple(nodes), edges=tuple(edges))

    def _to_node(self, d: RawDef) -> SymbolNode:
        return SymbolNode(
            fqn=d.fqn,
            kind=d.kind,
            language="python",
            repo=self.repo,
            path=d.src_path,
            start_line=d.start_line,
            end_line=d.end_line,
        )

    def _resolve_file(
        self,
        ext: FileExtraction,
        all_fqns: set[str],
        module_locals: dict[str, set[str]],
    ) -> Iterable[SymbolEdge]:
        local_table = self._build_local_table(ext, all_fqns, module_locals)
        # Per-class method index: class_fqn -> set(method_name)
        # Used for resolving `self.X` and `cls.X` calls.
        class_method_index = self._build_class_method_index(ext)
        # Per-call enclosing class lookup: src_fqn -> class_fqn (if method)
        method_to_class = self._build_method_to_class(ext)

        for raw in ext.edges:
            yield from self._resolve_edge(
                raw,
                local_table=local_table,
                method_to_class=method_to_class,
                class_method_index=class_method_index,
                all_fqns=all_fqns,
            )

    def _build_local_table(
        self,
        ext: FileExtraction,
        all_fqns: set[str],
        module_locals: dict[str, set[str]],
    ) -> dict[str, str]:
        """Local name -> resolved FQN prefix for this file."""
        table: dict[str, str] = {}
        # The file's own top-level defs.
        own_locals = module_locals.get(ext.file_module, set())
        for name in own_locals:
            table[name] = f"{ext.file_module}.{name}" if ext.file_module else name
        # Imports override own locals if there's a clash; standard Python
        # behaviour is the last binding wins, but we treat imports as later.
        for ib in ext.imports:
            table[ib.local_name] = ib.target_fqn
        return table

    def _build_class_method_index(self, ext: FileExtraction) -> dict[str, set[str]]:
        idx: dict[str, set[str]] = {}
        for d in ext.defs:
            if d.kind != "method":
                continue
            class_fqn = d.fqn.rsplit(".", 1)[0]
            method_name = d.fqn.rsplit(".", 1)[1]
            idx.setdefault(class_fqn, set()).add(method_name)
        return idx

    def _build_method_to_class(self, ext: FileExtraction) -> dict[str, str]:
        m2c: dict[str, str] = {}
        for d in ext.defs:
            if d.kind == "method":
                m2c[d.fqn] = d.fqn.rsplit(".", 1)[0]
        return m2c

    def _resolve_edge(
        self,
        raw: RawEdge,
        *,
        local_table: dict[str, str],
        method_to_class: dict[str, str],
        class_method_index: dict[str, set[str]],
        all_fqns: set[str],
    ) -> Iterable[SymbolEdge]:
        if raw.kind == "import":
            # Import dst is already the full module path; we keep it verbatim
            # whether or not it appears in `all_fqns` (third-party imports are
            # legitimate but not indexed).
            _ = all_fqns  # kept for symmetry with other branches
            yield self._make_edge(raw, raw.dst_local)
            return

        if raw.kind == "inherit":
            yield self._make_edge(raw, self._resolve_dotted(raw.dst_local, local_table, all_fqns))
            return

        # call
        callee = raw.dst_local
        # 1) self.X / cls.X — only valid inside a method.
        if callee.startswith(("self.", "cls.")):
            class_fqn = method_to_class.get(raw.src_fqn)
            if class_fqn is not None:
                _, _, method_name = callee.partition(".")
                # Walk up to support `self.foo.bar` chain — we only resolve the first hop.
                first_hop, _, rest = method_name.partition(".")
                if first_hop in class_method_index.get(class_fqn, set()):
                    target = f"{class_fqn}.{first_hop}"
                    yield self._make_edge(raw, target if not rest else unresolved(callee))
                    return
            yield self._make_edge(raw, unresolved(callee))
            return

        # 2) dotted path through local table
        yield self._make_edge(raw, self._resolve_dotted(callee, local_table, all_fqns))

    def _resolve_dotted(
        self,
        dotted: str,
        local_table: dict[str, str],
        all_fqns: set[str],
    ) -> str:
        head, _, rest = dotted.partition(".")
        prefix = local_table.get(head)
        if prefix is None:
            return unresolved(dotted)
        candidate = prefix if not rest else f"{prefix}.{rest}"
        # Accept the candidate even if not in `all_fqns` — third-party libraries
        # are real, just not indexed. We only mark as unresolved when the head
        # is unknown (no import + no local def).
        return candidate if candidate else unresolved(dotted)

    def _make_edge(self, raw: RawEdge, dst: str) -> SymbolEdge:
        return SymbolEdge(
            src=raw.src_fqn,
            dst=dst,
            kind=raw.kind,
            src_path=raw.src_path,
            src_line=raw.src_line,
        )

    def _derive_def_edges(self, nodes: list[SymbolNode]) -> Iterable[SymbolEdge]:
        fqn_to_node = {n.fqn: n for n in nodes}
        for n in nodes:
            if "." not in n.fqn:
                continue
            parent_fqn = n.fqn.rsplit(".", 1)[0]
            parent = fqn_to_node.get(parent_fqn)
            if parent is None:
                continue
            yield SymbolEdge(
                src=parent_fqn,
                dst=n.fqn,
                kind="def",
                src_path=n.path,
                src_line=n.start_line,
            )
