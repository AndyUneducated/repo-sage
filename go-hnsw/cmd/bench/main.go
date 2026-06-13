// Command hnsw-bench runs the SIFT-1M ANN benchmark (or a synthetic stand-in)
// and emits one CSV row per (M, efConstruction, efSearch) configuration:
//
//	index,M,efC,efSearch,recall,qps,p50_ms,p99_ms,build_s,rss_mb,recover_p50_ms,n,dim
//
// Examples:
//
//	# CI smoke (no download): build + snapshot + recover + query on synthetic data
//	hnsw-bench --synthetic 5000 --M 16 --efC 200 --ef 16,64,128 --snapshot /tmp/s.hnsw --header
//
//	# Full SIFT-1M sweep against a downloaded dataset
//	hnsw-bench --dataset-dir benchmarks/sift1m/data --M 8,16,32 --efC 100,200,400 \
//	  --ef 16,32,64,128,256 --metric l2 --snapshot /tmp/sift.hnsw --out results.csv
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"

	hnsw "github.com/AndyUneducated/repo-sage/go-hnsw"
	"github.com/AndyUneducated/repo-sage/go-hnsw/internal/bench"
)

func main() {
	datasetDir := flag.String("dataset-dir", "", "directory with SIFT *.fvecs/*.ivecs (empty → use --synthetic)")
	synthetic := flag.Int("synthetic", 0, "synthetic base size when no dataset-dir (0 disables)")
	synthQueries := flag.Int("synthetic-queries", 100, "synthetic query count")
	mList := flag.String("M", "16", "comma-separated out-degrees")
	efcList := flag.String("efC", "200", "comma-separated efConstruction values")
	efList := flag.String("ef", "64", "comma-separated efSearch values")
	metric := flag.String("metric", "l2", "distance metric: l2 | cosine | ip")
	topK := flag.Int("topk", 10, "neighbours per query (recall@topk)")
	maxBase := flag.Int("max-base", 0, "cap base vectors (0 = all; subset recomputes ground truth)")
	maxQueries := flag.Int("max-queries", 0, "cap query vectors (0 = all)")
	gtK := flag.Int("gt-k", 100, "ground-truth neighbours to keep for subsets/synthetic")
	snapshot := flag.String("snapshot", "", "snapshot path; set to also measure reload P50")
	recoverRuns := flag.Int("recover-runs", 5, "reload repetitions for the recover P50")
	out := flag.String("out", "", "append CSV rows to this file (default stdout)")
	header := flag.Bool("header", false, "print the CSV header first")
	flag.Parse()

	met, ok := hnsw.ParseMetric(*metric)
	if !ok {
		log.Fatalf("hnsw-bench: unknown metric %q", *metric)
	}

	ds, err := loadDataset(*datasetDir, *synthetic, *synthQueries, *maxBase, *maxQueries, *gtK, met)
	if err != nil {
		log.Fatalf("hnsw-bench: %v", err)
	}
	log.Printf("hnsw-bench: dataset=%s base=%d queries=%d dim=%d metric=%s",
		ds.Name, len(ds.Base), len(ds.Queries), ds.Dim, ds.Metric)

	w, closeOut, err := openOutput(*out)
	if err != nil {
		log.Fatalf("hnsw-bench: open output: %v", err)
	}
	defer closeOut()
	if *header {
		fmt.Fprintln(w, bench.CSVHeader())
	}

	ms := parseInts(*mList)
	efcs := parseInts(*efcList)
	efs := parseInts(*efList)

	for _, m := range ms {
		for _, efc := range efcs {
			built, err := bench.Build(ds, m, efc, *snapshot, *recoverRuns)
			if err != nil {
				log.Fatalf("hnsw-bench: build M=%d efC=%d: %v", m, efc, err)
			}
			for _, ef := range efs {
				row := built.Query(ds, ef, *topK)
				fmt.Fprintln(w, row.CSV())
			}
			built.Close()
		}
	}
}

func loadDataset(dir string, synthetic, synthQueries, maxBase, maxQueries, gtK int, met hnsw.Metric) (*bench.Dataset, error) {
	if dir != "" {
		ds, err := bench.LoadSIFT(dir, maxBase, maxQueries, gtK)
		if err != nil {
			return nil, fmt.Errorf("load SIFT from %s: %w (download with benchmarks/sift1m/fetch_sift1m.sh)", dir, err)
		}
		return ds, nil
	}
	if synthetic > 0 {
		// Synthetic ground truth is computed under L2, so the index must use
		// L2 too; --metric only applies to real datasets.
		_ = met
		return bench.Synthetic(synthetic, synthQueries, 128, gtK, 1337), nil
	}
	return nil, fmt.Errorf("provide --dataset-dir <sift dir> or --synthetic <N>")
}

func openOutput(path string) (*os.File, func(), error) {
	if path == "" {
		return os.Stdout, func() {}, nil
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, nil, err
	}
	return f, func() { _ = f.Close() }, nil
}

func parseInts(s string) []int {
	parts := strings.Split(s, ",")
	out := make([]int, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		n, err := strconv.Atoi(p)
		if err != nil {
			log.Fatalf("hnsw-bench: bad integer %q", p)
		}
		out = append(out, n)
	}
	if len(out) == 0 {
		log.Fatalf("hnsw-bench: empty integer list")
	}
	return out
}
