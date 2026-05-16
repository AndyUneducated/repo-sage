package hnsw

// graph holds the multi-layer adjacency. Implementation lands in Phase 2;
// for now this file fixes the internal API so insert.go / search.go /
// persist.go can compile in parallel.

type node struct {
	id        string
	vector    []float32
	neighbors [][]uint32 // neighbors[layer] -> internal node ids
}

type graph struct {
	cfg     Config
	nodes   []node
	idIndex map[string]uint32
	entry   uint32
	maxLvl  int
	hasAny  bool
}

func newGraph(cfg Config) *graph {
	return &graph{
		cfg:     cfg,
		idIndex: make(map[string]uint32),
	}
}

func (g *graph) size() int { return len(g.nodes) }
