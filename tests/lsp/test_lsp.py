"""Tests for siec.lsp: the analysis behind the language server."""

from siec.lsp import Report, analyze, outline, search_paths


def write(path, text):
    """
    Create a source file (and its parents) with the given text.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_analyze_clean_source_reports_nothing(tmp_path):
    """
    A file that compiles yields no report.
    """
    src = write(tmp_path / "main.sie", "fn main() -> i32 { return 0; }")
    assert analyze(src, []) is None


def test_analyze_reports_the_error_with_its_line(tmp_path):
    """
    A compile error comes back with the file and its 1-based line.
    """
    src = write(tmp_path / "main.sie",
                'fn main() -> i32 {\n    return "no";\n}')

    report = analyze(src, [])
    assert report == Report(str(src.resolve()), 2,
                            "cannot implicitly convert i8* to i32")


def test_analyze_reports_parse_errors(tmp_path):
    """
    Lexer and parser errors locate the same way as codegen's.
    """
    src = write(tmp_path / "main.sie", "fn main() -> i32 { return 0 }")

    report = analyze(src, [])
    assert report.file == str(src.resolve())
    assert report.line == 1
    assert "expected" in report.message


def test_analyze_prefers_overlay_text(tmp_path):
    """
    An overlay stands in for the file on disk: the editor's unsaved
    buffer analyzes, not the stale saved copy.
    """
    src = write(tmp_path / "main.sie", "fn main() -> i32 { broken }")
    fixed = "fn main() -> i32 { return 0; }"

    assert analyze(src, [], {str(src.resolve()): fixed}) is None


def test_analyze_blames_an_imported_modules_file(tmp_path):
    """
    An error inside an imported module carries that module's file.
    """
    mod = write(tmp_path / "util.sie",
                "fn f() -> i32;\nfn f() -> i64 { return 0; }")
    src = write(tmp_path / "main.sie",
                "import util;\n\nfn main() -> i32 { return 0; }")

    report = analyze(src, [])
    assert report.file == str(mod.resolve())


def test_analyze_checks_the_file_as_its_own_unit(tmp_path):
    """
    Analysis type-checks the edited file's bodies against an imported
    module's declarations without demanding the module's definitions
    emit, the way '-c' compiles.
    """
    write(tmp_path / "util.sie", "fn add(x: i32, y: i32) -> i32 { return x + y; }")
    src = write(tmp_path / "main.sie", """
        import { add } from util;

        fn main() -> i32 { return add(1, null); }
    """)

    report = analyze(src, [])
    assert report.file == str(src.resolve())
    assert report.line == 4


def test_outline_lists_declarations_in_source_order(tmp_path):
    """
    Every top-level declaration appears with its kind and line.
    """
    symbols = outline("""\
@const LIMIT = 8;

struct Point { x: i32; y: i32; }

interface Shape;

enum Color { RED, BLUE }

@type Pair = Point;

@static let count: i32 = 0;

fn area(p: Point) -> i32 { return p.x * p.y; }

fn Point::flip(&self) { }
""")

    assert [(s.name, s.kind, s.line) for s in symbols] == [
        ("LIMIT", "constant", 1),
        ("Point", "struct", 3),
        ("Shape", "interface", 5),
        ("Color", "enum", 7),
        ("Pair", "alias", 9),
        ("count", "variable", 11),
        ("area", "function", 13),
        ("Point::flip", "method", 15),
    ]


def test_outline_returns_none_when_the_text_does_not_parse():
    """
    Broken text yields None so the caller can keep the last good outline.
    """
    assert outline("fn broken( {") is None


def test_search_paths_finds_package_trees(tmp_path):
    """
    A workspace's 'packages/*/src' directories join the include path,
    after any configured ones, with the root and its 'lib/' closing.
    """
    (tmp_path / "packages" / "core" / "src").mkdir(parents=True)
    (tmp_path / "packages" / "libc" / "src").mkdir(parents=True)

    paths = search_paths(tmp_path, ["/explicit"])
    assert [str(p) for p in paths] == [
        "/explicit",
        str(tmp_path / "packages" / "core" / "src"),
        str(tmp_path / "packages" / "libc" / "src"),
        str(tmp_path),
        str(tmp_path / "lib"),
    ]


def unit(tmp_path, text, name="main.sie"):
    """
    Compile a source file as a unit for inspection.
    """
    from siec.lsp import compile_unit

    src = write(tmp_path / name, text)
    return compile_unit(src, []), src


def probe(analysis, src, line, col):
    """
    Inspect the written file at a 0-based position.
    """
    from siec.lsp import inspect

    return inspect(analysis, src.read_text(), line, col)


def test_inspect_types_a_local_variable(tmp_path):
    """
    Hovering a local shows its inferred type and sites its 'let'.
    """
    analysis, src = unit(tmp_path, """\
fn main() -> i32 {
    let count = 41;
    return count + 1;
}
""")

    finding = probe(analysis, src, 2, 11)
    assert finding.text == "count: i32"
    assert finding.targets == [(str(src.resolve()), 2)]


def test_inspect_shows_a_functions_overloads(tmp_path):
    """
    Hovering a function name lists every overload's signature and
    targets each declaration.
    """
    analysis, src = unit(tmp_path, """\
fn pick(n: i64) -> i64 { return n; }
fn pick(f: f64) -> f64 { return f; }

fn main() -> i32 {
    return pick(2) as i32;
}
""")

    finding = probe(analysis, src, 4, 11)
    assert finding.text == ("fn pick(n: i64) -> i64\n"
                            "fn pick(f: f64) -> f64")
    assert finding.targets == [(str(src.resolve()), 1), (str(src.resolve()), 2)]


def test_inspect_resolves_a_method_through_its_receiver(tmp_path):
    """
    Hovering a method call resolves the receiver's inferred type and
    shows the generic template's signature, sited at the template.
    """
    analysis, src = unit(tmp_path, """\
struct Box<T> { value: T; }

fn Box<T>::get(&self) -> T { return self.value; }

fn main() -> i32 {
    let b: Box<i32>;
    return b.get();
}
""")

    finding = probe(analysis, src, 6, 13)
    assert finding.text == "fn Box<T>::get(&Box<T>) -> T"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_types_a_field_through_the_chain(tmp_path):
    """
    Hovering a field types it through the receiver chain and sites its
    line in the struct's declaration.
    """
    analysis, src = unit(tmp_path, """\
struct Point {
    x: i32;
    y: i32;
}

fn main() -> i32 {
    let p: Point;
    return p.y;
}
""")

    finding = probe(analysis, src, 7, 13)
    assert finding.text == "y: i32"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_resolves_an_enum_member(tmp_path):
    """
    Hovering 'E::M' shows the member's value and sites its line.
    """
    analysis, src = unit(tmp_path, """\
enum Color {
    RED,
    BLUE = 7,
}

fn main() -> i32 {
    return Color::BLUE as i32;
}
""")

    finding = probe(analysis, src, 6, 18)
    assert finding.text == "Color::BLUE = 7"
    assert finding.targets == [(str(src.resolve()), 3)]


def test_inspect_resolves_a_module_member(tmp_path):
    """
    Hovering a qualified module member resolves through the binding to
    the module's declaration.
    """
    write(tmp_path / "util.sie", """\
fn add(x: i32, y: i32) -> i32 { return x + y; }
""")

    analysis, src = unit(tmp_path, """\
import util;

fn main() -> i32 {
    return util.add(40, 2);
}
""")

    finding = probe(analysis, src, 3, 16)
    assert finding.text == "fn add(x: i32, y: i32) -> i32"
    assert finding.targets == [(str((tmp_path / "util.sie").resolve()), 1)]


def test_inspect_shows_a_struct_declaration(tmp_path):
    """
    Hovering a type name renders the struct with its fields.
    """
    analysis, src = unit(tmp_path, """\
struct Point { x: i32; y: i32; }

fn main() -> i32 {
    let p: Point;
    return p.x;
}
""")

    finding = probe(analysis, src, 3, 11)
    assert finding.text == "struct Point {\n    x: i32;\n    y: i32;\n}"
    assert finding.targets == [(str(src.resolve()), 1)]


def test_inspect_misses_off_any_name(tmp_path):
    """
    Positions on literals, operators, or blanks resolve to nothing.
    """
    analysis, src = unit(tmp_path, """\
fn main() -> i32 {
    return 40 + 2;
}
""")

    assert probe(analysis, src, 1, 12) is None
    assert probe(analysis, src, 0, 0) is None
