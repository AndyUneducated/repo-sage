// Command hnsw-server is the in-house HNSW gRPC façade consumed by the
// Python retrieval stack.
//
// Lifecycle:
//
//  1. Open the SQLite index at --db.
//  2. Stream the `embeddings` rows into a fresh hnsw.Index on the
//     configured (M, efConstruction, efSearch) tuple.
//  3. Bind --addr and serve gRPC until SIGINT/SIGTERM.
//
// Phase 5 will add snapshot/recover so cold start is O(log n) rather than
// O(n insert). Phase 2 is in-memory only.
package main

import (
	"flag"
	"log"
	"net"
	"os"
	"os/signal"
	"syscall"
	"time"

	"google.golang.org/grpc"

	hnsw "github.com/AndyUneducated/repo-sage/go-hnsw"
	pb "github.com/AndyUneducated/repo-sage/go-hnsw/hnswpb"
	"github.com/AndyUneducated/repo-sage/go-hnsw/internal/grpcserver"
)

func main() {
	addr := flag.String("addr", "127.0.0.1:50051", "bind address for the gRPC server")
	dbPath := flag.String("db", "./data/reposage.db", "path to the reposage SQLite index")
	model := flag.String("model", "BAAI/bge-en-v1.5", "embedding model label; empty = load all")
	dim := flag.Int("dim", 768, "vector dimensionality")
	m := flag.Int("m", 16, "HNSW out-degree (paper M)")
	efC := flag.Int("ef-construction", 200, "HNSW efConstruction")
	efS := flag.Int("ef-search", 64, "HNSW default efSearch")
	flag.Parse()

	cfg := hnsw.DefaultConfig(*dim)
	cfg.M = *m
	cfg.EfConstruction = *efC
	cfg.EfSearch = *efS

	srv, err := grpcserver.New(cfg, *model)
	if err != nil {
		log.Fatalf("hnsw-server: new server: %v", err)
	}

	// Cold start: fill the index from SQLite. Missing DB file is non-fatal
	// for first runs (allows operating against an empty index until the
	// first `reposage index` writes embeddings).
	if _, err := os.Stat(*dbPath); err == nil {
		t0 := time.Now()
		n, err := srv.LoadFromSQLite(*dbPath, *model)
		if err != nil {
			log.Fatalf("hnsw-server: load: %v", err)
		}
		log.Printf("hnsw-server: loaded %d vectors from %s in %s", n, *dbPath, time.Since(t0))
	} else {
		log.Printf("hnsw-server: %s missing, starting empty", *dbPath)
	}

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("hnsw-server: listen: %v", err)
	}
	gs := grpc.NewServer()
	pb.RegisterHnswServiceServer(gs, srv)
	log.Printf("hnsw-server: listening on %s (dim=%d M=%d efC=%d efS=%d)",
		*addr, *dim, *m, *efC, *efS)

	// Graceful shutdown on SIGINT / SIGTERM.
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		log.Printf("hnsw-server: shutting down")
		gs.GracefulStop()
	}()

	if err := gs.Serve(lis); err != nil {
		log.Fatalf("hnsw-server: serve: %v", err)
	}
}
