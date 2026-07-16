package hnsw

import (
	"math/rand"
	"sync"
	"testing"
)

// TestIndex_AddBatch_MatchesSequentialAdd asserts AddBatch produces the same
// graph as calling Add one at a time with the same seed, so the batch
// fast-path is a pure performance optimisation with no behavioural drift.
func TestIndex_AddBatch_MatchesSequentialAdd(t *testing.T) {
	const n = 512
	const dim = 16
	rng := rand.New(rand.NewSource(7))
	pts := make([][]float32, n)
	ids := make([]string, n)
	for i := 0; i < n; i++ {
		v := make([]float32, dim)
		for j := range v {
			v[j] = float32(rng.NormFloat64())
		}
		pts[i] = v
		ids[i] = idForIndex(i)
	}

	seq, err := New(DefaultConfig(dim))
	if err != nil {
		t.Fatalf("New seq: %v", err)
	}
	for i := 0; i < n; i++ {
		if err := seq.Add(ids[i], pts[i]); err != nil {
			t.Fatalf("Add: %v", err)
		}
	}

	batch, err := New(DefaultConfig(dim))
	if err != nil {
		t.Fatalf("New batch: %v", err)
	}
	added, err := batch.AddBatch(ids, pts)
	if err != nil {
		t.Fatalf("AddBatch: %v", err)
	}
	if added != n {
		t.Fatalf("AddBatch added %d, want %d", added, n)
	}
	if batch.Len() != seq.Len() {
		t.Fatalf("Len batch=%d seq=%d", batch.Len(), seq.Len())
	}

	// Same seed + same insertion order => identical top-1 for every query.
	for q := 0; q < 40; q++ {
		query := make([]float32, dim)
		for j := range query {
			query[j] = float32(rng.NormFloat64())
		}
		gotSeq, _ := seq.Search(query, 1, 64)
		gotBatch, _ := batch.Search(query, 1, 64)
		if len(gotSeq) != 1 || len(gotBatch) != 1 {
			t.Fatalf("query %d: empty result seq=%v batch=%v", q, gotSeq, gotBatch)
		}
		if gotSeq[0].ID != gotBatch[0].ID {
			t.Fatalf("query %d: seq=%q batch=%q", q, gotSeq[0].ID, gotBatch[0].ID)
		}
	}
}

func TestIndex_AddBatch_LengthMismatch(t *testing.T) {
	ix, _ := New(DefaultConfig(2))
	if _, err := ix.AddBatch([]string{"a"}, [][]float32{{1, 0}, {0, 1}}); err == nil {
		t.Fatalf("expected ids/vecs length mismatch error")
	}
}

func TestIndex_AddBatch_RejectsBadDimBeforeMutating(t *testing.T) {
	ix, _ := New(DefaultConfig(3))
	ids := []string{"a", "b"}
	vecs := [][]float32{{1, 0, 0}, {0, 1}} // second row wrong dim
	if _, err := ix.AddBatch(ids, vecs); err == nil {
		t.Fatalf("expected dim error")
	}
	if ix.Len() != 0 {
		t.Fatalf("AddBatch mutated the index despite a bad row: Len=%d", ix.Len())
	}
}

// TestIndex_ConcurrentSearchWhileAdding is a -race guard: many reader
// goroutines Search while a single writer Adds. The RWMutex must let the
// readers run concurrently and keep every access race-free. Run via
// `go test -race`.
func TestIndex_ConcurrentSearchWhileAdding(t *testing.T) {
	const dim = 24
	rng := rand.New(rand.NewSource(11))
	cfg := DefaultConfig(dim)
	ix, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	// Seed so searches have something to walk from the first iteration.
	for i := 0; i < 128; i++ {
		v := make([]float32, dim)
		for j := range v {
			v[j] = float32(rng.NormFloat64())
		}
		if err := ix.Add(idForIndex(i), v); err != nil {
			t.Fatalf("seed Add: %v", err)
		}
	}

	stop := make(chan struct{})
	var writerWG sync.WaitGroup
	var readerWG sync.WaitGroup

	// Writer: keep adding fresh vectors until the readers are done.
	writerWG.Add(1)
	go func() {
		defer writerWG.Done()
		wrng := rand.New(rand.NewSource(99))
		i := 128
		for {
			select {
			case <-stop:
				return
			default:
			}
			v := make([]float32, dim)
			for j := range v {
				v[j] = float32(wrng.NormFloat64())
			}
			if err := ix.Add(idForIndex(i), v); err != nil {
				t.Errorf("concurrent Add: %v", err)
				return
			}
			i++
		}
	}()

	// Readers: hammer Search concurrently with the writer.
	const readers = 8
	const queriesPerReader = 200
	for r := 0; r < readers; r++ {
		readerWG.Add(1)
		go func(seed int64) {
			defer readerWG.Done()
			qrng := rand.New(rand.NewSource(seed))
			for q := 0; q < queriesPerReader; q++ {
				query := make([]float32, dim)
				for j := range query {
					query[j] = float32(qrng.NormFloat64())
				}
				if _, err := ix.Search(query, 5, 32); err != nil {
					t.Errorf("concurrent Search: %v", err)
					return
				}
			}
		}(int64(r) + 1)
	}

	// Readers finish first, then the writer is signalled to stop.
	readerWG.Wait()
	close(stop)
	writerWG.Wait()
}
