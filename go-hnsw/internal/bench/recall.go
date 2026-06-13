package bench

// RecallAtK returns the fraction of the true top-k neighbours that appear in
// the returned top-k: |got[:k] ∩ truth[:k]| / k. This is the standard
// ANN-Benchmarks recall@k.
func RecallAtK(got []int, truth []int32, k int) float64 {
	if k <= 0 {
		return 0
	}
	tset := make(map[int]struct{}, k)
	for i := 0; i < k && i < len(truth); i++ {
		tset[int(truth[i])] = struct{}{}
	}
	if len(tset) == 0 {
		return 0
	}
	hit := 0
	for i := 0; i < k && i < len(got); i++ {
		if _, ok := tset[got[i]]; ok {
			hit++
		}
	}
	return float64(hit) / float64(len(tset))
}
