// Package hnsw is a from-scratch implementation of Hierarchical Navigable
// Small World graphs for approximate nearest-neighbour search, following
// Malkov & Yashunin (2018).
//
// Design notes:
//   - The on-disk format (see persist.go) is mmap-friendly: vectors live in a
//     contiguous arena so the kernel can page them in lazily.
//   - Distance computations are the dominant cost, so DistanceFunc is exposed
//     in Config rather than hardcoded.
//   - Search is single-threaded per query; concurrency lives at the Index
//     level via per-layer RWMutex (added in Phase 6).
package hnsw

import (
	"errors"
	"sync"
)

// DistanceFunc returns a non-negative dissimilarity score; smaller is closer.
type DistanceFunc func(a, b []float32) float32

// Config governs index construction. Defaults follow paper recommendations.
type Config struct {
	Dim            int
	M              int          // out-degree per layer (typical: 8..48)
	MaxM           int          // cap for layer 0 (defaults to 2*M)
	EfConstruction int          // candidate list size during insert
	EfSearch       int          // candidate list size during search (override-able per query)
	LevelMult      float64      // 1 / ln(M); controls layer assignment
	Distance       DistanceFunc // defaults to cosine
	Seed           int64
}

// DefaultConfig returns reasonable defaults for a given dimensionality.
func DefaultConfig(dim int) Config {
	return Config{
		Dim:            dim,
		M:              16,
		MaxM:           32,
		EfConstruction: 200,
		EfSearch:       64,
		LevelMult:      0, // 0 → derived from M at construction
		Distance:       Cosine,
		Seed:           1337,
	}
}

// Index is the public handle.
type Index struct {
	cfg   Config
	mu    sync.RWMutex
	graph *graph
}

// New creates an empty index with the supplied config.
func New(cfg Config) (*Index, error) {
	if cfg.Dim <= 0 {
		return nil, errors.New("hnsw: Dim must be > 0")
	}
	if cfg.M <= 0 {
		return nil, errors.New("hnsw: M must be > 0")
	}
	if cfg.EfConstruction <= 0 {
		return nil, errors.New("hnsw: EfConstruction must be > 0")
	}
	if cfg.Distance == nil {
		cfg.Distance = Cosine
	}
	return &Index{cfg: cfg, graph: newGraph(cfg)}, nil
}

// Add inserts (or replaces) a vector with a stable string id.
func (ix *Index) Add(id string, vec []float32) error {
	if len(vec) != ix.cfg.Dim {
		return errors.New("hnsw: vector dimension mismatch")
	}
	ix.mu.Lock()
	defer ix.mu.Unlock()
	return ix.graph.insert(id, vec)
}

// Result is one search hit.
type Result struct {
	ID       string
	Distance float32
}

// Search returns the topK approximate nearest neighbours of `query`.
// `efSearch` overrides Config.EfSearch when > 0.
func (ix *Index) Search(query []float32, topK, efSearch int) ([]Result, error) {
	if len(query) != ix.cfg.Dim {
		return nil, errors.New("hnsw: query dimension mismatch")
	}
	if efSearch <= 0 {
		efSearch = ix.cfg.EfSearch
	}
	ix.mu.RLock()
	defer ix.mu.RUnlock()
	return ix.graph.search(query, topK, efSearch), nil
}

// Len reports the number of indexed vectors.
func (ix *Index) Len() int {
	ix.mu.RLock()
	defer ix.mu.RUnlock()
	return ix.graph.size()
}
