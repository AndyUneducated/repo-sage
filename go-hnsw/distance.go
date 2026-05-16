package hnsw

import "math"

// L2 squared Euclidean distance. Operating on squared L2 avoids a sqrt in the
// inner loop without changing the ranking.
func L2(a, b []float32) float32 {
	var sum float32
	for i := range a {
		d := a[i] - b[i]
		sum += d * d
	}
	return sum
}

// Cosine distance: 1 - cos(a, b). Vectors are NOT assumed pre-normalised; if
// they are, prefer InnerProductNormalised which skips the divisions.
func Cosine(a, b []float32) float32 {
	var dot, na, nb float64
	for i := range a {
		ai, bi := float64(a[i]), float64(b[i])
		dot += ai * bi
		na += ai * ai
		nb += bi * bi
	}
	if na == 0 || nb == 0 {
		return 1
	}
	return float32(1 - dot/(math.Sqrt(na)*math.Sqrt(nb)))
}

// InnerProductNormalised assumes both inputs are L2-normalised; returns
// 1 - dot, which orders identically to cosine distance but is ~2x faster.
func InnerProductNormalised(a, b []float32) float32 {
	var dot float32
	for i := range a {
		dot += a[i] * b[i]
	}
	return 1 - dot
}
