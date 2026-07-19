"""Tests for 'import': module resolution and bindings."""

import pytest

from tests.cli.test_cli import run_cli


def write_module(tmp_path):
    """
    Lay out 'math/util.sie' with a mix of exportable declarations.
    """
    mod = tmp_path / "math"
    mod.mkdir()
    (mod / "util.sie").write_text("""
        @const BASE = 40;
        @static let hidden: i32 = 9;

        fn add(x: i32, y: i32) -> i32 { return x + y; }
        @static fn helper() -> i32 { return 1; }
    """)


def test_qualified_access(tmp_path, monkeypatch):
    """
    'import a.b;' binds the module's members under 'a.b.<name>'.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import math.util;

        fn main() -> i32 {
            return math.util.add(math.util.BASE, 2);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_member_imports_with_aliases(tmp_path, monkeypatch):
    """
    'import { f as g, C } from a.b;' binds chosen members unqualified.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import { add as plus, BASE } from math.util;

        fn main() -> i32 {
            return plus(BASE, 2);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_module_alias(tmp_path, monkeypatch):
    """
    'import a.b as m;' rebinds the whole module under 'm'.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import math.util as u;

        fn main() -> i32 {
            return u.add(u.BASE, 2);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_import_cycles_load_once(tmp_path, monkeypatch):
    """
    Two modules importing each other resolve without recursing forever.
    """
    (tmp_path / "a.sie").write_text("""
        import b;

        fn from_a() -> i32 { return 40; }

        fn main() -> i32 { return b.from_b() + 2; }
    """)
    (tmp_path / "b.sie").write_text("""
        import a;

        fn from_b() -> i32 { return a.from_a(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, tmp_path / "a.sie", "--run") == 42


def test_statics_are_not_exported(tmp_path, monkeypatch, capsys):
    """
    A module's '@static' declarations stay its own.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import math.util;

        fn main() -> i32 { return math.util.hidden; }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "module 'math.util' has no member 'hidden'" in capsys.readouterr().err


def test_missing_member_is_an_error(tmp_path, monkeypatch, capsys):
    """
    Importing a member the module doesn't declare fails at the import.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import { nope } from math.util;

        fn main() -> i32 { return 0; }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "module 'math.util' has no member 'nope'" in capsys.readouterr().err


def test_missing_module_is_an_error(tmp_path, monkeypatch, capsys):
    """
    An unresolvable dotted path names itself in the error.
    """
    src = tmp_path / "main.sie"
    src.write_text("""
        import no.such.thing;

        fn main() -> i32 { return 0; }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "cannot resolve import 'no.such.thing'" in capsys.readouterr().err


def test_conditional_import_is_an_error(tmp_path, monkeypatch, capsys):
    """
    Imports resolve before '@if' conditions evaluate, so they cannot be
    conditional.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        @if (true) {
            import math.util;
        }

        fn main() -> i32 { return 0; }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "an 'import' cannot be conditional" in capsys.readouterr().err


def test_imported_names_are_scoped(tmp_path, monkeypatch, capsys):
    """
    'import a.b;' binds only the qualified names: the module's members do
    not leak into the importer unqualified.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import math.util;

        fn main() -> i32 {
            return add(40, 2);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "undefined function 'add'" in capsys.readouterr().err


def test_included_names_stay_in_view(tmp_path, monkeypatch):
    """
    '@include' is textual: the includer uses the included names directly.
    """
    write_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        @include("math/util")

        fn main() -> i32 {
            return add(BASE, 2);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_a_modules_failing_import_names_the_module(tmp_path, monkeypatch, capsys):
    """
    When an imported module's own import or include fails, the error
    blames the module file that wrote it, with its line.
    """
    (tmp_path / "mod.sie").write_text("fn helper() { }\nimport nope.missing;\n")
    src = tmp_path / "main.sie"
    src.write_text("import mod;\nfn main() -> i32 { return 0; }\n")

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "mod.sie at line 2: cannot resolve import 'nope.missing'" in capsys.readouterr().err


def test_a_modules_failing_include_names_the_module(tmp_path, monkeypatch, capsys):
    """
    A failing '@include' inside an imported module blames that module too.
    """
    (tmp_path / "mod.sie").write_text('\n@include("nowhere/gone")\n')
    src = tmp_path / "main.sie"
    src.write_text("import mod;\nfn main() -> i32 { return 0; }\n")

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "mod.sie at line 2: cannot resolve include 'nowhere/gone'" in capsys.readouterr().err


def test_dotted_generic_references(tmp_path, monkeypatch):
    """
    A qualified generic name works as a function value: explicit
    'util.identity<i32>' and bare 'util.identity' unified from context.
    """
    (tmp_path / "util.sie").write_text("""
        fn identity<T>(t: T) -> T { return t; }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import util;

        fn apply(f: fn(i32) -> i32, n: i32) -> i32 { return f(n); }

        fn main() -> i32 {
            let g = util.identity<i64>;
            let h: fn(i32) -> i32 = util.identity;

            return apply(util.identity, 20)
                + apply(util.identity<i32>, 1)
                + g(20) as i32
                + h(1);
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def write_shapes(tmp_path):
    """
    Lay out 'shapes.sie' with one exportable type of each kind.
    """
    (tmp_path / "shapes.sie").write_text("""
        struct Point { x: i32; y: i32; }
        struct Box<T> { value: T; }
        enum Color { RED = 1, BLUE = 2 }
        @type coord = i64;

        fn origin() -> Point {
            let p: Point = { 0, 0 };
            return p;
        }
    """)


def test_types_resolve_through_module_bindings(tmp_path, monkeypatch):
    """
    'import shapes;' binds the module's types under 'shapes.<Name>':
    structs, generic instantiations, aliases, and enums alike.
    """
    write_shapes(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import shapes;

        fn main() -> i32 {
            let p: shapes.Point = { 30, 10 };
            let b: shapes.Box<i32> = { 1 };
            let c: shapes.coord = 1;
            let o = shapes.origin();

            return p.x + p.y + b.value + c as i32 + o.x; // 42
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_member_imported_types_come_into_view(tmp_path, monkeypatch):
    """
    'import { Point, Box as Crate } from shapes;' binds types unqualified,
    a generic under its chosen name.
    """
    write_shapes(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import { Point, Box as Crate } from shapes;

        fn main() -> i32 {
            let p: Point = { 40, 1 };
            let c: Crate<i32> = { 1 };
            return p.x + p.y + c.value;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_unqualified_foreign_types_stay_out_of_view(tmp_path, monkeypatch, capsys):
    """
    A module's types don't leak unqualified: 'Point' needs its module's
    spelling or a member import.
    """
    write_shapes(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import shapes;

        fn main() -> i32 {
            let p: Point;
            return 0;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "unknown type 'Point'" in capsys.readouterr().err


def test_inferred_foreign_types_still_flow(tmp_path, monkeypatch):
    """
    A call's inferred return type works without importing the type's
    name: only written annotations are held to the file's view.
    """
    write_shapes(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import shapes;

        fn main() -> i32 {
            let o = shapes.origin();  // 'Point' inferred, never written
            return o.x + o.y + 42;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42
