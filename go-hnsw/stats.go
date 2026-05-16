package hnsw

import "sync/atomic"

// Stats are wrapped in atomic counters so we can flush them without locking
// the index. The bench harness reads them after each query batch.
type Stats struct {
	DistanceCalls atomic.Uint64
	LayerHops     atomic.Uint64
	CandidatePops atomic.Uint64
}

// Snapshot returns a non-atomic copy.
func (s *Stats) Snapshot() (distCalls, layerHops, candPops uint64) {
	return s.DistanceCalls.Load(), s.LayerHops.Load(), s.CandidatePops.Load()
}
