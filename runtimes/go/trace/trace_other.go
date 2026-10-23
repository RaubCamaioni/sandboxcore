//go:build !unix

package trace

// Open reports false: the trace FIFO exists only on Unix hosts.
func Open(path string) bool { return false }

// Line does nothing off Unix.
func Line(n uint32) {}

// Close does nothing off Unix.
func Close() {}
