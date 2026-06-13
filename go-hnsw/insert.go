package hnsw

import (
	"github.com/AndyUneducated/repo-sage/go-hnsw/internal/heap"
)

// insert implements Algorithm 1 of Malkov & Yashunin (2018):
//
//  1. sample a level L (geometric, floor(-ln(uniform)/ln(M)))
//  2. greedy descent from entry point until layer L+1
//  3. ef-bounded beam search at each layer L..0 to find M neighbours
//  4. apply neighbour selection (Algorithm 4 heuristic by default, DD-027, or
//     the simple Algorithm 3 when Config.Heuristic is false) to keep edges
//     bounded by Mmax.
//  5. update entry point if L > current max level
func (g *graph) insert(id string, vec []float32) error {
	g.ensureIDIndex()
	// Replace semantics: a re-Add with the same id keeps the slot but
	// re-walks the layers. This is what users expect when re-indexing the
	// same chunk_id with a new model checkpoint.
	if existing, ok := g.idIndex[id]; ok {
		g.nodes[existing].vector = append(g.nodes[existing].vector[:0], vec...)
		// Reuse the existing layer assignment to preserve neighbour slots;
		// neighbour lists get re-selected below.
		return g.connectExisting(existing)
	}

	internalID := uint32(len(g.nodes))
	level := g.randomLevel()
	n := node{
		vector:    append(make([]float32, 0, len(vec)), vec...),
		neighbors: make([][]uint32, level+1),
	}
	for lc := 0; lc <= level; lc++ {
		mmax := g.cfg.MaxM
		if lc > 0 {
			mmax = g.cfg.M
		}
		n.neighbors[lc] = make([]uint32, 0, mmax)
	}
	g.nodes = append(g.nodes, n)
	g.appendID(id)
	g.idIndex[id] = internalID

	if !g.hasAny {
		g.hasAny = true
		g.entry = internalID
		g.maxLvl = level
		return nil
	}

	if err := g.connect(internalID, level); err != nil {
		return err
	}
	if level > g.maxLvl {
		g.maxLvl = level
		g.entry = internalID
	}
	return nil
}

// connectExisting re-runs the connection step for a node whose vector has
// been mutated in place. It assumes the layer assignment doesn't change.
func (g *graph) connectExisting(id uint32) error {
	level := len(g.nodes[id].neighbors) - 1
	for lc := 0; lc <= level; lc++ {
		g.nodes[id].neighbors[lc] = g.nodes[id].neighbors[lc][:0]
	}
	if g.entry == id && len(g.nodes) == 1 {
		return nil
	}
	return g.connect(id, level)
}

// connect performs the Algorithm 1 lower-half: greedy descent from entry to
// layer L+1, then ef-bounded search at each layer to pick neighbours.
func (g *graph) connect(q uint32, level int) error {
	vec := g.nodes[q].vector

	// Phase A: greedy descent from `entry` through layers above `level`.
	currNearest := g.entry
	currDist := g.distance(vec, currNearest)
	for lc := g.maxLvl; lc > level; lc-- {
		changed := true
		for changed {
			changed = false
			for _, e := range g.neighborsAt(currNearest, lc) {
				if e == q {
					continue
				}
				d := g.distance(vec, e)
				if d < currDist {
					currDist = d
					currNearest = e
					changed = true
				}
			}
		}
	}

	// Phase B: at each layer L..0 run an ef-bounded beam search seeded at
	// currNearest, then apply the simple neighbour-selection.
	entry := []uint32{currNearest}
	for lc := minInt(level, g.maxLvl); lc >= 0; lc-- {
		candidates := g.searchLayer(vec, entry, g.cfg.EfConstruction, lc, q)
		// Select M neighbours from the candidate set (heuristic or simple).
		mmax := g.cfg.MaxM
		if lc > 0 {
			mmax = g.cfg.M
		}
		neighbours := g.selectNeighbors(vec, candidates, g.cfg.M)
		g.setNeighborsAt(q, lc, neighbours)
		// Add reverse edges, trimming the neighbour's list if it overflows.
		for _, e := range neighbours {
			eList := g.neighborsAt(e, lc)
			eList = appendUnique(eList, q)
			if len(eList) > mmax {
				eList = g.trimNeighbours(e, lc, eList, mmax)
			}
			g.setNeighborsAt(e, lc, eList)
		}
		// Seed the next layer's descent from the closest candidate so we
		// can reuse its proximity work (paper figure 1).
		if len(neighbours) > 0 {
			entry = neighbours
		}
	}
	return nil
}

// searchLayer is the inner loop shared by insert (with `excludeSelf`) and
// search (with `excludeSelf == invalidID`). Returns up to `ef` items closest
// to `vec`, popped from a max-heap so the worst-of-the-best is at index 0.
func (g *graph) searchLayer(
	vec []float32, entryPoints []uint32, ef int, lc int, excludeSelf uint32,
) []heap.Item {
	visited := make(map[uint32]struct{}, ef*2)
	candidates := heap.NewMinHeap(ef * 2)
	results := heap.NewMaxHeap(ef + 1)

	for _, ep := range entryPoints {
		if ep == excludeSelf {
			continue
		}
		if _, ok := visited[ep]; ok {
			continue
		}
		visited[ep] = struct{}{}
		d := g.distance(vec, ep)
		candidates.Push(heap.Item{Dist: d, ID: ep})
		results.Push(heap.Item{Dist: d, ID: ep})
		if results.Len() > ef {
			results.Pop()
		}
	}

	for candidates.Len() > 0 {
		c := candidates.Pop()
		if results.Len() >= ef && c.Dist > results.Peek().Dist {
			break
		}
		for _, e := range g.neighborsAt(c.ID, lc) {
			if e == excludeSelf {
				continue
			}
			if _, ok := visited[e]; ok {
				continue
			}
			visited[e] = struct{}{}
			d := g.distance(vec, e)
			if results.Len() < ef || d < results.Peek().Dist {
				candidates.Push(heap.Item{Dist: d, ID: e})
				results.Push(heap.Item{Dist: d, ID: e})
				if results.Len() > ef {
					results.Pop()
				}
			}
		}
	}
	return results.Items()
}

// selectNeighbors dispatches to the Algorithm 4 heuristic (DD-027) or the
// simple Algorithm 3 selection based on Config.Heuristic. `query` is the
// vector all candidate distances were measured against.
func (g *graph) selectNeighbors(query []float32, candidates []heap.Item, m int) []uint32 {
	if g.cfg.Heuristic {
		return g.selectNeighborsHeuristic(query, candidates, m, true)
	}
	return selectNeighborsSimple(candidates, m)
}

// selectNeighborsHeuristic implements Algorithm 4 (SELECT-NEIGHBORS-HEURISTIC)
// of Malkov & Yashunin (2018). A candidate e is kept only if it is closer to
// the query than to any already-selected neighbour — an RNG-style pruning that
// favours diverse long/short edges and improves recall on clustered data over
// the naive "keep the M closest". With keepPruned the result is topped up from
// the discarded set so the out-degree still reaches M (hnswlib default).
//
// `candidates[i].Dist` must already be distance(query, candidates[i].ID).
func (g *graph) selectNeighborsHeuristic(
	query []float32, candidates []heap.Item, m int, keepPruned bool,
) []uint32 {
	_ = query // distances are precomputed in candidates; kept for call-site clarity
	work := heap.NewMinHeap(len(candidates))
	for _, it := range candidates {
		work.Push(it)
	}
	result := make([]uint32, 0, m)
	var discarded []heap.Item
	for work.Len() > 0 && len(result) < m {
		e := work.Pop() // nearest remaining candidate to the query
		good := true
		for _, r := range result {
			// distance from candidate e to an already-selected neighbour r
			if g.cfg.Distance(g.nodes[e.ID].vector, g.nodes[r].vector) < e.Dist {
				good = false
				break
			}
		}
		if good {
			result = append(result, e.ID)
		} else if keepPruned {
			discarded = append(discarded, e)
		}
	}
	// discarded is in ascending-distance order (popped from a min-heap).
	for i := 0; keepPruned && len(result) < m && i < len(discarded); i++ {
		result = append(result, discarded[i].ID)
	}
	return result
}

// selectNeighborsSimple takes the M closest items from a max-heap-shaped
// candidate slice. We do a single O(K) extraction via a min-heap.
func selectNeighborsSimple(items []heap.Item, m int) []uint32 {
	if len(items) <= m {
		out := make([]uint32, len(items))
		// Sort by distance ascending so neighbour lists are deterministic.
		mh := heap.NewMinHeap(len(items))
		for _, it := range items {
			mh.Push(it)
		}
		for i := range out {
			out[i] = mh.Pop().ID
		}
		return out
	}
	mh := heap.NewMinHeap(len(items))
	for _, it := range items {
		mh.Push(it)
	}
	out := make([]uint32, m)
	for i := 0; i < m; i++ {
		out[i] = mh.Pop().ID
	}
	return out
}

// trimNeighbours re-selects `id`'s neighbour list down to `mmax` when a reverse
// edge pushes it over the cap. It reuses the same selection policy as insert so
// the graph stays consistent: with the heuristic on, an overflowing list is
// re-pruned for diversity rather than simply truncated to the closest mmax.
func (g *graph) trimNeighbours(
	id uint32, lc int, eList []uint32, mmax int,
) []uint32 {
	vec := g.nodes[id].vector
	items := make([]heap.Item, len(eList))
	for i, n := range eList {
		items[i] = heap.Item{Dist: g.distance(vec, n), ID: n}
	}
	return g.selectNeighbors(vec, items, mmax)
}

func appendUnique(xs []uint32, x uint32) []uint32 {
	for _, v := range xs {
		if v == x {
			return xs
		}
	}
	return append(xs, x)
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
