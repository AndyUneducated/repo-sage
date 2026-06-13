package hnsw

import (
	"math"
	"math/rand"
)

// graph holds the multi-layer adjacency. `nodes[i]` owns its vector and a
// per-layer slice of neighbours (uint32 internal ids). Stable string ids are
// stored columnar in `idData`/`idOff` (node i's id is the byte range
// idData[idOff[i]:idOff[i+1]]) rather than per-node, so Recover can alias the
// id bytes straight out of the mmap and materialise strings lazily — only for
// the handful of search hits, never all n up front (DD-026).
//
// `idIndex` maps the caller's stable string id to the internal id so callers
// can re-Add the same id and have it replace. After Recover it is nil and gets
// built lazily on the first Add (serving-only deploys never pay for it).
//
// The implementation is single-writer: every public Index method wraps the
// graph mutation in an `Index.mu` lock, so no per-graph synchronisation is
// needed here. Phase 5 will swap this for sharded locks.

type node struct {
	vector    []float32
	neighbors [][]uint32 // neighbors[layer] -> internal node ids
}

type graph struct {
	cfg       Config
	nodes     []node
	idData    []byte   // packed id bytes; recover aliases the mmap region
	idOff     []uint64 // len == len(nodes)+1; idOff[0] == 0
	idIndex   map[string]uint32
	entry     uint32
	maxLvl    int // top layer currently in the graph
	hasAny    bool
	frozen    bool // true after Recover until the first mutation thaws it
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
		idOff:     []uint64{0},
		rng:       rand.New(rand.NewSource(seed)),
		levelMult: cfg.LevelMult,
	}
}

func (g *graph) size() int { return len(g.nodes) }

// nodeID materialises node i's stable string id from the columnar store. On a
// recovered (frozen) graph idData aliases the mmap, so this is a copy of just
// the few bytes for that id — cheap, and only ever called for search hits.
func (g *graph) nodeID(i uint32) string {
	return string(g.idData[g.idOff[i]:g.idOff[i+1]])
}

// appendID records a fresh id in the columnar store and returns nothing; the
// caller already knows the internal id (== len(nodes) before appending).
func (g *graph) appendID(id string) {
	g.idData = append(g.idData, id...)
	g.idOff = append(g.idOff, uint64(len(g.idData)))
}

// ensureIDIndex lazily rebuilds the id→internal-id map after a Recover, where
// it is left nil to keep recover O(parse small arrays).
func (g *graph) ensureIDIndex() {
	if g.idIndex != nil {
		return
	}
	g.idIndex = make(map[string]uint32, len(g.nodes))
	for i := range g.nodes {
		g.idIndex[g.nodeID(uint32(i))] = uint32(i)
	}
}

// thaw copies everything that aliased the read-only mmap (vectors, neighbour
// lists, id bytes) into owned memory and flips the graph mutable. After thaw
// the index can be safely mutated; the caller (Index.thawLocked) unmaps the
// snapshot afterwards. This is the rare "recover then write" path.
func (g *graph) thaw() {
	if !g.frozen {
		return
	}
	for i := range g.nodes {
		v := g.nodes[i].vector
		ov := make([]float32, len(v))
		copy(ov, v)
		g.nodes[i].vector = ov

		nb := g.nodes[i].neighbors
		onb := make([][]uint32, len(nb))
		for lc := range nb {
			s := make([]uint32, len(nb[lc]))
			copy(s, nb[lc])
			onb[lc] = s
		}
		g.nodes[i].neighbors = onb
	}
	id := make([]byte, len(g.idData))
	copy(id, g.idData)
	g.idData = id
	off := make([]uint64, len(g.idOff))
	copy(off, g.idOff)
	g.idOff = off
	g.ensureIDIndex()
	g.frozen = false
}

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
