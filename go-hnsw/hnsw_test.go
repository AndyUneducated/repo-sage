package hnsw

import (
	"math/rand"
	"testing"
)

func TestIndex_AddAndSearch_Trivial(t *testing.T) {
	cfg := DefaultConfig(4)
	cfg.M = 4
	cfg.EfConstruction = 16
	cfg.EfSearch = 16
	ix, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}

	vecs := map[string][]float32{
		"a": {1, 0, 0, 0},
		"b": {0.99, 0.01, 0, 0},
		"c": {0, 1, 0, 0},
		"d": {0, 0, 1, 0},
		"e": {0, 0, 0, 1},
	}
	for id, v := range vecs {
		if err := ix.Add(id, v); err != nil {
			t.Fatalf("Add %s: %v", id, err)
		}
	}
	if ix.Len() != 5 {
		t.Fatalf("Len = %d, want 5", ix.Len())
	}

	got, err := ix.Search([]float32{1, 0, 0, 0}, 2, 0)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(got) != 2 {
		t.Fatalf("expected 2 results, got %d", len(got))
	}
	if got[0].ID != "a" {
		t.Fatalf("nearest = %q, want %q (results=%v)", got[0].ID, "a", got)
	}
	if got[1].ID != "b" {
		t.Fatalf("second = %q, want %q (results=%v)", got[1].ID, "b", got)
	}
}

func TestIndex_DimMismatch(t *testing.T) {
	ix, _ := New(DefaultConfig(4))
	if err := ix.Add("a", []float32{1, 2, 3}); err == nil {
		t.Fatalf("expected dim mismatch error")
	}
	if _, err := ix.Search([]float32{1, 2, 3}, 1, 0); err == nil {
		t.Fatalf("expected dim mismatch error on Search")
	}
}

func TestIndex_RandomRecall(t *testing.T) {
	// Sanity: against 1k 32-d gaussians, top-1 recall on random queries
	// should be perfect because the graph contains every candidate.
	const n = 1024
	const dim = 32
	rng := rand.New(rand.NewSource(42))
	cfg := DefaultConfig(dim)
	cfg.EfSearch = 64
	ix, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	pts := make([][]float32, n)
	ids := make([]string, n)
	for i := 0; i < n; i++ {
		v := make([]float32, dim)
		for j := range v {
			v[j] = float32(rng.NormFloat64())
		}
		pts[i] = v
		ids[i] = idForIndex(i)
		if err := ix.Add(ids[i], v); err != nil {
			t.Fatalf("Add: %v", err)
		}
	}

	// For each of 50 random queries, check the brute-force nearest is
	// in the top-5 returned by the index.
	hits := 0
	for q := 0; q < 50; q++ {
		query := make([]float32, dim)
		for j := range query {
			query[j] = float32(rng.NormFloat64())
		}
		bestID := bruteForceNearest(query, pts, ids)
		got, err := ix.Search(query, 5, 64)
		if err != nil {
			t.Fatalf("Search: %v", err)
		}
		for _, r := range got {
			if r.ID == bestID {
				hits++
				break
			}
		}
	}
	if hits < 48 {
		t.Fatalf("recall@5 = %d/50, want >= 48", hits)
	}
}

func TestIndex_AddReplacesExisting(t *testing.T) {
	ix, _ := New(DefaultConfig(2))
	if err := ix.Add("a", []float32{1, 0}); err != nil {
		t.Fatalf("Add: %v", err)
	}
	if err := ix.Add("a", []float32{0, 1}); err != nil {
		t.Fatalf("re-Add: %v", err)
	}
	if ix.Len() != 1 {
		t.Fatalf("Len after re-Add = %d, want 1", ix.Len())
	}
	got, _ := ix.Search([]float32{0, 1}, 1, 0)
	if len(got) != 1 || got[0].ID != "a" {
		t.Fatalf("expected re-Add to update vector, got %v", got)
	}
}

func bruteForceNearest(q []float32, pts [][]float32, ids []string) string {
	best := -1
	bestD := float32(1e30)
	for i, p := range pts {
		d := Cosine(q, p)
		if d < bestD {
			best = i
			bestD = d
		}
	}
	return ids[best]
}

func idForIndex(i int) string {
	return string(rune('A'+i%26)) + "_" + intToString(i)
}

func intToString(i int) string {
	if i == 0 {
		return "0"
	}
	digits := []byte{}
	neg := false
	if i < 0 {
		neg = true
		i = -i
	}
	for i > 0 {
		digits = append([]byte{byte('0' + i%10)}, digits...)
		i /= 10
	}
	if neg {
		digits = append([]byte{'-'}, digits...)
	}
	return string(digits)
}
