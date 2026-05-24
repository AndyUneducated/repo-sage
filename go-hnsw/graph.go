package hnsw

import (
	"math"
	"math/rand"
)

// graph holds the multi-layer adjacency. `nodes[id]` owns its vector and a
// per-layer slice of neighbours (uint32 internal ids). `idIndex` maps the
// caller's stable string id to the internal id so callers can re-Add the
// same id and have it replace.
//
// The implementation is single-writer: every public Index method wraps the
// graph mutation in an `Index.mu` lock, so no per-graph synchronisation is
// needed here. Phase 6 will swap this for sharded locks.

type node struct {
	id        string
	vector    []float32
	neighbors [][]uint32 // neighbors[layer] -> internal node ids
}

type graph struct {
	cfg       Config
	nodes     []node
	idIndex   map[string]uint32
	entry     uint32
	maxLvl    int // top layer currently in the graph
	hasAny    bool
	rng       *rand.Rand
	levelMult float64
}

func newGraph(cfg Config) *graph {
	if cfg.LevelMult == 0 {
		cfg.LevelMult = 1.0 / math.Log(float64(cfg.M))
	}
	if cfg.MaxM == 0 {
		cfg.MaxM = 2 * cfg.M
	}
	seed := cfg.Seed
	if seed == 0 {
		seed = 1337
	}
	return &graph{
		cfg:       cfg,
		idIndex:   make(map[string]uint32),
		rng:       rand.New(rand.NewSource(seed)),
		levelMult: cfg.LevelMult,
	}
}

func (g *graph) size() int { return len(g.nodes) }

// randomLevel samples a level for a freshly inserted node following the
// geometric distribution from the paper: floor(-ln(uniform) * levelMult).
func (g *graph) randomLevel() int {
	r := g.rng.Float64()
	if r <= 0 {
		r = 1e-12
	}
	return int(-math.Log(r) * g.levelMult)
}

// distance computes dissimilarity from `vec` to node `id`.
func (g *graph) distance(vec []float32, id uint32) float32 {
	return g.cfg.Distance(vec, g.nodes[id].vector)
}

// neighborsAt returns a copy-safe slice header into the neighbour list at
// layer `lc` for node `id`. Mutations go through setNeighborsAt.
func (g *graph) neighborsAt(id uint32, lc int) []uint32 {
	if lc >= len(g.nodes[id].neighbors) {
		return nil
	}
	return g.nodes[id].neighbors[lc]
}

func (g *graph) setNeighborsAt(id uint32, lc int, ns []uint32) {
	g.nodes[id].neighbors[lc] = ns
}
