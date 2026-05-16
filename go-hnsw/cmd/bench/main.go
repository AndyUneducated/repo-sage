// Command hnsw-bench runs the SIFT-1M benchmark and emits a CSV row of
// (M, efConstruction, efSearch, build_seconds, qps, recall@10, p50_ms, p99_ms,
//
//	rss_mb).
//
// Phase 5 lands the actual benchmark; this stub documents the CLI surface.
package main

import (
	"flag"
	"fmt"
)

func main() {
	dataset := flag.String("dataset", "sift1m", "dataset name (sift1m | sift10m)")
	M := flag.Int("M", 16, "out-degree")
	efC := flag.Int("efC", 200, "efConstruction")
	ef := flag.Int("ef", 64, "efSearch")
	flag.Parse()

	fmt.Printf("dataset=%s M=%d efC=%d ef=%d\n", *dataset, *M, *efC, *ef)
	// Phase 5: load fvecs, build index, run query set, print CSV row.
}
