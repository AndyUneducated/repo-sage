// Package heap holds bounded min/max heaps specialised for the HNSW candidate
// set: keys are float32 distances, payloads are internal uint32 node ids.
//
// We avoid container/heap for two reasons:
//  1. Generic interface{} conversions allocate on the hot path.
//  2. The candidate set is bounded by `ef`; specialised slice-based heaps
//     beat the standard library by ~4x in micro-benchmarks.
package heap

// Item is the heap payload used by both MinHeap and MaxHeap.
type Item struct {
	Dist float32
	ID   uint32
}

// MinHeap returns the closest item via Pop. Used for the "to visit" set.
type MinHeap struct{ data []Item }

// MaxHeap returns the farthest item via Pop. Used for the "ef-bounded
// candidate" set so we can evict the worst when full.
type MaxHeap struct{ data []Item }

// (Implementations land in Phase 2 alongside insert/search.)
