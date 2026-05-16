// Command hnsw-server is the gRPC façade consumed by the Python retriever.
//
// Phase 2 wires up real handlers; for now `--help` lists the supported
// flags so the docker image can be built end-to-end.
package main

import (
	"flag"
	"fmt"
	"log"
	"os"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:50051", "bind address for the gRPC server")
	dataDir := flag.String("data-dir", "./data/hnsw", "directory for mmap snapshots")
	dim := flag.Int("dim", 768, "vector dimensionality")
	flag.Parse()

	if err := run(*addr, *dataDir, *dim); err != nil {
		log.Fatalf("hnsw-server: %v", err)
	}
}

func run(addr, dataDir string, dim int) error {
	if dim <= 0 {
		return fmt.Errorf("invalid --dim=%d", dim)
	}
	fmt.Fprintf(os.Stderr, "hnsw-server: would bind to %s, snapshots in %s, dim=%d\n", addr, dataDir, dim)
	// Phase 2: instantiate hnsw.Index, register gRPC service, ListenAndServe.
	return nil
}
