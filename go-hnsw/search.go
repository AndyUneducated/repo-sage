package hnsw

// search implements Algorithm 5 of Malkov & Yashunin (2018):
//
//  1. greedy walk from entry point down to layer 1
//  2. ef-bounded beam search at layer 0 with `ef = max(efSearch, topK)`
//  3. return the topK closest from the resulting candidate set
//
// Phase 2 lands the implementation.
func (g *graph) search(query []float32, topK, efSearch int) []Result {
	_ = query
	_ = topK
	_ = efSearch
	return nil
}
