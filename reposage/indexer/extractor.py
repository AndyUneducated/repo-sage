"""Symbol extraction: turn a `ParseResult` into raw, *unresolved* graph rows.

Two outputs:

* `RawDef` — every definition (module / class / function / method) with its
  fully-qualified name (FQN) computed from the file's module path.
* `RawEdge` — every `call` / `inherit` / `import` edge whose destination
  is still a *local* name (e.g. ``foo``, ``utils.bar``, ``a.b.C``). The
  resolver runs as a second pass and turns these into FQNs.

The extractor is intentionally *language-aware but resolver-agnostic*: it
walks the tree and produces structural facts without any cross-file
knowledge. Phase 1 wires up Python only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from reposage.indexer.parser import ParseResult

if TYPE_CHECKING:
    import tree_sitter

NodeKind = Literal["module", "class", "function", "method"]
EdgeKind = Literal["call", "inherit", "import"]


@dataclass(slots=True, frozen=True)
class RawDef:
    fqn: str
    kind: NodeKind
    src_path: str
    start_line: int
    end_line: int


@dataclass(slots=True, frozen=True)
class RawEdge:
    kind: EdgeKind
    src_fqn: str
    dst_local: str  # unresolved name, e.g. "User.login" or "utils.bar"
    src_path: str
    src_line: int


@dataclass(slots=True, frozen=True)
class ImportBinding:
    """A single name binding produced by a Python `import` statement.

    ``local_name`` is what's visible in the file's namespace.
    ``target_fqn`` is the dotted path that name should resolve against.

    Examples
    --------
    ``import a.b``                        -> ``ImportBinding('a', 'a')``
    ``import a.b as c``                   -> ``ImportBinding('c', 'a.b')``
    ``from a.b import c``                 -> ``ImportBinding('c', 'a.b.c')``
    ``from a.b import c as d``            -> ``ImportBinding('d', 'a.b.c')``
    """

    local_name: str
    target_fqn: str


@dataclass(slots=True)
class FileExtraction:
    file_module: str
    src_path: str
    defs: list[RawDef] = field(default_factory=list)
    edges: list[RawEdge] = field(default_factory=list)
    imports: list[ImportBinding] = field(default_factory=list)


def module_fqn_for(repo_root: Path, file_path: Path) -> str:
    """Compute Python module FQN from a file path, relative to ``repo_root``.

    ``foo/bar.py``         -> ``foo.bar``
    ``foo/__init__.py``    -> ``foo``
    ``__init__.py``        -> ``""`` (top-level package; rare)
    Files outside ``repo_root`` fall back to the path stem.
    """
    try:
        rel = file_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        rel = Path(file_path.name)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _node_text(source: bytes, node: tree_sitter.Node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _name_of(node: tree_sitter.Node) -> str | None:
    field_node = node.child_by_field_name("name")
    if field_node is None:
        return None
    text = field_node.text
    if text is None:
        return None
    return str(text.decode("utf-8", errors="replace"))


def _unwrap_decorated(node: tree_sitter.Node) -> tree_sitter.Node:
    if node.type != "decorated_definition":
        return node
    for child in node.children:
        if child.type in {"function_definition", "class_definition"}:
            return child
    return node


def _dotted_text(node: tree_sitter.Node, source: bytes) -> str | None:
    """Render an ``identifier`` / ``attribute`` / ``dotted_name`` node as a string."""
    if node.type in {"identifier", "dotted_name"}:
        return _node_text(source, node).strip()
    if node.type == "attribute":
        return _node_text(source, node).strip()
    return None


class PythonExtractor:
    """Walk a Python tree-sitter tree, emitting `FileExtraction`."""

    def extract(self, parsed: ParseResult, file_module: str) -> FileExtraction:
        if parsed.language != "python":
            raise ValueError(f"PythonExtractor only handles python, got {parsed.language!r}")
        root = parsed.tree.root_node
        out = FileExtraction(file_module=file_module, src_path=str(parsed.path))
        out.defs.append(
            RawDef(
                fqn=file_module,
                kind="module",
                src_path=out.src_path,
                start_line=1,
                end_line=root.end_point[0] + 1,
            )
        )
        scope = [file_module] if file_module else []
        self._walk(
            root, scope=scope, src_fqn=file_module or "<anonymous>", state=out, source=parsed.source
        )
        return out

    def _walk(
        self,
        node: tree_sitter.Node,
        *,
        scope: list[str],
        src_fqn: str,
        state: FileExtraction,
        source: bytes,
    ) -> None:
        for child in node.named_children:
            inner = _unwrap_decorated(child)
            ctype = inner.type
            if ctype == "class_definition":
                self._handle_class(child, inner, scope, state, source)
            elif ctype == "function_definition":
                self._handle_function(child, inner, scope, state, source)
            elif ctype == "import_statement":
                self._handle_import(inner, state, source)
            elif ctype == "import_from_statement":
                self._handle_import_from(inner, state, source)
            elif ctype == "call":
                self._handle_call(inner, src_fqn, state, source)
                # Calls may have nested calls in their arguments — recurse.
                self._walk(inner, scope=scope, src_fqn=src_fqn, state=state, source=source)
            else:
                self._walk(inner, scope=scope, src_fqn=src_fqn, state=state, source=source)

    # --- definitions -------------------------------------------------------

    def _handle_class(
        self,
        outer: tree_sitter.Node,
        inner: tree_sitter.Node,
        scope: list[str],
        state: FileExtraction,
        source: bytes,
    ) -> None:
        name = _name_of(inner)
        if not name:
            return
        class_fqn = ".".join([*scope, name]) if scope else name
        state.defs.append(
            RawDef(
                fqn=class_fqn,
                kind="class",
                src_path=state.src_path,
                start_line=outer.start_point[0] + 1,
                end_line=outer.end_point[0] + 1,
            )
        )
        sc = inner.child_by_field_name("superclasses")
        if sc is not None:
            for arg in sc.named_children:
                parent_local = _dotted_text(arg, source)
                if parent_local:
                    state.edges.append(
                        RawEdge(
                            kind="inherit",
                            src_fqn=class_fqn,
                            dst_local=parent_local,
                            src_path=state.src_path,
                            src_line=outer.start_point[0] + 1,
                        )
                    )
        body = inner.child_by_field_name("body")
        if body is not None:
            self._walk(
                body,
                scope=[*scope, name],
                src_fqn=class_fqn,
                state=state,
                source=source,
            )

    def _handle_function(
        self,
        outer: tree_sitter.Node,
        inner: tree_sitter.Node,
        scope: list[str],
        state: FileExtraction,
        source: bytes,
    ) -> None:
        name = _name_of(inner)
        if not name:
            return
        func_fqn = ".".join([*scope, name]) if scope else name
        # `method` if the immediate parent scope is a class. We can't tell that
        # from `scope` alone, so the resolver re-classifies later if needed.
        # For Phase 1 we use a simple heuristic: depth >= 2 (module + class)
        # implies method.
        depth = len(scope)
        kind: NodeKind = "method" if depth >= 2 else "function"
        state.defs.append(
            RawDef(
                fqn=func_fqn,
                kind=kind,
                src_path=state.src_path,
                start_line=outer.start_point[0] + 1,
                end_line=outer.end_point[0] + 1,
            )
        )
        body = inner.child_by_field_name("body")
        if body is not None:
            self._walk(
                body,
                scope=[*scope, name],
                src_fqn=func_fqn,
                state=state,
                source=source,
            )

    # --- calls -------------------------------------------------------------

    def _handle_call(
        self,
        node: tree_sitter.Node,
        src_fqn: str,
        state: FileExtraction,
        source: bytes,
    ) -> None:
        func_node = node.child_by_field_name("function")
        if func_node is None:
            return
        callee = _dotted_text(func_node, source)
        if callee is None:
            return
        state.edges.append(
            RawEdge(
                kind="call",
                src_fqn=src_fqn,
                dst_local=callee,
                src_path=state.src_path,
                src_line=node.start_point[0] + 1,
            )
        )

    # --- imports -----------------------------------------------------------

    def _handle_import(
        self,
        node: tree_sitter.Node,
        state: FileExtraction,
        source: bytes,
    ) -> None:
        # `import a.b` / `import a.b as c` / `import a, b`
        for child in node.named_children:
            if child.type == "dotted_name":
                module = _node_text(source, child).strip()
                if not module:
                    continue
                local = module.split(".")[0]
                state.imports.append(ImportBinding(local_name=local, target_fqn=module))
                state.edges.append(
                    RawEdge(
                        kind="import",
                        src_fqn=state.file_module or "<anonymous>",
                        dst_local=module,
                        src_path=state.src_path,
                        src_line=node.start_point[0] + 1,
                    )
                )
            elif child.type == "aliased_import":
                target_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                if target_node is None or alias_node is None:
                    continue
                module = _node_text(source, target_node).strip()
                alias = _node_text(source, alias_node).strip()
                if not module or not alias:
                    continue
                state.imports.append(ImportBinding(local_name=alias, target_fqn=module))
                state.edges.append(
                    RawEdge(
                        kind="import",
                        src_fqn=state.file_module or "<anonymous>",
                        dst_local=module,
                        src_path=state.src_path,
                        src_line=node.start_point[0] + 1,
                    )
                )

    def _handle_import_from(
        self,
        node: tree_sitter.Node,
        state: FileExtraction,
        source: bytes,
    ) -> None:
        # `from a.b import c [, d as e]` / `from . import c`
        module_node = node.child_by_field_name("module_name")
        if module_node is None:
            return
        module = _node_text(source, module_node).strip()
        if not module:
            return
        line = node.start_point[0] + 1
        state.edges.append(
            RawEdge(
                kind="import",
                src_fqn=state.file_module or "<anonymous>",
                dst_local=module,
                src_path=state.src_path,
                src_line=line,
            )
        )
        # Imported names follow the module_name field. tree-sitter exposes
        # them as additional named children of `import_from_statement`.
        for name_node in node.children_by_field_name("name"):
            if name_node.type == "aliased_import":
                target_node = name_node.child_by_field_name("name")
                alias_node = name_node.child_by_field_name("alias")
                if target_node is None or alias_node is None:
                    continue
                imported = _node_text(source, target_node).strip()
                alias = _node_text(source, alias_node).strip()
                if not imported or not alias:
                    continue
                state.imports.append(
                    ImportBinding(local_name=alias, target_fqn=f"{module}.{imported}")
                )
            elif name_node.type in {"dotted_name", "identifier"}:
                imported = _node_text(source, name_node).strip()
                if not imported:
                    continue
                local = imported.split(".")[0]
                state.imports.append(
                    ImportBinding(local_name=local, target_fqn=f"{module}.{imported}")
                )
