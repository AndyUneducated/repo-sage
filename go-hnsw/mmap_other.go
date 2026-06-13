//go:build !unix

package hnsw

import (
	"io"
	"os"
)

// mapFile falls back to reading the whole file into memory on platforms
// without mmap (e.g. Windows). Correct, but pays a full copy and loses the
// lazy-paging win, so the recover-latency target only holds on unix hosts.
func mapFile(f *os.File, size int) ([]byte, error) {
	buf := make([]byte, size)
	if _, err := io.ReadFull(f, buf); err != nil {
		return nil, err
	}
	return buf, nil
}

// munmap is a no-op when mapFile returned an owned buffer; the GC reclaims it.
func munmap(_ []byte) error { return nil }
