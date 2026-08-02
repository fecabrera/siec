"""Tests for 'import': module resolution and bindings."""

import shutil
import subprocess

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


def test_module_alias_cannot_be_rebound(tmp_path, monkeypatch, capsys):
    """
    Two imports cannot silently point one module alias at different files.
    """
    (tmp_path / "one.sie").write_text("")
    (tmp_path / "two.sie").write_text("")
    src = tmp_path / "main.sie"
    src.write_text(
        "import one as dep;\n"
        "import two as dep;\n"
        "fn main() -> i32 { return 0; }\n")

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert ("main.sie at line 2: import binding 'dep' is already declared "
            "at line 1") in capsys.readouterr().err


def test_member_import_alias_cannot_be_rebound(tmp_path, monkeypatch, capsys):
    """
    A member binding names exactly one imported declaration in its file.
    """
    (tmp_path / "one.sie").write_text("fn first() {}\n")
    (tmp_path / "two.sie").write_text("fn second() {}\n")
    src = tmp_path / "main.sie"
    src.write_text(
        "import { first as selected } from one;\n"
        "import { second as selected } from two;\n"
        "fn main() -> i32 { return 0; }\n")

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert ("main.sie at line 2: import binding 'selected' is already "
            "declared at line 1") in capsys.readouterr().err


def test_module_and_member_imports_can_share_a_binding(tmp_path, monkeypatch):
    """
    A module prefix and an unqualified member have distinguishable uses.
    """
    (tmp_path / "one.sie").write_text(
        "fn value() -> i32 { return 21; }\n")
    src = tmp_path / "main.sie"
    src.write_text(
        "import one as selected;\n"
        "import { value as selected } from one;\n"
        "fn main() -> i32 { return selected.value() + selected(); }\n")

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_repeating_the_same_import_binding_is_idempotent(tmp_path, monkeypatch):
    """
    Repeating an identical import does not create an ambiguous binding.
    """
    (tmp_path / "answer.sie").write_text(
        "fn value() -> i32 { return 42; }\n")
    src = tmp_path / "main.sie"
    src.write_text("""
        import answer as module;
        import answer as module;
        import { value as answer } from answer;
        import { value as answer } from answer;

        fn main() -> i32 { return module.value() - answer(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


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


def write_private_module(tmp_path):
    """
    Lay out a module containing every declaration supported by '@private'.
    """
    (tmp_path / "private_api.sie").write_text("""
        @private @const BASE = 20;

        @private fn hidden_add(n: i32) -> i32 {
            return BASE + n;
        }

        @private struct Hidden {
            value: i32;
        }

        @private enum HiddenState {
            Ready = 1,
        }

        @private fn Hidden::read(const &self) -> i32 {
            return self.value + HiddenState::Ready - 1;
        }

        struct Public {
            value: i32;
        }

        @private fn Public::read(const &self) -> i32 {
            return self.value;
        }

        struct Box<T> {
            value: T;
        }

        @private fn Box<T>::read(const &self) -> T {
            return self.value;
        }

        fn answer() -> i32 {
            let hidden: Hidden = { hidden_add(1) };
            let public: Public = { 21 };
            return hidden.read() + public.read();
        }
    """)


def test_private_declarations_remain_usable_inside_their_module(
        tmp_path, monkeypatch):
    """
    Privacy changes the module surface, not uses within the defining module.
    """
    write_private_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import private_api;
        fn main() -> i32 { return private_api.answer(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


@pytest.mark.parametrize("body, missing", [
    ("return private_api.BASE;", "BASE"),
    ("return private_api.hidden_add(1);", "hidden_add"),
    ("let value: private_api.Hidden; return 0;", "Hidden"),
    ("return private_api.HiddenState::Ready;", "HiddenState"),
])
def test_private_names_are_absent_from_module_imports(
        tmp_path, monkeypatch, capsys, body, missing):
    """
    Qualified imports cannot reach private constants, functions, or structs.
    """
    write_private_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text(f"""
        import private_api;
        fn main() -> i32 {{ {body} }}
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert f"module 'private_api' has no member '{missing}'" in capsys.readouterr().err


def test_private_names_are_absent_from_member_imports(
        tmp_path, monkeypatch, capsys):
    """
    A private declaration is rejected directly by a member import.
    """
    write_private_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import { hidden_add } from private_api;
        fn main() -> i32 { return 0; }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "module 'private_api' has no member 'hidden_add'" in capsys.readouterr().err


def test_private_methods_are_not_reached_through_imported_types(
        tmp_path, monkeypatch, capsys):
    """
    Receiver-based lookup cannot bypass a private method's module boundary.
    """
    write_private_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import private_api;

        fn main() -> i32 {
            let value: private_api.Public = { 42 };
            return value.read();
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "undefined function 'value.read'" in capsys.readouterr().err


def test_private_generic_methods_are_not_reached_through_imported_types(
        tmp_path, monkeypatch, capsys):
    """
    Instantiating a public generic receiver does not expose private templates.
    """
    write_private_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import private_api;

        fn main() -> i32 {
            let value: private_api.Box<i32> = { 42 };
            return value.read();
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "undefined function 'value.read'" in capsys.readouterr().err


def test_include_can_access_private_declarations(tmp_path, monkeypatch):
    """
    '@include' is textual, so all private declaration forms remain in view.
    """
    write_private_module(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        @include("private_api")

        fn main() -> i32 {
            let hidden: Hidden = { hidden_add(1) };
            let public: Public = { BASE + 1 };
            let box: Box<i32> = { 42 };
            if (box.read() != 42) { return 1; }
            if (HiddenState::Ready != 1) { return 2; }
            return hidden.read() + public.read();
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_included_files_share_private_names_both_ways(tmp_path, monkeypatch):
    """
    Textual visibility also lets included code use root and sibling privates.
    """
    (tmp_path / "one.sie").write_text("""
        fn combined() -> i32 { return from_root() + from_two(); }
    """)
    (tmp_path / "two.sie").write_text("""
        @private fn from_two() -> i32 { return 2; }
    """)
    src = tmp_path / "main.sie"
    src.write_text("""
        @include("one")
        @include("two")

        @private fn from_root() -> i32 { return 40; }
        fn main() -> i32 { return combined(); }
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


def test_enum_members_resolve_through_module_bindings(tmp_path, monkeypatch, capsys):
    """
    'shapes.Color::RED' reaches an enum's member through its module; a
    member import (renamed or not) binds it unqualified; a bare foreign
    enum stays out of view.
    """
    write_shapes(tmp_path)
    src = tmp_path / "main.sie"
    src.write_text("""
        import shapes;
        import { Color as Hue } from shapes;

        fn main() -> i32 {
            let c = shapes.Color::RED;
            let d: shapes.Color = shapes.Color::BLUE;

            case (d) {
                when shapes.Color::BLUE: return 40 + c as i32 + Hue::RED;
            }
            return 0;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42

    leak = tmp_path / "leak.sie"
    leak.write_text("""
        import shapes;

        fn main() -> i32 { return Color::RED; }
    """)
    assert run_cli(monkeypatch, leak, "--run") == 1
    assert "unknown type 'Color'" in capsys.readouterr().err

def test_methods_resolve_on_carried_foreign_types(tmp_path, monkeypatch):
    """
    A method resolves on its receiver's carried type even when that
    type's name is not in the calling file's view: an element of a
    foreign generic field calls its methods without the caller ever
    importing the generic struct behind it.
    """
    mod = tmp_path / "coll"
    mod.mkdir()
    (mod / "list.sie").write_text("""
        struct List<T> { data: T*; length: u64; }
        fn List<T>::size(self: &List<T>) -> u64 { return self.length; }
    """)

    pkg = tmp_path / "pack"
    pkg.mkdir()
    (pkg / "info.sie").write_text("""
        import { List } from coll.list;

        struct Info { items: List<List<u8>>; }

        @static let backing: List<u8>;

        fn Info::fill(self: &Info) {
            backing.length = 3;
            self.items.data = &backing;
            self.items.length = 1;
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { Info } from pack.info;

        fn check(n: u64) -> i32 { return n as i32; }

        fn main() -> i32 {
            let info: Info;
            info.fill();

            // dotted-chain form, and an indexed receiver in argument
            // position - both resolve on the carried 'List<...>' type
            let n = info.items.size();
            if (n != 1) { return 1; }
            return check(info.items.data[0].size()) - 3;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_compile_only_leaves_imports_as_declarations(tmp_path, monkeypatch, capsys):
    """
    Under '-c' an imported module's functions stay declarations - its own
    unit defines them - while an '@include'd file's define here, C-style,
    and an imported '@inline' function defines in every unit that sees it.
    """
    mod = tmp_path / "math"
    mod.mkdir()
    (mod / "util.sie").write_text("""
        fn add(x: i32, y: i32) -> i32 { return x + y; }
        @inline fn twice(x: i32) -> i32 { return x * 2; }
    """)
    (tmp_path / "impl.sie").write_text("""
        fn shared() -> i32 { return 2; }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        @include("impl")
        import { add, twice } from math.util;

        fn main() -> i32 { return add(twice(20), shared()); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "-c", "--emit-llvm") == 0

    out = capsys.readouterr().out
    assert 'declare i32 @"add(i32,i32)"' in out
    assert 'define i32 @"shared()"' in out
    assert 'define linkonce_odr i32 @"twice(i32)"' in out


def test_whole_program_builds_define_imports(tmp_path, monkeypatch, capsys):
    """
    Without '-c' the build is the whole program: an imported module's
    functions define into the one module, as ever.
    """
    mod = tmp_path / "math"
    mod.mkdir()
    (mod / "util.sie").write_text("""
        fn add(x: i32, y: i32) -> i32 { return x + y; }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { add } from math.util;

        fn main() -> i32 { return add(40, 2); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--emit-llvm") == 0
    assert 'define i32 @"add(i32,i32)"' in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_separately_compiled_units_link_and_run(tmp_path, monkeypatch):
    """
    Each '-c' unit defines its own functions plus the generic instances
    it stamps; the shared instances merge at link and the program runs.
    """
    (tmp_path / "box.sie").write_text("""
        struct Box<T> { value: T; }

        fn Box<T>::init(&self, value: T) { self.value = value; }
        fn Box<T>::get(&self) -> T { return self.value; }
    """)
    (tmp_path / "part.sie").write_text("""
        import { Box } from box;

        fn part() -> i32 { let b = Box<i32>(20); return b.get(); }
    """)
    (tmp_path / "main.sie").write_text("""
        import { Box } from box;

        fn part() -> i32;

        fn main() -> i32 { let b = Box<i32>(22); return b.get() + part(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, tmp_path / "part.sie", "-c", "-o", "part.o") == 0
    assert run_cli(monkeypatch, tmp_path / "main.sie", "-c", "-o", "main.o") == 0

    subprocess.run(["cc", "part.o", "main.o", "-o", "app"], check=True)
    assert subprocess.run(["./app"]).returncode == 42


def test_modules_keep_their_own_constants(tmp_path, monkeypatch):
    """
    Two modules may each declare the same '@const' name - stdio and
    unistd both keep a SEEK_SET - resolved by each user's view: its own
    module's qualified, a member import's unqualified.
    """
    (tmp_path / "stdio.sie").write_text("@const SEEK_SET = 10;\n")
    (tmp_path / "unistd.sie").write_text("""
        @const SEEK_SET = 20;

        fn own() -> i32 {
            return SEEK_SET;    // its own file's, 20
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import stdio;
        import unistd;
        import { SEEK_SET } from stdio;
        import { own } from unistd;

        fn main() -> i32 {
            if (stdio.SEEK_SET != 10) { return 1; }
            if (unistd.SEEK_SET != 20) { return 2; }
            if (SEEK_SET != 10) { return 3; }     // the member import's
            if (own() != 20) { return 4; }
            return 0;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_macro_names_resolve_in_their_own_module(tmp_path, monkeypatch):
    """
    An imported macro's expansion resolves its names where the macro was
    written: 'errno'-style, the location function stays the module's
    private business.
    """
    (tmp_path / "err.sie").write_text("""
        @static let slot: i32 = 0;

        fn location() -> i32* {
            return &slot;
        }

        @macro errno = *location();

        fn set_errno(value: i32) {
            *location() = value;
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { errno, set_errno } from err;

        fn main() -> i32 {
            set_errno(42);
            let seen = errno;      // bare object-like use, inferred i32
            return seen - 42;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_nested_generic_macro_type_arguments_keep_the_outer_view(
        tmp_path, monkeypatch):
    """
    A type argument written in one macro resolves there even when its
    expansion invokes a generic macro declared in another module.
    """
    (tmp_path / "generic.sie").write_text("""
        @macro convert<T>(value) = value as T;
        @macro pointer<T>(value) = value as T*;
    """)
    (tmp_path / "wrapper.sie").write_text("""
        import { convert, pointer } from generic;

        @type Private = i64;
        struct Hidden;
        @macro widen(value) = convert<Private>(value);
        @macro hidden_pointer(value) = pointer<Hidden>(value);
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { hidden_pointer, widen } from wrapper;

        struct Local;

        fn main() -> i32 {
            let value: i64 = widen(42);
            let local: Local* = null;
            hidden_pointer(local);
            return (value - 42) as i32;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_conformance_checks_in_the_interface_view(tmp_path, monkeypatch):
    """
    Conformance expands an action's types without the user's view in the
    way: an interface behind 'std.io'-style includes may speak an enum
    the entry file never imports.
    """
    std = tmp_path / "std"
    std.mkdir()
    (std / "_ifaces.sie").write_text("""
        enum IOError { SystemError, NotOpen, }

        interface Reader {
            fn read(&self, buf: &u8[]) -> Result<i64, IOError>;
        }
    """)
    (std / "io.sie").write_text('@include("_ifaces");\n')
    (std / "fs.sie").write_text("""
        import { Reader, IOError } from std.io;

        struct File: Reader { fill: u8; }

        fn File::read(&self, buf: &u8[]) -> Result<i64, IOError> {
            buf[0] = self.fill;
            return Ok(1 as i64);
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { File } from std.fs;

        // registers after everything else, leaving this file's view (no
        // IOError in it) as the one conformance would wrongly gate by
        struct Wrapper { f: File; }

        fn main() -> i32 {
            let w: Wrapper;
            w.f.fill = 9;

            let buf: u8[2];
            let r = w.f.read(buf);
            if (not r.ok) { return 1; }
            return buf[0] as i32 - 9;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_symbol_bindings_stay_in_their_module(tmp_path, monkeypatch):
    """
    A qualified member maps through '@symbol' only when its own module
    declared it: libc-style 'stderr' in one module must not hijack
    another module's 'stderr'.
    """
    (tmp_path / "libc.sie").write_text("""
        @extern @symbol("write") fn c_write(fd: i32, buf: const char*,
                                            count: u64) -> i64;
        @static let __fd: i32 = 2;
        @macro stderr = __fd;
    """)
    (tmp_path / "streams.sie").write_text("""
        @static let __stderr: i64 = 7;
        @macro stderr = __stderr;
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import streams;

        fn main() -> i32 {
            let v = streams.stderr;    // streams' own, not libc's
            return (v - 7) as i32;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 0


def test_imported_names_are_not_reexported(tmp_path, monkeypatch, capsys):
    """
    A module offers only its own declarations: what it imports stays the
    imported module's, unreachable through the middleman qualified.
    """
    (tmp_path / "a.sie").write_text("""
        struct S { x: i32; }
        fn func() -> i32 { return 1; }
    """)
    (tmp_path / "b.sie").write_text("import a;\n")
    src = tmp_path / "c.sie"
    src.write_text("""
        import b;

        fn main() -> i32 { return b.func(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "module 'b' has no member 'func'" in capsys.readouterr().err


def test_imported_types_are_not_reexported(tmp_path, monkeypatch, capsys):
    """
    Types follow the same rule: an imported struct does not reach through
    the importing module's name.
    """
    (tmp_path / "a.sie").write_text("struct S { x: i32; }\n")
    (tmp_path / "b.sie").write_text("import a;\n")
    src = tmp_path / "c.sie"
    src.write_text("""
        import b;

        fn main() -> i32 {
            let var: b.S;
            return var.x;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "module 'b' has no member 'S'" in capsys.readouterr().err


def test_member_imports_do_not_reach_through_imports(tmp_path, monkeypatch, capsys):
    """
    'import { f } from b;' fails when b only imports f's module itself.
    """
    (tmp_path / "a.sie").write_text("fn func() -> i32 { return 1; }\n")
    (tmp_path / "b.sie").write_text("import a;\n")
    src = tmp_path / "c.sie"
    src.write_text("""
        import { func } from b;

        fn main() -> i32 { return func(); }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert "module 'b' has no member 'func'" in capsys.readouterr().err


def test_included_names_reexport_through_imports(tmp_path, monkeypatch):
    """
    '@include' is textual, so an including module re-offers what it
    pulled in: the include is the way to compose a module's surface.
    """
    (tmp_path / "a.sie").write_text("""
        struct S { x: i32; }
        fn func() -> i32 { return 40; }
    """)
    (tmp_path / "b.sie").write_text('@include("a")\n')
    src = tmp_path / "c.sie"
    src.write_text("""
        import b;

        fn main() -> i32 {
            let var: b.S;
            var.x = 2;
            return b.func() + var.x;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_module_constants_build_on_their_siblings(tmp_path, monkeypatch):
    """
    A constant's value resolves in its declaring file's view: a module's
    constant may reference its siblings wherever it is used.
    """
    (tmp_path / "flags.sie").write_text("""
        @const A = 1 << 1;
        @const B = 1 << 3;
        @const ALL = A | B;
        @const DEFAULT = ALL;
    """)
    src = tmp_path / "main.sie"
    src.write_text("""
        import { DEFAULT } from flags;

        fn main() -> i32 {
            return DEFAULT * 4 + 2;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_a_template_return_type_is_not_gated_by_the_callers_view(tmp_path, monkeypatch):
    """
    Inferring a generic call's return type expands the template's own
    spelling - a struct from the template's module - which the calling
    file need not see to hold the value.
    """
    mod = tmp_path / "lib"
    mod.mkdir()
    (mod / "boxes.sie").write_text("""
        struct Box<T> {
            v: T;
        }

        fn wrap(v: const &Iterable<char>) -> Box<u64> {
            let n: u64 = 0;
            foreach (c : v) {
                n += 1;
            }
            let b: Box<u64> = { n };
            return b;
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import { wrap } from lib.boxes;

        fn main() -> i32 {
            let b = wrap("hello");
            return (b.v as i32) + 37;
        }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 42


def test_error_directive_blames_its_own_module(tmp_path, monkeypatch, capsys):
    """
    An '@error' inside an imported module names that module and its line,
    the way any other compile error does.
    """
    (tmp_path / "refuser.sie").write_text("""
        @if (true) {
            @error("this platform has no binding")
        }
    """)

    src = tmp_path / "main.sie"
    src.write_text("""
        import refuser;

        fn main() -> i32 { return 0; }
    """)

    monkeypatch.chdir(tmp_path)
    assert run_cli(monkeypatch, src, "--run") == 1
    assert ("refuser.sie at line 3: this platform has no binding"
            in capsys.readouterr().err)
