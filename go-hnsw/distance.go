package hnsw

import "math"

// Metric identifies a distance function by a stable on-disk id so a snapshot
// can be reloaded with the same metric it was built with (DistanceFunc itself
// is a closure and cannot be serialised). The zero value is cosine, matching
// DefaultConfig, so older callers that never set Metric keep working.
type Metric uint8

const (
	MetricCosine       Metric = 0 // 1 - cos(a, b); inputs not assumed normalised
	MetricL2           Metric = 1 // squared Euclidean (SIFT-1M uses L2)
	MetricInnerProduct Metric = 2 // 1 - dot; assumes L2-normalised inputs
)

// Func resolves the DistanceFunc for a metric. Unknown metrics fall back to
// cosine so a corrupt/forward-version snapshot degrades loudly at search time
// rather than panicking in the hot loop.
func (m Metric) Func() DistanceFunc {
	switch m {
	case MetricL2:
		return L2
	case MetricInnerProduct:
		return InnerProductNormalised
	default:
		return Cosine
	}
}

// String renders the metric for CLI flags and bench CSV rows.
func (m Metric) String() string {
	switch m {
	case MetricL2:
		return "l2"
	case MetricInnerProduct:
		return "ip"
	default:
		return "cosine"
	}
}

// ParseMetric maps a CLI string to a Metric. Unknown strings return cosine and
// ok=false so callers can choose to reject.
func ParseMetric(s string) (Metric, bool) {
	switch s {
	case "l2", "euclidean":
		return MetricL2, true
	case "ip", "inner", "dot":
		return MetricInnerProduct, true
	case "cosine", "cos", "":
		return MetricCosine, true
	default:
		return MetricCosine, false
	}
}

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
