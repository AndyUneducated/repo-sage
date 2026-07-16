package grpcserver

import (
	"database/sql"
	"encoding/binary"
	"errors"
	"fmt"
	"math"

	_ "modernc.org/sqlite" // pure-Go SQLite driver — no CGO at build time
)

// LoadFromSQLite opens the index at `dbPath` and bulk-feeds the embeddings
// table into the server. Vectors are stored as little-endian float32 BLOBs
// (see reposage/storage/embeddings_store.py). Returns the count loaded.
//
// We match against `model` so deploys with multiple embedding sets can pin
// the server to one. An empty model string loads everything.
func (s *Server) LoadFromSQLite(dbPath, model string) (uint64, error) {
	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return 0, fmt.Errorf("open sqlite: %w", err)
	}
	defer db.Close()

	// Verify the table exists; running against a Phase 1 DB without the
	// embeddings table should fail loudly rather than silently load zero.
	var name string
	err = db.QueryRow(
		"SELECT name FROM sqlite_master WHERE type='table' AND name='embeddings'",
	).Scan(&name)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, fmt.Errorf("no `embeddings` table in %s; run `reposage index` first", dbPath)
	}
	if err != nil {
		return 0, fmt.Errorf("inspect schema: %w", err)
	}

	var rows *sql.Rows
	if model == "" {
		rows, err = db.Query(
			"SELECT chunk_id, vector, dim FROM embeddings ORDER BY chunk_id",
		)
	} else {
		rows, err = db.Query(
			"SELECT chunk_id, vector, dim FROM embeddings WHERE model = ? ORDER BY chunk_id",
			model,
		)
	}
	if err != nil {
		return 0, fmt.Errorf("query embeddings: %w", err)
	}
	defer rows.Close()

	// Buffer rows and insert in batches so the write lock is taken once per
	// flush rather than once per vector (see hnsw.Index.AddBatch).
	const flushEvery = 1024
	var n uint64
	ids := make([]string, 0, flushEvery)
	vecs := make([][]float32, 0, flushEvery)

	flush := func() error {
		if len(ids) == 0 {
			return nil
		}
		s.mu.Lock()
		added, err := s.index.AddBatch(ids, vecs)
		s.mu.Unlock()
		n += uint64(added)
		ids = ids[:0]
		vecs = vecs[:0]
		if err != nil {
			return fmt.Errorf("add batch: %w", err)
		}
		return nil
	}

	for rows.Next() {
		var chunkID string
		var blob []byte
		var dim int
		if err := rows.Scan(&chunkID, &blob, &dim); err != nil {
			return n, fmt.Errorf("scan row: %w", err)
		}
		if dim != s.cfg.Dim {
			return n, fmt.Errorf(
				"row %s dim=%d does not match server dim=%d",
				chunkID, dim, s.cfg.Dim,
			)
		}
		vec, err := decodeFloat32LE(blob, dim)
		if err != nil {
			return n, fmt.Errorf("decode %s: %w", chunkID, err)
		}
		ids = append(ids, chunkID)
		vecs = append(vecs, vec)
		if len(ids) >= flushEvery {
			if err := flush(); err != nil {
				return n, err
			}
		}
	}
	if err := rows.Err(); err != nil {
		return n, fmt.Errorf("iter embeddings: %w", err)
	}
	if err := flush(); err != nil {
		return n, err
	}
	return n, nil
}

// decodeFloat32LE turns a little-endian float32 byte slice into []float32.
func decodeFloat32LE(blob []byte, dim int) ([]float32, error) {
	if len(blob) != dim*4 {
		return nil, fmt.Errorf("blob len %d != dim*4=%d", len(blob), dim*4)
	}
	out := make([]float32, dim)
	for i := 0; i < dim; i++ {
		bits := binary.LittleEndian.Uint32(blob[i*4 : (i+1)*4])
		out[i] = math.Float32frombits(bits)
	}
	return out, nil
}
