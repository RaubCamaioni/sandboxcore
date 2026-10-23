// Package trace is the Go runtime's writer for the sandboxcore line trace protocol
// (docs/trace-protocol.md): it opens the FIFO the host offers and writes 4-byte line records,
// never blocking and never failing the program.
//
// Instrumented tools (see package instrument) call Start once, before anything else runs, and Line
// in front of every statement.
package trace

import "os"

const (
	// Env carries the trace FIFO's path.
	Env = "SANDBOXCORE_TRACE"
	// EnvLegacy carries the same path for hosts and images from before sandboxcore.
	EnvLegacy = "PYWEBTOOLS_TRACE"
)

// Path is the trace FIFO the host offers, or "" when the run is not traced.
func Path() string {
	if p := os.Getenv(Env); p != "" {
		return p
	}
	return os.Getenv(EnvLegacy)
}

// Start opens the FIFO the host offers, if any. It reports whether tracing is on; when it is not,
// Line does nothing.
func Start() bool {
	p := Path()
	return p != "" && Open(p)
}
