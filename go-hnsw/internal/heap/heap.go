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

// NewMinHeap pre-allocates room for `cap` items.
func NewMinHeap(cap int) *MinHeap {
	return &MinHeap{data: make([]Item, 0, cap)}
}

func (h *MinHeap) Len() int      { return len(h.data) }
func (h *MinHeap) Peek() Item    { return h.data[0] }
func (h *MinHeap) Items() []Item { return h.data }
func (h *MinHeap) Reset()        { h.data = h.data[:0] }

func (h *MinHeap) Push(it Item) {
	h.data = append(h.data, it)
	// sift-up
	i := len(h.data) - 1
	for i > 0 {
		parent := (i - 1) >> 1
		if h.data[parent].Dist <= h.data[i].Dist {
			break
		}
		h.data[parent], h.data[i] = h.data[i], h.data[parent]
		i = parent
	}
}

func (h *MinHeap) Pop() Item {
	top := h.data[0]
	n := len(h.data) - 1
	h.data[0] = h.data[n]
	h.data = h.data[:n]
	// sift-down
	i := 0
	for {
		l := 2*i + 1
		if l >= n {
			break
		}
		r := l + 1
		smallest := l
		if r < n && h.data[r].Dist < h.data[l].Dist {
			smallest = r
		}
		if h.data[i].Dist <= h.data[smallest].Dist {
			break
		}
		h.data[i], h.data[smallest] = h.data[smallest], h.data[i]
		i = smallest
	}
	return top
}

// MaxHeap returns the farthest item via Pop. Used for the "ef-bounded
// candidate" set so we can evict the worst when full.
type MaxHeap struct{ data []Item }

// NewMaxHeap pre-allocates room for `cap` items.
func NewMaxHeap(cap int) *MaxHeap {
	return &MaxHeap{data: make([]Item, 0, cap)}
}

func (h *MaxHeap) Len() int      { return len(h.data) }
func (h *MaxHeap) Peek() Item    { return h.data[0] }
func (h *MaxHeap) Items() []Item { return h.data }
func (h *MaxHeap) Reset()        { h.data = h.data[:0] }

func (h *MaxHeap) Push(it Item) {
	h.data = append(h.data, it)
	i := len(h.data) - 1
	for i > 0 {
		parent := (i - 1) >> 1
		if h.data[parent].Dist >= h.data[i].Dist {
			break
		}
		h.data[parent], h.data[i] = h.data[i], h.data[parent]
		i = parent
	}
}

func (h *MaxHeap) Pop() Item {
	top := h.data[0]
	n := len(h.data) - 1
	h.data[0] = h.data[n]
	h.data = h.data[:n]
	i := 0
	for {
		l := 2*i + 1
		if l >= n {
			break
		}
		r := l + 1
		largest := l
		if r < n && h.data[r].Dist > h.data[l].Dist {
			largest = r
		}
		if h.data[i].Dist >= h.data[largest].Dist {
			break
		}
		h.data[i], h.data[largest] = h.data[largest], h.data[i]
		i = largest
	}
	return top
}
