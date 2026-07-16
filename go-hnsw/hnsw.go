// Package hnsw is a from-scratch implementation of Hierarchical Navigable
// Small World graphs for approximate nearest-neighbour search, following
// Malkov & Yashunin (2018).
//
// Design notes:
//   - The on-disk format (see persist.go) is mmap-friendly: vectors live in a
//     contiguous arena so the kernel can page them in lazily.
//   - Distance computations are the dominant cost, so DistanceFunc is exposed
//     in Config rather than hardcoded.
//   - Search is single-threaded per query. Concurrency lives at the Index
//     level via a single sync.RWMutex: many Search calls proceed concurrently
//     (RLock), while Add / AddBatch take the write lock (single-writer). The
//     gRPC server (Phase 5) mirrors this with its own RWMutex so reads never
//     serialise behind each other. A finer per-layer sharded lock is a
//     possible future optimisation but is not needed for the single-writer
//     indexing model.
package hnsw

import (
	"errors"
	"fmt"
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
	Distance       DistanceFunc // resolved from Metric when nil
	Metric         Metric       // serialised into snapshots; 0 = cosine
	Heuristic      bool         // Algorithm 4 neighbour selection (vs. Algorithm 3 simple)
	Seed           int64
}

// DefaultConfig returns reasonable defaults for a given dimensionality.
// Phase 4 turns on the Algorithm 4 heuristic by default (DD-027).
func DefaultConfig(dim int) Config {
	return Config{
		Dim:            dim,
		M:              16,
		MaxM:           32,
		EfConstruction: 200,
		EfSearch:       64,
		LevelMult:      0, // 0 → derived from M at construction
		Distance:       Cosine,
		Metric:         MetricCosine,
		Heuristic:      true,
		Seed:           1337,
	}
}

// Index is the public handle.
type Index struct {
	cfg    Config
	mu     sync.RWMutex
	graph  *graph
	mmap   []byte // non-nil while a recovered snapshot is mapped; freed by Close
	closed bool
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
	// Distance takes precedence when set explicitly (legacy callers); otherwise
	// resolve it from Metric so snapshots reload with the right function.
	if cfg.Distance == nil {
		cfg.Distance = cfg.Metric.Func()
	}
	return &Index{cfg: cfg, graph: newGraph(cfg)}, nil
}

// Add inserts (or replaces) a vector with a stable string id. If this index
// was produced by Recover (frozen, vectors aliasing a read-only mmap), the
// first Add thaws it into owned memory first so the mutation is safe.
func (ix *Index) Add(id string, vec []float32) error {
	if len(vec) != ix.cfg.Dim {
		return errors.New("hnsw: vector dimension mismatch")
	}
	ix.mu.Lock()
	defer ix.mu.Unlock()
	if ix.closed {
		return errClosed
	}
	if ix.graph.frozen {
		ix.thawLocked()
	}
	return ix.graph.insert(id, vec)
}

// AddBatch inserts (or replaces) many vectors under a single write lock. This
// is the batch-upsert fast path used by BulkLoad / cold-load: acquiring
// ix.mu once for N vectors instead of once per vector removes the lock
// hand-off between every insert, which dominates when a single indexer
// goroutine streams a whole repo's embeddings in. Order is preserved so a
// caller can correlate the returned error with its input slice.
//
// It validates every dimension up front so a single bad row fails the batch
// before any mutation, keeping the "all or nothing per RPC" contract the
// gRPC BulkLoad handler relies on. Returns the number successfully inserted.
func (ix *Index) AddBatch(ids []string, vecs [][]float32) (int, error) {
	if len(ids) != len(vecs) {
		return 0, errors.New("hnsw: ids/vecs length mismatch")
	}
	for i, vec := range vecs {
		if len(vec) != ix.cfg.Dim {
			return 0, fmt.Errorf("hnsw: vector %d dim %d != index dim %d", i, len(vec), ix.cfg.Dim)
		}
	}
	ix.mu.Lock()
	defer ix.mu.Unlock()
	if ix.closed {
		return 0, errClosed
	}
	if ix.graph.frozen {
		ix.thawLocked()
	}
	for i, id := range ids {
		if err := ix.graph.insert(id, vecs[i]); err != nil {
			return i, err
		}
	}
	return len(ids), nil
}

// thawLocked copies the mmap-aliased vector arena and id bytes into owned
// memory, unmaps the snapshot, and flips the graph back to mutable. Callers
// must hold ix.mu. This is the rare "recover then write" path.
func (ix *Index) thawLocked() {
	ix.graph.thaw()
	if ix.mmap != nil {
		_ = munmap(ix.mmap)
		ix.mmap = nil
	}
}

// Close releases the memory-mapped snapshot, if any. It is safe to call on an
// in-memory index (no-op) and idempotent. After Close the index must not be
// used.
func (ix *Index) Close() error {
	ix.mu.Lock()
	defer ix.mu.Unlock()
	if ix.closed {
		return nil
	}
	ix.closed = true
	if ix.mmap != nil {
		err := munmap(ix.mmap)
		ix.mmap = nil
		ix.graph = nil
		return err
	}
	ix.graph = nil
	return nil
}

var errClosed = errors.New("hnsw: index is closed")

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
	if ix.closed {
		return nil, errClosed
	}
	return ix.graph.search(query, topK, efSearch), nil
}

// Len reports the number of indexed vectors.
func (ix *Index) Len() int {
	ix.mu.RLock()
	defer ix.mu.RUnlock()
	if ix.closed {
		return 0
	}
	return ix.graph.size()
}

// Metric reports the distance metric the index was built with.
func (ix *Index) Metric() Metric { return ix.cfg.Metric }

// Dim reports the vector dimensionality.
func (ix *Index) Dim() int { return ix.cfg.Dim }
