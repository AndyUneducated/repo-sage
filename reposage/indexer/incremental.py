"""Change detection for incremental reindex (Phase 7).

The building blocks are deliberately small and side-effect-free so they can
be unit-tested without a repo on disk:

* :func:`compute_changeset` is a **pure** set-diff between "what's on disk"
  (``{path: file_sha}``) and "what the index already has"
  (``SQLiteSymbolGraphStore.all_files``). It classifies every path into
  added / modified / deleted / unchanged.
* :func:`affected_files` expands the changed set by **one import hop**
  (DD-038): files that ``import`` a changed/deleted module must be
  re-resolved so their cross-file edges don't go stale. Deeper transitive
  ripples are left to ``--force`` / periodic full rebuilds, and the
  incremental result is kept honest by the equivalence tests.

See `docs/plans/phase-7-incremental.md`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True, frozen=True)
class ChangeSet:
    """Classification of a working tree against the persisted index."""

    added: tuple[str, ...]
    modified: tuple[str, ...]
    deleted: tuple[str, ...]
    unchanged: tuple[str, ...]

    @property
    def changed(self) -> tuple[str, ...]:
        """Files needing a full re-parse (new content): added plus modified."""
        return self.added + self.modified

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    @property
    def n_touched(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)


def compute_changeset(
    disk_shas: Mapping[str, str],
    indexed_shas: Mapping[str, str],
) -> ChangeSet:
    """Diff working-tree file shas against the index's file shas.

    ``disk_shas`` / ``indexed_shas`` are ``{repo_relative_path: file_sha}``.
    Pure function — the caller supplies both maps (walk the tree for the
    first, :meth:`SQLiteSymbolGraphStore.all_files` for the second).
    """
    disk = set(disk_shas)
    indexed = set(indexed_shas)
    both = disk & indexed
    added = tuple(sorted(disk - indexed))
    deleted = tuple(sorted(indexed - disk))
    modified = tuple(sorted(p for p in both if disk_shas[p] != indexed_shas[p]))
    unchanged = tuple(sorted(p for p in both if disk_shas[p] == indexed_shas[p]))
    return ChangeSet(added=added, modified=modified, deleted=deleted, unchanged=unchanged)


class _ImportRippleStore(Protocol):
    """The subset of ``SQLiteSymbolGraphStore`` :func:`affected_files` needs."""

    def module_fqns_for_paths(self, repo: str, paths: Iterable[str]) -> set[str]: ...

    def paths_importing(self, modules: Iterable[str]) -> set[str]: ...


def affected_files(
    changeset: ChangeSet,
    store: _ImportRippleStore,
    *,
    repo: str,
) -> tuple[str, ...]:
    """One-hop import ripple (DD-038): files importing a changed/deleted module.

    Returns paths that are *unchanged themselves* but must be re-resolved
    because a module they import changed. Excludes files already in the
    changed/deleted set (they're re-parsed regardless).
    """
    touched = set(changeset.changed) | set(changeset.deleted)
    if not touched:
        return ()
    modules = store.module_fqns_for_paths(repo, touched)
    if not modules:
        return ()
    importers = store.paths_importing(modules)
    return tuple(sorted(importers - touched))
