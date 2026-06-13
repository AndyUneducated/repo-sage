package hnsw

import (
	"testing"

	"github.com/AndyUneducated/repo-sage/go-hnsw/internal/heap"
)

// candItems builds candidate Items with Dist measured from q under dist.
func candItems(g *graph, q []float32, dist DistanceFunc, ids ...uint32) []heap.Item {
	items := make([]heap.Item, len(ids))
	for i, id := range ids {
		items[i] = heap.Item{ID: id, Dist: dist(q, g.nodes[id].vector)}
	}
	return items
}

func contains(xs []uint32, x uint32) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

// The heuristic should reject a candidate that is closer to an already-selected
// neighbour than to the query, preferring a more distant but diverse point.
// Simple selection (closest-M) would instead pick the redundant near-duplicate.
func TestSelectNeighborsHeuristic_PrefersDiversity(t *testing.T) {
	cfg := DefaultConfig(2)
	cfg.Distance = L2
	g := newGraph(cfg)
	g.nodes = []node{
		{vector: []float32{1, 0}},       // 0: A
		{vector: []float32{1.05, 0.05}}, // 1: B, a near-duplicate of A
		{vector: []float32{0, 1.4}},     // 2: C, far from A
	}
	q := []float32{0, 0}
	cands := candItems(g, q, L2, 0, 1, 2)

	heuristic := g.selectNeighborsHeuristic(q, cands, 2, true)
	if !contains(heuristic, 0) || !contains(heuristic, 2) {
		t.Fatalf("heuristic = %v, want the diverse pair {0,2}", heuristic)
	}

	simple := selectNeighborsSimple(cands, 2)
	if !contains(simple, 0) || !contains(simple, 1) {
		t.Fatalf("simple = %v, want the closest pair {0,1}", simple)
	}
}

// keepPrunedConnections tops the result back up to M from the discarded set so
// the out-degree is preserved even when the heuristic prunes aggressively.
func TestSelectNeighborsHeuristic_KeepPruned(t *testing.T) {
	cfg := DefaultConfig(2)
	cfg.Distance = L2
	g := newGraph(cfg)
	// A tight collinear cluster: every later point is far closer to its
	// predecessor than to the query, so the heuristic only deems the first
	// "good".
	g.nodes = []node{
		{vector: []float32{1.00, 0}},
		{vector: []float32{1.01, 0}},
		{vector: []float32{1.02, 0}},
	}
	q := []float32{0, 0}
	cands := candItems(g, q, L2, 0, 1, 2)

	withKeep := g.selectNeighborsHeuristic(q, cands, 3, true)
	if len(withKeep) != 3 {
		t.Fatalf("keepPruned=true returned %d neighbours, want 3 (%v)", len(withKeep), withKeep)
	}
	noKeep := g.selectNeighborsHeuristic(q, cands, 3, false)
	if len(noKeep) != 1 || noKeep[0] != 0 {
		t.Fatalf("keepPruned=false = %v, want just {0}", noKeep)
	}
}

// The simple (Algorithm 3) path must still build a usable graph when the
// heuristic is disabled.
func TestInsert_SimpleSelectionStillWorks(t *testing.T) {
	cfg := DefaultConfig(4)
	cfg.Heuristic = false
	ix, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	vecs := map[string][]float32{
		"a": {1, 0, 0, 0},
		"b": {0.9, 0.1, 0, 0},
		"c": {0, 1, 0, 0},
		"d": {0, 0, 1, 0},
	}
	for id, v := range vecs {
		if err := ix.Add(id, v); err != nil {
			t.Fatalf("Add %s: %v", id, err)
		}
	}
	got, err := ix.Search([]float32{1, 0, 0, 0}, 1, 0)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(got) != 1 || got[0].ID != "a" {
		t.Fatalf("nearest = %v, want a", got)
	}
}

// randomLevel must follow the geometric distribution with mL = 1/ln(M): the
// fraction of nodes assigned to layer 0 only should be about 1 - 1/M.
func TestRandomLevel_Distribution(t *testing.T) {
	const m = 16
	cfg := DefaultConfig(8)
	cfg.M = m
	g := newGraph(cfg)
	const n = 200000
	layer0 := 0
	maxLvl := 0
	for i := 0; i < n; i++ {
		lv := g.randomLevel()
		if lv == 0 {
			layer0++
		}
		if lv > maxLvl {
			maxLvl = lv
		}
	}
	frac := float64(layer0) / float64(n)
	want := 1.0 - 1.0/float64(m) // ~0.9375
	if frac < want-0.02 || frac > want+0.02 {
		t.Fatalf("layer-0 fraction = %.4f, want ~%.4f", frac, want)
	}
	if maxLvl < 2 {
		t.Fatalf("max level = %d, expected several layers over %d samples", maxLvl, n)
	}
}
