package hnsw

import "unsafe"

// This file holds the zero-copy reinterpretations between byte slices and
// numeric slices used by the snapshot reader/writer (persist.go). They assume
// a little-endian host; init() asserts that, since x86-64 and arm64 (our only
// targets) are both little-endian and a big-endian host would silently read
// garbage out of an mmap'd snapshot.

func init() {
	x := uint16(1)
	if *(*byte)(unsafe.Pointer(&x)) != 1 {
		panic("hnsw: snapshots require a little-endian platform")
	}
}

// uint32SliceToBytes views a []uint32 as raw bytes for a single Write call.
func uint32SliceToBytes(s []uint32) []byte {
	if len(s) == 0 {
		return nil
	}
	return unsafe.Slice((*byte)(unsafe.Pointer(&s[0])), len(s)*4)
}

// float32SliceToBytes views a []float32 as raw bytes for a single Write call.
func float32SliceToBytes(s []float32) []byte {
	if len(s) == 0 {
		return nil
	}
	return unsafe.Slice((*byte)(unsafe.Pointer(&s[0])), len(s)*4)
}

// bytesToUint32 aliases a byte span (typically inside an mmap) as []uint32.
// The caller must guarantee 4-byte alignment and len(b)%4 == 0.
func bytesToUint32(b []byte) []uint32 {
	if len(b) == 0 {
		return nil
	}
	return unsafe.Slice((*uint32)(unsafe.Pointer(&b[0])), len(b)/4)
}

// bytesToFloat32 aliases a byte span (typically inside an mmap) as []float32.
// The caller must guarantee 4-byte alignment and len(b)%4 == 0.
func bytesToFloat32(b []byte) []float32 {
	if len(b) == 0 {
		return nil
	}
	return unsafe.Slice((*float32)(unsafe.Pointer(&b[0])), len(b)/4)
}
