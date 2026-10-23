//go:build unix

package trace

import (
	"encoding/binary"
	"os"
	"path/filepath"
	"syscall"
	"testing"
	"time"
)

// fifo makes a FIFO and opens it the way the host does: the read end and a write end of its own.
func fifo(t *testing.T) (path string, r int) {
	t.Helper()
	path = filepath.Join(t.TempDir(), "trace")
	if err := syscall.Mkfifo(path, 0o666); err != nil {
		t.Fatal(err)
	}
	r, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		t.Fatal(err)
	}
	w, err := syscall.Open(path, syscall.O_WRONLY|syscall.O_NONBLOCK, 0)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { syscall.Close(r); syscall.Close(w); Close() })
	return path, r
}

func TestLineWritesLittleEndianRecords(t *testing.T) {
	path, r := fifo(t)
	if !Open(path) {
		t.Fatal("Open failed")
	}
	Line(7)
	Line(300)
	b := make([]byte, 16)
	n, err := syscall.Read(r, b)
	if err != nil || n != 8 {
		t.Fatalf("read %d bytes, %v", n, err)
	}
	if a, c := binary.LittleEndian.Uint32(b), binary.LittleEndian.Uint32(b[4:]); a != 7 || c != 300 {
		t.Fatalf("records %d %d", a, c)
	}
}

func TestFullPipeDropsWithoutBlocking(t *testing.T) {
	path, _ := fifo(t) // nobody drains it
	if !Open(path) {
		t.Fatal("Open failed")
	}
	start := time.Now()
	for i := 0; i < 200_000; i++ { // 800 KB of records into a 64 KB pipe
		Line(uint32(i%50 + 1))
	}
	if d := time.Since(start); d > 5*time.Second {
		t.Fatalf("writing to a full pipe took %v", d)
	}
}

func TestOpenFailsWithoutAReaderOrAFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trace")
	if err := syscall.Mkfifo(path, 0o666); err != nil {
		t.Fatal(err)
	}
	if Open(path) { // no reader: ENXIO, never a blocking open
		t.Fatal("Open succeeded without a reader")
	}
	if Open(filepath.Join(t.TempDir(), "missing")) {
		t.Fatal("Open succeeded on a missing path")
	}
	Line(1) // tracing is off: a no-op
}

func TestPathPrefersTheNewVariable(t *testing.T) {
	t.Setenv(EnvLegacy, "/old")
	t.Setenv(Env, "")
	if Path() != "/old" {
		t.Fatalf("Path() = %q, want the legacy value", Path())
	}
	t.Setenv(Env, "/new")
	if Path() != "/new" {
		t.Fatalf("Path() = %q, want the new value", Path())
	}
	os.Unsetenv(Env)
	os.Unsetenv(EnvLegacy)
	if Start() {
		t.Fatal("Start succeeded without a trace variable")
	}
}
