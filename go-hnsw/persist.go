package hnsw

// On-disk format (Phase 5):
//
//	header   : magic(4) | version(2) | dim(4) | M(2) | maxM(2) | efC(2)
//	           | levelMult(8 float64) | n(8) | maxLevel(2) | entry(4)
//	ids      : packed strings prefixed with their lengths (uint16)
//	vectors  : n * dim float32 contiguous (mmap-friendly)
//	layers   : per-node packed neighbour lists (CSR-style)
//
// We choose CSR over per-node slices because:
//   - mmap loads it as a single span, no allocation needed
//   - sequential layer-0 scans (most common during search) get cache locality
//
// Snapshot writes a fresh file atomically (tmp + rename).
func (ix *Index) Snapshot(_ string) error {
	return errPersistTODO
}

// Recover loads an index from disk. The vector arena is mmap'd; the graph
// adjacency is parsed eagerly because it is small relative to the vectors.
func Recover(_ string) (*Index, error) {
	return nil, errPersistTODO
}

var errPersistTODO = errPersistNotImplemented{}

type errPersistNotImplemented struct{}

func (errPersistNotImplemented) Error() string {
	return "hnsw: persistence not implemented yet (Phase 5)"
}
