//go:build unix

package hnsw

import (
	"os"

	"golang.org/x/sys/unix"
)

// mapFile maps the whole file read-only. The mapping outlives the *os.File, so
// callers may close the fd immediately after; the pages stay valid until
// munmap. This is what keeps Recover O(parse small arrays): the 512 MB vector
// arena is paged in lazily by the kernel, never copied.
func mapFile(f *os.File, size int) ([]byte, error) {
	return unix.Mmap(int(f.Fd()), 0, size, unix.PROT_READ, unix.MAP_SHARED)
}

func munmap(b []byte) error {
	if len(b) == 0 {
		return nil
	}
	return unix.Munmap(b)
}
