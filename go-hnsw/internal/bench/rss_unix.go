//go:build linux || darwin

package bench

import (
	"runtime"

	"golang.org/x/sys/unix"
)

// readRSSMB reports peak resident set size via getrusage. Linux returns
// ru_maxrss in kilobytes; macOS returns it in bytes — normalise both to MB.
func readRSSMB() float64 {
	var ru unix.Rusage
	if err := unix.Getrusage(unix.RUSAGE_SELF, &ru); err != nil {
		return heapMB()
	}
	max := float64(ru.Maxrss)
	if runtime.GOOS == "darwin" {
		return max / (1 << 20)
	}
	return max / 1024.0
}
