package bench

import (
	"fmt"
	"math/rand"
	"os"
	"path/filepath"
	"sort"

	hnsw "github.com/AndyUneducated/repo-sage/go-hnsw"
)

// Dataset is an ANN benchmark fixture: base vectors to index, query vectors to
// search with, and the per-query ground-truth neighbour indices into Base.
type Dataset struct {
	Name        string
	Dim         int
	Base        [][]float32
	Queries     [][]float32
	GroundTruth [][]int32 // q × k, indices into Base, nearest-first
	Metric      hnsw.Metric
}

// siftFile finds one of the conventional SIFT filenames in dir.
func siftFile(dir string, candidates ...string) (string, error) {
	for _, c := range candidates {
		p := filepath.Join(dir, c)
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	return "", fmt.Errorf("none of %v found in %s", candidates, dir)
}

// LoadSIFT loads SIFT-1M (L2 metric) from dir. When maxBase > 0 the base set is
// truncated and the ground truth is recomputed by brute force over the loaded
// subset (the file ground truth is only valid for the full 1M base). maxQueries
// truncates the query set likewise.
func LoadSIFT(dir string, maxBase, maxQueries, gtK int) (*Dataset, error) {
	basePath, err := siftFile(dir, "sift_base.fvecs", "base.fvecs", "sift/sift_base.fvecs")
	if err != nil {
		return nil, err
	}
	queryPath, err := siftFile(dir, "sift_query.fvecs", "query.fvecs", "sift/sift_query.fvecs")
	if err != nil {
		return nil, err
	}

	base, err := ReadFvecs(basePath, maxBase)
	if err != nil {
		return nil, err
	}
	queries, err := ReadFvecs(queryPath, maxQueries)
	if err != nil {
		return nil, err
	}
	if len(base) == 0 || len(queries) == 0 {
		return nil, fmt.Errorf("sift: empty base (%d) or queries (%d)", len(base), len(queries))
	}

	var gt [][]int32
	if maxBase > 0 {
		// Subset: file ground truth no longer applies; recompute it.
		gt = BruteForceGT(base, queries, gtK)
	} else {
		gtPath, gerr := siftFile(dir, "sift_groundtruth.ivecs", "groundtruth.ivecs", "sift/sift_groundtruth.ivecs")
		if gerr != nil {
			return nil, gerr
		}
		gt, err = ReadIvecs(gtPath, maxQueries)
		if err != nil {
			return nil, err
		}
	}

	return &Dataset{
		Name:        "sift1m",
		Dim:         len(base[0]),
		Base:        base,
		Queries:     queries,
		GroundTruth: gt,
		Metric:      hnsw.MetricL2,
	}, nil
}

// Synthetic builds a reproducible Gaussian dataset with brute-force ground
// truth (L2). It exists so CI and `go test` can exercise the full harness
// without the 1 GB SIFT download.
func Synthetic(n, q, dim, gtK int, seed int64) *Dataset {
	rng := rand.New(rand.NewSource(seed))
	gauss := func(m int) [][]float32 {
		out := make([][]float32, m)
		for i := range out {
			v := make([]float32, dim)
			for j := range v {
				v[j] = float32(rng.NormFloat64())
			}
			out[i] = v
		}
		return out
	}
	base := gauss(n)
	queries := gauss(q)
	return &Dataset{
		Name:        "synthetic",
		Dim:         dim,
		Base:        base,
		Queries:     queries,
		GroundTruth: BruteForceGT(base, queries, gtK),
		Metric:      hnsw.MetricL2,
	}
}

// BruteForceGT computes exact top-k (by squared L2) neighbour indices for each
// query. O(q·n·dim) — only used for synthetic data and SIFT subsets.
func BruteForceGT(base, queries [][]float32, k int) [][]int32 {
	out := make([][]int32, len(queries))
	for qi, q := range queries {
		type cand struct {
			idx int32
			d   float32
		}
		cands := make([]cand, len(base))
		for i, b := range base {
			cands[i] = cand{idx: int32(i), d: hnsw.L2(q, b)}
		}
		sort.Slice(cands, func(a, b int) bool {
			if cands[a].d != cands[b].d {
				return cands[a].d < cands[b].d
			}
			return cands[a].idx < cands[b].idx
		})
		kk := k
		if kk > len(cands) {
			kk = len(cands)
		}
		row := make([]int32, kk)
		for i := 0; i < kk; i++ {
			row[i] = cands[i].idx
		}
		out[qi] = row
	}
	return out
}
