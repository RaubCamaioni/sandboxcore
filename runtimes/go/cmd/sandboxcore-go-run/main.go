// Command sandboxcore-go-run runs one Go tool file in a run directory, line-traced when the host offers a
// trace (docs/trace-protocol.md).
//
// Experimental: this is the trace half of a Go runtime. The tool is a `package main` file with a
// `func main()`; the args.json/result.json contract for Go tools is still to come. The tool is
// compiled on every run, which counts against the sandbox's time limit: a production runtime would
// compile at upload.
//
//	sandboxcore-go-run --file /runs/x/tool.go --workdir /runs/x --module /opt/sandboxcore/runtimes/go
package main

import (
	"errors"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/RaubCamaioni/sandboxcore/runtimes/go/instrument"
	"github.com/RaubCamaioni/sandboxcore/runtimes/go/trace"
)

const modulePath = "github.com/RaubCamaioni/sandboxcore/runtimes/go"

func main() {
	file := flag.String("file", "", "the tool's Go source (package main)")
	workdir := flag.String("workdir", "", "the run directory, the tool's working directory")
	module := flag.String("module", os.Getenv("SANDBOXCORE_GO_MODULE"), "the directory of this module (runtimes/go)")
	flag.Parse()
	if *file == "" || *workdir == "" {
		flag.Usage()
		os.Exit(2)
	}
	code, err := run(*file, *workdir, *module)
	if err != nil {
		fmt.Fprintln(os.Stderr, "sandboxcore-go-run:", err)
		os.Exit(1)
	}
	os.Exit(code)
}

func run(file, workdir, module string) (int, error) {
	src, err := os.ReadFile(file)
	if err != nil {
		return 0, err
	}
	gomod := "module sandboxcoretool\n\ngo 1.22\n"
	// only a traced run is instrumented; the tool then needs the trace package from this module
	if trace.Path() != "" {
		if module == "" {
			return 0, errors.New("a traced run needs --module or SANDBOXCORE_GO_MODULE")
		}
		if src, err = instrument.Source(filepath.Base(file), src); err != nil {
			return 0, err
		}
		abs, err := filepath.Abs(module)
		if err != nil {
			return 0, err
		}
		gomod += fmt.Sprintf("\nrequire %s v0.0.0\n\nreplace %s => %s\n", modulePath, modulePath, abs)
	}

	build, err := os.MkdirTemp("", "sandboxcore-go-")
	if err != nil {
		return 0, err
	}
	defer os.RemoveAll(build)
	// the file keeps its name, so compiler messages name the uploaded file
	if err := os.WriteFile(filepath.Join(build, filepath.Base(file)), src, 0o644); err != nil {
		return 0, err
	}
	if err := os.WriteFile(filepath.Join(build, "go.mod"), []byte(gomod), 0o644); err != nil {
		return 0, err
	}
	bin := filepath.Join(build, "tool")
	compile := exec.Command("go", "build", "-o", bin, ".")
	compile.Dir = build
	// no network, no workspace, no toolchain download: everything comes from the image
	compile.Env = append(os.Environ(), "GOFLAGS=-mod=mod", "GOWORK=off", "GOTOOLCHAIN=local", "GOPROXY=off", "CGO_ENABLED=0")
	compile.Stdout, compile.Stderr = os.Stderr, os.Stderr
	if err := compile.Run(); err != nil {
		return 0, fmt.Errorf("build failed: %w", err)
	}

	tool := exec.Command(bin)
	tool.Dir = workdir
	tool.Stdin, tool.Stdout, tool.Stderr = os.Stdin, os.Stdout, os.Stderr
	if err := tool.Run(); err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode(), nil
		}
		return 0, err
	}
	return 0, nil
}
