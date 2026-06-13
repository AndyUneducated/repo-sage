package bench

import (
	"fmt"
	"math"
	"runtime"
	"sort"
	"strconv"
	"time"

	hnsw "github.com/AndyUneducated/repo-sage/go-hnsw"
)

// Result is one benchmark row: a single (M, efC, efSearch) configuration.
type Result struct {
	Index        string
	M            int
	EfC          int
	EfSearch     int
	Recall       float64
	QPS          float64
	P50ms        float64
	P99ms        float64
	BuildS       float64
	RSSmb        float64
	RecoverP50ms float64
	N            int
	Dim          int
}

// CSVHeader is the column order shared by go-hnsw and the Faiss baseline.
func CSVHeader() string {
	return "index,M,efC,efSearch,recall,qps,p50_ms,p99_ms,build_s,rss_mb,recover_p50_ms,n,dim"
}

// CSV renders the row in the CSVHeader column order.
func (r Result) CSV() string {
	return fmt.Sprintf("%s,%d,%d,%d,%.4f,%.1f,%.4f,%.4f,%.2f,%.1f,%.3f,%d,%d",
		r.Index, r.M, r.EfC, r.EfSearch, r.Recall, r.QPS, r.P50ms, r.P99ms,
		r.BuildS, r.RSSmb, r.RecoverP50ms, r.N, r.Dim)
}

// Built is an index built once for a (M, efC) pair, queryable at many efSearch
// values so a sweep does not pay the build cost per efSearch row.
type Built struct {
	index     *hnsw.Index
	M         int
	EfC       int
	BuildS    float64
	RSSmb     float64
	RecoverMs float64
	N         int
	Dim       int
	recovered bool
}

// Build constructs and populates an index from ds.Base. When snapshotPath is
// non-empty it snapshots, reloads recoverRuns times to measure the reload P50
// (the Phase 4 exit metric), and then queries against the reloaded (mmap)
// index so the whole persistence path is exercised end-to-end.
func Build(ds *Dataset, m, efC int, snapshotPath string, recoverRuns int) (*Built, error) {
	cfg := hnsw.DefaultConfig(ds.Dim)
	cfg.M = m
	cfg.MaxM = 2 * m
	cfg.EfConstruction = efC
	cfg.Metric = ds.Metric
	cfg.Distance = nil // resolve from Metric

	ix, err := hnsw.New(cfg)
	if err != nil {
		return nil, err
	}
	t0 := time.Now()
	for i, v := range ds.Base {
		if err := ix.Add(strconv.Itoa(i), v); err != nil {
			return nil, fmt.Errorf("add %d: %w", i, err)
		}
	}
	b := &Built{
		index:  ix,
		M:      m,
		EfC:    efC,
		BuildS: time.Since(t0).Seconds(),
		RSSmb:  readRSSMB(),
		N:      len(ds.Base),
		Dim:    ds.Dim,
	}

	if snapshotPath != "" {
		if err := ix.Snapshot(snapshotPath); err != nil {
			return nil, fmt.Errorf("snapshot: %w", err)
		}
		if recoverRuns < 1 {
			recoverRuns = 1
		}
		lat := make([]float64, 0, recoverRuns)
		var rec *hnsw.Index
		for r := 0; r < recoverRuns; r++ {
			if rec != nil {
				_ = rec.Close()
			}
			tr := time.Now()
			rec, err = hnsw.Recover(snapshotPath)
			if err != nil {
				return nil, fmt.Errorf("recover: %w", err)
			}
			lat = append(lat, time.Since(tr).Seconds()*1000)
		}
		b.RecoverMs = percentile(lat, 50)
		_ = ix.Close() // free the in-memory build; query the snapshot
		b.index = rec
		b.recovered = true
	}
	return b, nil
}

// Query runs the full query set at one efSearch and returns the row.
func (b *Built) Query(ds *Dataset, efSearch, topK int) Result {
	n := len(ds.Queries)
	lat := make([]float64, 0, n)
	var recallSum float64

	if n > 0 { // warm the mmap pages; not counted
		_, _ = b.index.Search(ds.Queries[0], topK, efSearch)
	}
	for qi, q := range ds.Queries {
		t := time.Now()
		res, _ := b.index.Search(q, topK, efSearch)
		lat = append(lat, time.Since(t).Seconds()*1000)

		got := make([]int, 0, len(res))
		for _, r := range res {
			if id, e := strconv.Atoi(r.ID); e == nil {
				got = append(got, id)
			}
		}
		var truth []int32
		if qi < len(ds.GroundTruth) {
			truth = ds.GroundTruth[qi]
		}
		recallSum += RecallAtK(got, truth, topK)
	}

	var totalS float64
	for _, l := range lat {
		totalS += l / 1000
	}
	qps := 0.0
	if totalS > 0 {
		qps = float64(n) / totalS
	}
	recall := 0.0
	if n > 0 {
		recall = recallSum / float64(n)
	}
	return Result{
		Index:        "go-hnsw",
		M:            b.M,
		EfC:          b.EfC,
		EfSearch:     efSearch,
		Recall:       recall,
		QPS:          qps,
		P50ms:        percentile(lat, 50),
		P99ms:        percentile(lat, 99),
		BuildS:       b.BuildS,
		RSSmb:        b.RSSmb,
		RecoverP50ms: b.RecoverMs,
		N:            b.N,
		Dim:          b.Dim,
	}
}

// Close releases the underlying index (and its mmap if recovered).
func (b *Built) Close() {
	if b.index != nil {
		_ = b.index.Close()
		b.index = nil
	}
}

func percentile(xs []float64, p float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := append([]float64(nil), xs...)
	sort.Float64s(s)
	idx := int(math.Ceil(p/100*float64(len(s)))) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(s) {
		idx = len(s) - 1
	}
	return s[idx]
}

func heapMB() float64 {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)
	return float64(m.HeapAlloc) / (1 << 20)
}
