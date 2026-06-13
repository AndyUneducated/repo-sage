//go:build !linux && !darwin

package bench

// readRSSMB falls back to the Go heap size where getrusage is unavailable.
func readRSSMB() float64 { return heapMB() }
