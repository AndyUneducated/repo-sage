package hnsw

import (
	"math"
	"testing"
)

func TestL2_Identical(t *testing.T) {
	v := []float32{1, 2, 3, 4}
	if got := L2(v, v); got != 0 {
		t.Fatalf("L2(v,v) = %v, want 0", got)
	}
}

func TestL2_Symmetry(t *testing.T) {
	a := []float32{1, 2, 3}
	b := []float32{4, 5, 6}
	if math.Abs(float64(L2(a, b)-L2(b, a))) > 1e-6 {
		t.Fatalf("L2 not symmetric")
	}
}

func TestCosine_Orthogonal(t *testing.T) {
	a := []float32{1, 0}
	b := []float32{0, 1}
	if got := Cosine(a, b); math.Abs(float64(got-1)) > 1e-6 {
		t.Fatalf("Cosine(orthogonal) = %v, want 1", got)
	}
}

func TestCosine_ZeroVector(t *testing.T) {
	a := []float32{0, 0, 0}
	b := []float32{1, 2, 3}
	if got := Cosine(a, b); got != 1 {
		t.Fatalf("Cosine(0, b) = %v, want 1 fallback", got)
	}
}

func TestNew_ValidatesConfig(t *testing.T) {
	if _, err := New(Config{Dim: 0}); err == nil {
		t.Fatalf("expected error for Dim=0")
	}
	if _, err := New(DefaultConfig(8)); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
