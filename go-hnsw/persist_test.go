package hnsw

import (
	"math/rand"
	"os"
	"path/filepath"
	"testing"
)

func buildRandomIndex(t *testing.T, n, dim int, metric Metric) (*Index, [][]float32) {
	t.Helper()
	cfg := DefaultConfig(dim)
	cfg.Metric = metric
	cfg.Distance = nil // force resolution from Metric (mirrors recover)
	ix, err := New(cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	rng := rand.New(rand.NewSource(7))
	pts := make([][]float32, n)
	for i := 0; i < n; i++ {
		v := make([]float32, dim)
		for j := range v {
			v[j] = float32(rng.NormFloat64())
		}
		pts[i] = v
		if err := ix.Add(idForIndex(i), v); err != nil {
			t.Fatalf("Add: %v", err)
		}
	}
	return ix, pts
}

func sameResults(a, b []Result) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].ID != b[i].ID || a[i].Distance != b[i].Distance {
			return false
		}
	}
	return true
}

func TestSnapshotRecover_Roundtrip(t *testing.T) {
	for _, metric := range []Metric{MetricCosine, MetricL2} {
		ix, pts := buildRandomIndex(t, 600, 24, metric)
		path := filepath.Join(t.TempDir(), "index.hnsw")
		if err := ix.Snapshot(path); err != nil {
			t.Fatalf("Snapshot: %v", err)
		}
		rec, err := Recover(path)
		if err != nil {
			t.Fatalf("Recover: %v", err)
		}
		defer rec.Close()

		if rec.Len() != ix.Len() {
			t.Fatalf("Len mismatch: recovered %d != %d", rec.Len(), ix.Len())
		}
		if rec.Metric() != metric {
			t.Fatalf("metric mismatch: %v != %v", rec.Metric(), metric)
		}
		// Identical search results across 30 queries.
		for q := 0; q < 30; q++ {
			query := pts[q*7%len(pts)]
			before, _ := ix.Search(query, 10, 64)
			after, err := rec.Search(query, 10, 64)
			if err != nil {
				t.Fatalf("recovered Search: %v", err)
			}
			if !sameResults(before, after) {
				t.Fatalf("metric=%v query=%d results differ:\n  before=%v\n  after =%v",
					metric, q, before, after)
			}
		}
	}
}

func TestSnapshot_Empty(t *testing.T) {
	ix, err := New(DefaultConfig(8))
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	path := filepath.Join(t.TempDir(), "empty.hnsw")
	if err := ix.Snapshot(path); err != nil {
		t.Fatalf("Snapshot empty: %v", err)
	}
	rec, err := Recover(path)
	if err != nil {
		t.Fatalf("Recover empty: %v", err)
	}
	defer rec.Close()
	if rec.Len() != 0 {
		t.Fatalf("recovered empty Len = %d, want 0", rec.Len())
	}
	if got, _ := rec.Search([]float32{1, 0, 0, 0, 0, 0, 0, 0}, 5, 0); len(got) != 0 {
		t.Fatalf("search on empty recovered index returned %v", got)
	}
}

func TestRecover_RejectsBadFile(t *testing.T) {
	dir := t.TempDir()

	badMagic := filepath.Join(dir, "bad.hnsw")
	buf := make([]byte, headerSize)
	copy(buf, "XXXX")
	if err := os.WriteFile(badMagic, buf, 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Recover(badMagic); err == nil {
		t.Fatalf("expected error on bad magic")
	}

	tooSmall := filepath.Join(dir, "small.hnsw")
	if err := os.WriteFile(tooSmall, []byte("HNSW"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Recover(tooSmall); err == nil {
		t.Fatalf("expected error on truncated header")
	}
}

// writeSnapshotTmp must produce a complete, independently-recoverable image at
// the .tmp path, while leaving any pre-existing committed snapshot untouched
// until the rename. This is the atomicity boundary.
func TestSnapshot_TmpIsCompleteBeforeCommit(t *testing.T) {
	ixOld, _ := buildRandomIndex(t, 100, 8, MetricCosine)
	ixNew, _ := buildRandomIndex(t, 250, 8, MetricCosine)
	path := filepath.Join(t.TempDir(), "index.hnsw")

	if err := ixOld.Snapshot(path); err != nil {
		t.Fatalf("snapshot old: %v", err)
	}
	tmp, err := ixNew.writeSnapshotTmp(path)
	if err != nil {
		t.Fatalf("writeSnapshotTmp: %v", err)
	}
	// Committed file still holds the OLD index (100 nodes).
	old, err := Recover(path)
	if err != nil {
		t.Fatalf("recover committed: %v", err)
	}
	if old.Len() != 100 {
		t.Fatalf("committed snapshot Len = %d, want 100 (tmp must not affect it)", old.Len())
	}
	old.Close()
	// The tmp itself is a complete NEW index (250 nodes).
	staged, err := Recover(tmp)
	if err != nil {
		t.Fatalf("recover tmp: %v", err)
	}
	if staged.Len() != 250 {
		t.Fatalf("tmp snapshot Len = %d, want 250", staged.Len())
	}
	staged.Close()
}

// A failed rename must not destroy the previous snapshot, and the .tmp must be
// cleaned up.
func TestSnapshot_RenameFailureKeepsOld(t *testing.T) {
	orig := renameHook
	defer func() { renameHook = orig }()

	ixOld, _ := buildRandomIndex(t, 100, 8, MetricCosine)
	ixNew, _ := buildRandomIndex(t, 250, 8, MetricCosine)
	path := filepath.Join(t.TempDir(), "index.hnsw")
	if err := ixOld.Snapshot(path); err != nil {
		t.Fatalf("snapshot old: %v", err)
	}

	renameHook = func(_, _ string) error { return os.ErrPermission }
	if err := ixNew.Snapshot(path); err == nil {
		t.Fatalf("expected Snapshot to fail when rename fails")
	}
	if _, err := os.Stat(path + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("tmp file should be cleaned up after rename failure")
	}
	renameHook = orig
	rec, err := Recover(path)
	if err != nil {
		t.Fatalf("recover after failed rename: %v", err)
	}
	defer rec.Close()
	if rec.Len() != 100 {
		t.Fatalf("after failed rename Len = %d, want 100 (old preserved)", rec.Len())
	}
}

// A recovered (frozen) index must serve searches, and the first Add must thaw
// it into owned memory and reflect the new vector.
func TestRecover_FrozenThenAddThaws(t *testing.T) {
	ix, _ := buildRandomIndex(t, 200, 8, MetricCosine)
	path := filepath.Join(t.TempDir(), "index.hnsw")
	if err := ix.Snapshot(path); err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	rec, err := Recover(path)
	if err != nil {
		t.Fatalf("Recover: %v", err)
	}
	defer rec.Close()
	if !rec.graph.frozen {
		t.Fatalf("recovered index should start frozen")
	}

	newVec := []float32{9, 9, 9, 9, 9, 9, 9, 9}
	if err := rec.Add("brand-new", newVec); err != nil {
		t.Fatalf("Add after recover: %v", err)
	}
	if rec.graph.frozen {
		t.Fatalf("Add should have thawed the index")
	}
	if rec.Len() != 201 {
		t.Fatalf("Len after Add = %d, want 201", rec.Len())
	}
	got, err := rec.Search(newVec, 1, 64)
	if err != nil {
		t.Fatalf("Search: %v", err)
	}
	if len(got) != 1 || got[0].ID != "brand-new" {
		t.Fatalf("nearest to new vector = %v, want brand-new", got)
	}
}

func TestClose_RejectsFurtherUse(t *testing.T) {
	ix, _ := buildRandomIndex(t, 50, 8, MetricCosine)
	path := filepath.Join(t.TempDir(), "index.hnsw")
	if err := ix.Snapshot(path); err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	rec, err := Recover(path)
	if err != nil {
		t.Fatalf("Recover: %v", err)
	}
	if err := rec.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	if err := rec.Close(); err != nil {
		t.Fatalf("second Close should be a no-op, got %v", err)
	}
	if _, err := rec.Search([]float32{1, 0, 0, 0, 0, 0, 0, 0}, 1, 0); err == nil {
		t.Fatalf("Search after Close should error")
	}
}
