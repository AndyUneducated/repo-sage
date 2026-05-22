"""Indexing pipeline: parse → chunk → extract → resolve → graph → community.

The pipeline is intentionally split into small composable stages so that
phase-by-phase work can replace one stage without touching the others.
"""

from reposage.indexer.chunker import Chunk, Chunker
from reposage.indexer.extractor import (
    FileExtraction,
    ImportBinding,
    PythonExtractor,
    RawDef,
    RawEdge,
)
from reposage.indexer.parser import ParseResult, TreeSitterParser
from reposage.indexer.python_resolver import PythonModuleResolver, ResolvedGraph
from reposage.indexer.symbol_graph import SymbolEdge, SymbolGraph, SymbolNode

__all__ = [
    "Chunk",
    "Chunker",
    "FileExtraction",
    "ImportBinding",
    "ParseResult",
    "PythonExtractor",
    "PythonModuleResolver",
    "RawDef",
    "RawEdge",
    "ResolvedGraph",
    "SymbolEdge",
    "SymbolGraph",
    "SymbolNode",
    "TreeSitterParser",
]
