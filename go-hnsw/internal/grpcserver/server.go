// Package grpcserver hosts the HNSW service: a thin RPC layer over the
// hnsw.Index from the parent package. It is deliberately a separate package
// so the production binary can wire in observability without leaking gRPC
// types into the algorithm core.
package grpcserver

import (
	"context"
	"errors"
	"sync"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	hnsw "github.com/AndyUneducated/repo-sage/go-hnsw"
	pb "github.com/AndyUneducated/repo-sage/go-hnsw/hnswpb"
)

// Server is the gRPC façade. It wraps a single hnsw.Index, plus the model
// label so clients can confirm they're talking to a server that holds the
// vectors they expect.
//
// Concurrency (Phase 5): mu is a RWMutex so read RPCs (Search / Stats) take
// the read lock and run concurrently, while write RPCs (Add / BulkLoad /
// Snapshot / Close / LoadFromSQLite) take the write lock. This mirrors the
// single-writer/many-reader model of the underlying hnsw.Index and removes
// the false serialisation the earlier plain Mutex imposed on concurrent
// searches.
type Server struct {
	pb.UnimplementedHnswServiceServer

	mu    sync.RWMutex
	index *hnsw.Index
	cfg   hnsw.Config
	model string
}

// New constructs a server with an empty index. Callers are expected to
// BulkLoad or Add before calling Search.
func New(cfg hnsw.Config, model string) (*Server, error) {
	ix, err := hnsw.New(cfg)
	if err != nil {
		return nil, err
	}
	return &Server{index: ix, cfg: cfg, model: model}, nil
}

// NewWithIndex wraps an already-built index, e.g. one returned by
// hnsw.Recover. The caller's cfg (from server flags) is used for the Stats
// contract and dim validation; it must agree with how the snapshot was built.
func NewWithIndex(ix *hnsw.Index, cfg hnsw.Config, model string) *Server {
	return &Server{index: ix, cfg: cfg, model: model}
}

// Snapshot persists the current index to path atomically (tmp + fsync +
// rename). Used by the boot/exit lifecycle in cmd/server.
func (s *Server) Snapshot(path string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.index.Snapshot(path)
}

// Close releases the index (and its mmap if recovered).
func (s *Server) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.index.Close()
}

// Add inserts or replaces a vector under the given id. It takes the write
// lock so it is exclusive with other writers and with in-flight searches;
// concurrent reads resume as soon as it returns.
func (s *Server) Add(_ context.Context, req *pb.AddRequest) (*pb.AddResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "nil request")
	}
	if req.GetId() == "" {
		return nil, status.Error(codes.InvalidArgument, "id required")
	}
	vec := req.GetVector()
	if len(vec) != s.cfg.Dim {
		return nil, status.Errorf(
			codes.InvalidArgument,
			"vector dim %d != server dim %d", len(vec), s.cfg.Dim,
		)
	}
	s.mu.Lock()
	err := s.index.Add(req.GetId(), vec)
	size := uint64(s.index.Len())
	s.mu.Unlock()
	if err != nil {
		return nil, status.Errorf(codes.Internal, "add: %v", err)
	}
	return &pb.AddResponse{Ok: true, Size: size}, nil
}

// bulkLoadFlush bounds how many streamed vectors we buffer before taking the
// write lock once to AddBatch them. Batching amortises the lock hand-off over
// many inserts (the win of Index.AddBatch) while still bounding memory so a
// multi-million-vector stream doesn't materialise entirely in RAM.
const bulkLoadFlush = 1024

// BulkLoad is a streaming Add used by the indexer at startup. Incoming
// vectors are buffered and flushed in batches through Index.AddBatch, which
// takes the write lock once per flush instead of once per vector. We reply
// with a summary once the client closes the stream so transient errors
// mid-load abort the whole batch (the client retries).
func (s *Server) BulkLoad(stream pb.HnswService_BulkLoadServer) error {
	var inserted uint64
	ids := make([]string, 0, bulkLoadFlush)
	vecs := make([][]float32, 0, bulkLoadFlush)

	flush := func() error {
		if len(ids) == 0 {
			return nil
		}
		s.mu.Lock()
		n, err := s.index.AddBatch(ids, vecs)
		s.mu.Unlock()
		inserted += uint64(n)
		ids = ids[:0]
		vecs = vecs[:0]
		if err != nil {
			return status.Errorf(codes.Internal, "add: %v", err)
		}
		return nil
	}

	for {
		req, err := stream.Recv()
		if errors.Is(err, errEOF) || isEOF(err) {
			break
		}
		if err != nil {
			return err
		}
		if req.GetId() == "" {
			return status.Error(codes.InvalidArgument, "id required")
		}
		vec := req.GetVector()
		if len(vec) != s.cfg.Dim {
			return status.Errorf(
				codes.InvalidArgument,
				"vector dim %d != server dim %d", len(vec), s.cfg.Dim,
			)
		}
		ids = append(ids, req.GetId())
		vecs = append(vecs, vec)
		if len(ids) >= bulkLoadFlush {
			if err := flush(); err != nil {
				return err
			}
		}
	}
	if err := flush(); err != nil {
		return err
	}
	s.mu.RLock()
	size := uint64(s.index.Len())
	s.mu.RUnlock()
	return stream.SendAndClose(&pb.BulkLoadResponse{Inserted: inserted, Size: size})
}

// Search returns the top-k nearest neighbours.
func (s *Server) Search(_ context.Context, req *pb.SearchRequest) (*pb.SearchResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "nil request")
	}
	if req.GetTopK() == 0 {
		return nil, status.Error(codes.InvalidArgument, "top_k must be > 0")
	}
	vec := req.GetVector()
	if len(vec) != s.cfg.Dim {
		return nil, status.Errorf(
			codes.InvalidArgument,
			"vector dim %d != server dim %d", len(vec), s.cfg.Dim,
		)
	}
	s.mu.RLock()
	hits, err := s.index.Search(vec, int(req.GetTopK()), int(req.GetEfSearch()))
	s.mu.RUnlock()
	if err != nil {
		return nil, status.Errorf(codes.Internal, "search: %v", err)
	}
	out := make([]*pb.SearchHit, len(hits))
	for i, h := range hits {
		out[i] = &pb.SearchHit{Id: h.ID, Distance: h.Distance}
	}
	return &pb.SearchResponse{Hits: out}, nil
}

// Stats lets clients confirm dim / model agree before issuing inserts.
func (s *Server) Stats(_ context.Context, _ *pb.StatsRequest) (*pb.StatsResponse, error) {
	s.mu.RLock()
	size := uint64(s.index.Len())
	s.mu.RUnlock()
	return &pb.StatsResponse{
		Size:           size,
		Dim:            uint32(s.cfg.Dim),
		Model:          s.model,
		M:              uint32(s.cfg.M),
		EfConstruction: uint32(s.cfg.EfConstruction),
		EfSearch:       uint32(s.cfg.EfSearch),
	}, nil
}

// errEOF is wrapped via stream.Recv() when the client closes the half. We
// match against it loosely because gRPC-go returns io.EOF directly.
var errEOF = errors.New("hnsw grpcserver: stream closed")

func isEOF(err error) bool {
	if err == nil {
		return false
	}
	return err.Error() == "EOF"
}
