package hnsw

import "errors"

// insert implements Algorithm 1 of Malkov & Yashunin (2018):
//
//  1. sample a level L
//  2. greedy descent from entry point until layer L+1
//  3. ef-bounded beam search at each layer L..0 to find M neighbours
//  4. apply the heuristic neighbour-selection (Algorithm 4) to keep edges
//     diverse and bounded by Mmax
//  5. update entry point if L > current max level
//
// Phase 2 lands the implementation; this stub keeps the API stable so the
// rest of the package compiles.
func (g *graph) insert(id string, vec []float32) error {
	_ = id
	_ = vec
	return errors.New("hnsw: insert not implemented yet (Phase 2)")
}
