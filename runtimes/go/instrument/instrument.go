// Package instrument rewrites a Go tool file so it reports its lines through package trace.
//
// A call `__cwctrace.Line(n); ` goes in front of every statement of every statement list, on the
// same line, so every original line keeps its number (and compiler messages still point at the
// uploaded file). The trace package is imported right after the package clause and started by the
// first package-level variable, before any of the tool's own code runs.
package instrument

import (
	"fmt"
	"go/ast"
	"go/parser"
	"go/token"
	"sort"
)

// ImportPath is the trace package an instrumented tool imports.
const ImportPath = "github.com/RaubCamaioni/containerwebcore/runtimes/go/trace"

const alias = "__cwctrace"

type insertion struct {
	offset int
	text   string
}

// Source returns src with line trace calls added. filename is used in parse errors only.
func Source(filename string, src []byte) ([]byte, error) {
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, filename, src, parser.SkipObjectResolution)
	if err != nil {
		return nil, err
	}
	var ins []insertion
	statements := func(list []ast.Stmt) {
		for _, s := range list {
			switch s.(type) {
			case *ast.EmptyStmt, *ast.CaseClause, *ast.CommClause:
				// nothing runs at an empty statement; the clauses of a switch or select body are not
				// statements a call may precede (their own bodies are instrumented)
				continue
			}
			p := fset.Position(s.Pos())
			ins = append(ins, insertion{p.Offset, fmt.Sprintf("%s.Line(%d); ", alias, p.Line)})
		}
	}
	ast.Inspect(f, func(n ast.Node) bool {
		switch n := n.(type) {
		case *ast.BlockStmt:
			statements(n.List)
		case *ast.CaseClause:
			statements(n.Body)
		case *ast.CommClause:
			statements(n.Body)
		}
		return true
	})

	// the import right after the package clause; Start in a package-level variable declared before
	// all others, so it runs before the tool's own variables and init functions
	imp := fmt.Sprintf("; import %s %q", alias, ImportPath)
	start := fmt.Sprintf("; var _ = %s.Start()", alias)
	afterImports := -1
	for _, d := range f.Decls {
		if g, ok := d.(*ast.GenDecl); ok && g.Tok == token.IMPORT {
			afterImports = fset.Position(g.End()).Offset
		}
	}
	pkgEnd := fset.Position(f.Name.End()).Offset
	if afterImports < 0 {
		ins = append(ins, insertion{pkgEnd, imp + start})
	} else {
		ins = append(ins, insertion{pkgEnd, imp}, insertion{afterImports, start})
	}

	// apply from the end so earlier offsets stay valid. No two insertions share an offset: statements
	// start at distinct offsets, and the import and Start are one insertion when they would meet
	sort.SliceStable(ins, func(i, j int) bool { return ins[i].offset > ins[j].offset })
	out := append([]byte(nil), src...)
	for _, in := range ins {
		out = append(out[:in.offset], append([]byte(in.text), out[in.offset:]...)...)
	}
	return out, nil
}
