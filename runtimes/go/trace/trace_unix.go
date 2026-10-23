//go:build unix

package trace

import (
	"encoding/binary"
	"sync/atomic"
	"syscall"
)

var fd atomic.Int32

func init() { fd.Store(-1) }

// Open opens the FIFO at path for writing without blocking. It reports false, leaving tracing off,
// when the FIFO is missing or nobody reads it.
func Open(path string) bool {
	f, err := syscall.Open(path, syscall.O_WRONLY|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return false
	}
	if old := fd.Swap(int32(f)); old >= 0 {
		syscall.Close(int(old))
	}
	return true
}

// Line records that line n of the tool file is about to run. Every error is ignored: a full pipe
// (EAGAIN) drops the record, a closed one (EPIPE) drops them all.
func Line(n uint32) {
	f := fd.Load()
	if f < 0 {
		return
	}
	var b [4]byte
	binary.LittleEndian.PutUint32(b[:], n)
	// a raw write: an *os.File on a non-blocking pipe waits for room in the poller instead of
	// failing with EAGAIN, which would stall the tool. 4 bytes < PIPE_BUF, so a record is atomic.
	syscall.Write(int(f), b[:])
}

// Close stops tracing.
func Close() {
	if f := fd.Swap(-1); f >= 0 {
		syscall.Close(int(f))
	}
}
