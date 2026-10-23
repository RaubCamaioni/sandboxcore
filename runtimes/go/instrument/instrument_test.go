package instrument

import (
	"go/parser"
	"go/token"
	"regexp"
	"strconv"
	"strings"
	"testing"
)

const tool = `package main // a tool

import (
	"fmt"
	"os"
)

var greeting = "hi"

func main() {
	total := 0
	for i := 0; i < 3; i++ { total += i }
outer:
	for {
		switch total {
		case 3: fmt.Println(greeting)
			break outer
		default:
			total--
		}
	}
	ch := make(chan int, 1)
	select {
	case ch <- 1:
		f := func() int { return <-ch }
		fmt.Fprintln(os.Stderr, f())
	}
	if total > 0 {
	} else if total < 0 {
		return
	}
}
`

var call = regexp.MustCompile(`__sandboxcore_trace\.Line\((\d+)\); `)

func instrumented(t *testing.T, src string) string {
	t.Helper()
	out, err := Source("tool.go", []byte(src))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := parser.ParseFile(token.NewFileSet(), "out.go", out, 0); err != nil {
		t.Fatalf("instrumented source does not parse: %v\n%s", err, out)
	}
	return string(out)
}

func TestEveryOriginalLineKeepsItsNumberAndText(t *testing.T) {
	out := instrumented(t, tool)
	src, got := strings.Split(tool, "\n"), strings.Split(out, "\n")
	if len(got) != len(src) {
		t.Fatalf("%d lines became %d", len(src), len(got))
	}
	for i := range src {
		stripped := call.ReplaceAllString(got[i], "")
		stripped = strings.Replace(stripped, `; import __sandboxcore_trace "`+ImportPath+`"`, "", 1)
		stripped = strings.Replace(stripped, "; var _ = __sandboxcore_trace.Start()", "", 1)
		if stripped != src[i] {
			t.Fatalf("line %d changed:\n%q\n%q", i+1, src[i], got[i])
		}
	}
}

func TestEachStatementReportsItsOwnLine(t *testing.T) {
	out := instrumented(t, tool)
	for i, line := range strings.Split(out, "\n") {
		for _, m := range call.FindAllStringSubmatch(line, -1) {
			if m[1] != strconv.Itoa(i+1) {
				t.Fatalf("line %d reports %s: %q", i+1, m[1], line)
			}
		}
	}
	// a labeled statement reports its label's line; `else if` is part of its if statement
	for _, want := range []int{11, 12, 13, 15, 16, 17, 19, 22, 23, 25, 26, 28, 30} {
		if !strings.Contains(out, "__sandboxcore_trace.Line("+strconv.Itoa(want)+"); ") {
			t.Errorf("line %d is not reported", want)
		}
	}
}

func TestClausesAreNotPrecededByCalls(t *testing.T) {
	out := instrumented(t, tool)
	if regexp.MustCompile(`Line\(\d+\); (case|default)`).MatchString(out) {
		t.Fatalf("a call precedes a case clause:\n%s", out)
	}
}

func TestStartRunsBeforeTheToolsOwnVariables(t *testing.T) {
	out := instrumented(t, tool)
	start, greeting := strings.Index(out, "__sandboxcore_trace.Start()"), strings.Index(out, "var greeting")
	if start < 0 || start > greeting {
		t.Fatalf("Start is not declared before the tool's variables:\n%s", out)
	}
}

func TestAFileWithoutImportsGetsImportAndStartTogether(t *testing.T) {
	out := instrumented(t, "package main\n\nfunc main() {\n\tprintln(1)\n}\n")
	want := `package main; import __sandboxcore_trace "` + ImportPath + `"; var _ = __sandboxcore_trace.Start()` + "\n"
	if !strings.HasPrefix(out, want) || !strings.Contains(out, "\t__sandboxcore_trace.Line(4); println(1)") {
		t.Fatalf("got:\n%s", out)
	}
}

func TestSyntaxErrorsAreReported(t *testing.T) {
	if _, err := Source("tool.go", []byte("package main\nfunc main( {\n")); err == nil {
		t.Fatal("no error for a syntax error")
	}
}
