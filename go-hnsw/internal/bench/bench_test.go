package bench

import (
	"path/filepath"
	"testing"
)

func TestFvecsIvecs_Roundtrip(t *testing.T) {
	dir := t.TempDir()
	fp := filepath.Join(dir, "v.fvecs")
	ip := filepath.Join(dir, "g.ivecs")

	vecs := [][]float32{{1, 2, 3}, {4, 5, 6}, {-1, 0, 0.5}}
	rows := [][]int32{{0, 2, 1}, {2, 1, 0}}
	if err := WriteFvecs(fp, vecs); err != nil {
		t.Fatal(err)
	}
	if err := WriteIvecs(ip, rows); err != nil {
		t.Fatal(err)
	}

	gotV, err := ReadFvecs(fp, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(gotV) != len(vecs) {
		t.Fatalf("fvecs len = %d, want %d", len(gotV), len(vecs))
	}
	for i := range vecs {
		for j := range vecs[i] {
			if gotV[i][j] != vecs[i][j] {
				t.Fatalf("fvecs[%d][%d] = %v, want %v", i, j, gotV[i][j], vecs[i][j])
			}
		}
	}

	gotG, err := ReadIvecs(ip, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(gotG) != len(rows) {
		t.Fatalf("ivecs len = %d, want %d", len(gotG), len(rows))
	}
	for i := range rows {
		for j := range rows[i] {
			if gotG[i][j] != rows[i][j] {
				t.Fatalf("ivecs[%d][%d] = %v, want %v", i, j, gotG[i][j], rows[i][j])
			}
		}
	}
}

func TestReadFvecs_Limit(t *testing.T) {
	dir := t.TempDir()
	fp := filepath.Join(dir, "v.fvecs")
	if err := WriteFvecs(fp, [][]float32{{1, 1}, {2, 2}, {3, 3}, {4, 4}}); err != nil {
		t.Fatal(err)
	}
	got, err := ReadFvecs(fp, 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 {
		t.Fatalf("limited read = %d, want 2", len(got))
	}
}

func TestRecallAtK(t *testing.T) {
	truth := []int32{10, 20, 30, 40, 50}
	cases := []struct {
		got  []int
		k    int
		want float64
	}{
		{[]int{10, 20, 30, 40, 50}, 5, 1.0},
		{[]int{1, 2, 3, 4, 5}, 5, 0.0},
		{[]int{10, 20, 99, 98, 97}, 5, 0.4},
		{[]int{10, 20}, 2, 1.0},
		{nil, 5, 0.0},
	}
	for i, c := range cases {
		if got := RecallAtK(c.got, truth, c.k); got != c.want {
			t.Fatalf("case %d: recall = %v, want %v", i, got, c.want)
		}
	}
}

func TestBruteForceGT_NearestIsSelf(t *testing.T) {
	base := [][]float32{{0, 0}, {10, 10}, {0, 1}}
	queries := [][]float32{{0, 0}}
	gt := BruteForceGT(base, queries, 2)
	if gt[0][0] != 0 {
		t.Fatalf("nearest to (0,0) = %d, want index 0", gt[0][0])
	}
	if gt[0][1] != 2 {
		t.Fatalf("second nearest = %d, want index 2 (0,1)", gt[0][1])
	}
}

// End-to-end harness smoke test: build → snapshot → recover → query on a small
// synthetic set. Recall should be high and the reload latency populated.
func TestBuildQuery_SyntheticSmoke(t *testing.T) {
	ds := Synthetic(3000, 40, 16, 10, 99)
	snap := filepath.Join(t.TempDir(), "smoke.hnsw")

	built, err := Build(ds, 16, 200, snap, 3)
	if err != nil {
		t.Fatalf("Build: %v", err)
	}
	defer built.Close()
	if !built.recovered {
		t.Fatalf("expected the queried index to be the recovered snapshot")
	}
	if built.RecoverMs <= 0 {
		t.Fatalf("recover P50 not measured: %v", built.RecoverMs)
	}

	res := built.Query(ds, 128, 10)
	if res.Recall < 0.90 {
		t.Fatalf("recall@10 = %.3f, want >= 0.90", res.Recall)
	}
	if res.QPS <= 0 {
		t.Fatalf("QPS not measured: %v", res.QPS)
	}
	if res.N != 3000 || res.Dim != 16 {
		t.Fatalf("metadata wrong: n=%d dim=%d", res.N, res.Dim)
	}
}
