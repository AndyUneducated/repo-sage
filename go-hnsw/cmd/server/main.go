// Command hnsw-server is the in-house HNSW gRPC façade consumed by the
// Python retrieval stack.
//
// Cold-start lifecycle (in priority order):
//
//  1. If --snapshot points at an existing file, mmap-Recover it (O(parse small
//     arrays); the 512 MB vector arena pages in lazily). This is the fast path.
//  2. Otherwise stream the `embeddings` rows out of the SQLite index at --db
//     into a fresh hnsw.Index, and — if --snapshot was given — write an initial
//     snapshot so the next boot takes the fast path.
//  3. Bind --addr and serve gRPC until SIGINT/SIGTERM. With --snapshot-on-exit
//     the index is persisted again on graceful shutdown.
package main

import (
	"flag"
	"fmt"
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
	snapshot := flag.String("snapshot", "", "snapshot path; recovered on boot if present, written after cold load")
	snapshotOnExit := flag.Bool("snapshot-on-exit", false, "persist the index on graceful shutdown")
	flag.Parse()

	cfg := hnsw.DefaultConfig(*dim)
	cfg.M = *m
	cfg.EfConstruction = *efC
	cfg.EfSearch = *efS

	srv, err := boot(cfg, *snapshot, *dbPath, *model, *dim)
	if err != nil {
		log.Fatalf("hnsw-server: boot: %v", err)
	}
	defer srv.Close()

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		log.Fatalf("hnsw-server: listen: %v", err)
	}
	gs := grpc.NewServer()
	pb.RegisterHnswServiceServer(gs, srv)
	log.Printf("hnsw-server: listening on %s (dim=%d M=%d efC=%d efS=%d)",
		*addr, *dim, *m, *efC, *efS)

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-stop
		log.Printf("hnsw-server: shutting down")
		if *snapshotOnExit && *snapshot != "" {
			t0 := time.Now()
			if err := srv.Snapshot(*snapshot); err != nil {
				log.Printf("hnsw-server: snapshot on exit failed: %v", err)
			} else {
				log.Printf("hnsw-server: snapshot written to %s in %s", *snapshot, time.Since(t0))
			}
		}
		gs.GracefulStop()
	}()

	if err := gs.Serve(lis); err != nil {
		log.Fatalf("hnsw-server: serve: %v", err)
	}
}

// boot chooses the fastest available cold-start path and returns a ready
// server.
func boot(cfg hnsw.Config, snapshot, dbPath, model string, dim int) (*grpcserver.Server, error) {
	if snapshot != "" {
		if _, err := os.Stat(snapshot); err == nil {
			t0 := time.Now()
			ix, rerr := hnsw.Recover(snapshot)
			if rerr != nil {
				return nil, rerr
			}
			if ix.Dim() != dim {
				return nil, fmt.Errorf("snapshot dim %d != --dim %d", ix.Dim(), dim)
			}
			log.Printf("hnsw-server: recovered %d vectors from snapshot %s in %s",
				ix.Len(), snapshot, time.Since(t0))
			return grpcserver.NewWithIndex(ix, cfg, model), nil
		}
	}

	srv, err := grpcserver.New(cfg, model)
	if err != nil {
		return nil, err
	}
	if _, err := os.Stat(dbPath); err == nil {
		t0 := time.Now()
		n, lerr := srv.LoadFromSQLite(dbPath, model)
		if lerr != nil {
			return nil, lerr
		}
		log.Printf("hnsw-server: loaded %d vectors from %s in %s", n, dbPath, time.Since(t0))
		if snapshot != "" && n > 0 {
			if serr := srv.Snapshot(snapshot); serr != nil {
				log.Printf("hnsw-server: initial snapshot failed: %v", serr)
			} else {
				log.Printf("hnsw-server: wrote initial snapshot to %s", snapshot)
			}
		}
	} else {
		log.Printf("hnsw-server: %s missing, starting empty", dbPath)
	}
	return srv, nil
}
