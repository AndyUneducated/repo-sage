package hnsw

import "sort"

// search implements Algorithm 5 of Malkov & Yashunin (2018):
//
//  1. greedy walk from entry point down to layer 1
//  2. ef-bounded beam search at layer 0 with `ef = max(efSearch, topK)`
//  3. return the topK closest from the resulting candidate set
func (g *graph) search(query []float32, topK, efSearch int) []Result {
	if !g.hasAny || len(g.nodes) == 0 {
		return nil
	}
	if efSearch < topK {
		efSearch = topK
	}

	curr := g.entry
	currDist := g.distance(query, curr)

	// Phase A: greedy descent from maxLvl down to layer 1.
	for lc := g.maxLvl; lc > 0; lc-- {
		changed := true
		for changed {
			changed = false
			for _, e := range g.neighborsAt(curr, lc) {
				d := g.distance(query, e)
				if d < currDist {
					currDist = d
					curr = e
					changed = true
				}
			}
		}
	}

	// Phase B: ef-bounded search at layer 0.
	const invalidID = ^uint32(0)
	items := g.searchLayer(query, []uint32{curr}, efSearch, 0, invalidID)

	// Items come out of a max-heap; sort by distance ascending and trim to topK.
	sort.Slice(items, func(i, j int) bool {
		if items[i].Dist != items[j].Dist {
			return items[i].Dist < items[j].Dist
		}
		return items[i].ID < items[j].ID
	})
	if len(items) > topK {
		items = items[:topK]
	}
	out := make([]Result, len(items))
	for i, it := range items {
		out[i] = Result{ID: g.nodes[it.ID].id, Distance: it.Dist}
	}
	return out
}
