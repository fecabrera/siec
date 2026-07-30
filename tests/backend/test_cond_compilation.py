"""Feature tests for '@if' conditional compilation."""

import pytest

from siec.codegen import codegen
from siec.lexer import lex
from siec.parser import parse


def test_constant_comparison_picks_the_branch(run):
    """
    '@if (A == B)' compiles the matching branch's declarations.
    """
    result = run("""
        @const A = 1;
        @const B = 1;

        @if (A == B) {
            fn pick() -> i32 { return 42; }
        } @else {
            fn pick() -> i32 { return 1; }
        }

        fn main() -> i32 { return pick(); }
    """)
    assert result.returncode == 42


def test_false_condition_takes_the_else(run):
    """
    A false condition compiles the '@else' branch instead.
    """
    result = run("""
        @if (1 == 2) {
            @const WHERE = 1;
        } @else {
            @const WHERE = 2;
        }

        fn main() -> i32 { return WHERE; }
    """)
    assert result.returncode == 2


def test_else_if_chains(run):
    """
    '@else @if' chains conditions, first match winning.
    """
    result = run("""
        @const N = 2;

        @if (N == 1) {
            @const R = 10;
        } @else @if (N == 2) {
            @const R = 42;
        } @else {
            @const R = 30;
        }

        fn main() -> i32 { return R; }
    """)
    assert result.returncode == 42


def test_branches_hold_any_declaration(run):
    """
    A chosen branch's aliases, structs, globals, constants, and nested
    '@if's all join the program.
    """
    result = run("""
        @if (TARGET_OS != 255) {
            @type word = u64;
            struct info { x: word; }
            @static let base: word = 30;

            @if (1 == 1) {
                @const EXTRA = 12;
            } @else {
                @const EXTRA = 5;
            }
        }

        fn main() -> i32 {
            let i: info = { x = base };
            return (i.x as i32) + EXTRA;
        }
    """)
    assert result.returncode == 42


def test_the_unchosen_branch_never_compiles(run):
    """
    The rejected branch's declarations are skipped entirely: duplicate
    names and undefined calls in it cost nothing.
    """
    result = run("""
        @if (true) {
            fn f() -> i32 { return 42; }
        } @else {
            fn f() -> i32 { return not_even_defined(); }
        }

        fn main() -> i32 { return f(); }
    """)
    assert result.returncode == 42


def test_conditions_follow_the_target():
    """
    Target constants inside '@if' see the compilation target's values.
    """
    module = codegen(parse(lex("""
        @if (TARGET_OS == OS_LINUX) {
            @const WHERE = 1;
        } @else {
            @const WHERE = 2;
        }

        fn where() -> i32 { return WHERE; }
    """)), "m", target="x86_64-unknown-linux-gnu")

    assert "ret i32 1" in str(module)


def test_sizeof_condition_sees_a_later_struct(run):
    """
    A type-dependent condition waits until the active struct inventory and
    layouts are resolved, regardless of declaration order.
    """
    result = run("""
        @if (@sizeof(Header) == 4) {
            @const ANSWER = 42;
        } @else {
            @const ANSWER = 1;
        }

        struct Header {
            value: i32;
        }

        fn main() -> i32 { return ANSWER; }
    """)
    assert result.returncode == 42


def test_enum_condition_sees_a_later_enum(run):
    """
    Enum members and type IDs in conditions resolve from the complete active
    inventory, not from the declarations visited so far.
    """
    result = run("""
        @if (Mode::Enabled == 42 and @typeid(Mode) != 0) {
            @const ANSWER = 42;
        } @else {
            @const ANSWER = 1;
        }

        enum Mode {
            Enabled = 42,
        }

        fn main() -> i32 { return ANSWER; }
    """)
    assert result.returncode == 42


def test_constant_condition_waits_for_its_type_dependencies(run):
    """
    Deferral follows '@const' references, so hiding '@sizeof' behind a
    constant does not restore declaration-order dependence.
    """
    result = run("""
        @const HEADER_FITS = @sizeof(Header) == 4;

        @if (HEADER_FITS) {
            @const ANSWER = 42;
        } @else {
            @const ANSWER = 1;
        }

        struct Header {
            value: i32;
        }

        fn main() -> i32 { return ANSWER; }
    """)
    assert result.returncode == 42


def test_nested_condition_sees_types_selected_by_its_parent(run):
    """
    A selected semantic branch registers its declarations before resolving a
    nested condition, so the nested condition may inspect the selected type.
    """
    result = run("""
        struct Trigger {
            value: i32;
        }

        @if (@sizeof(Trigger) == 4) {
            struct Selected {
                value: i64;
            }

            @if (@sizeof(Selected) == 8) {
                @const ANSWER = 42;
            } @else {
                @const ANSWER = 1;
            }
        }

        fn main() -> i32 { return ANSWER; }
    """)
    assert result.returncode == 42


def test_condition_cannot_depend_on_the_type_it_selects(compile_source):
    """
    A declaration cannot supply the type information that decides whether the
    declaration exists; the unavailable type is diagnosed at the condition.
    """
    with pytest.raises(TypeError, match="unknown type 'Selected'"):
        compile_source("""
        @if (@sizeof(Selected) == 4) {
            struct Selected {
                value: i32;
            }
        }
        """)


def test_non_constant_condition_is_an_error(compile_source):
    """
    The condition must evaluate at compile time.
    """
    with pytest.raises(TypeError, match="not a compile-time constant"):
        compile_source("""
            @if (some_var == 1) {
                fn f() -> i32 { return 1; }
            }
        """)


def test_conditional_include_parses():
    """
    An '@include' may sit inside an '@if': the loader evaluates the
    condition and loads only the chosen arm's files (tests/loader).
    """
    from siec.lexer import lex
    from siec.parser import parse

    program = parse(lex("""
        @if (true) {
            @include("libc/stdio");
        }
    """))
    assert program.conds[0].then.includes[0].path == "libc/stdio"


def test_error_directive_stops_the_chosen_branch(compile_source):
    """
    An '@error' the compilation reaches stops it with the message it
    carries, the way a platform that has no binding refuses to build.
    """
    source = """
    @if (TARGET_OS == OS_WINDOWS) {
        @error("Unsupported OS")
    }

    fn main() -> i32 { return 0; }
    """
    with pytest.raises(TypeError, match="Unsupported OS"):
        compile_source(source, target="x86_64-pc-windows-msvc")


def test_error_directive_stays_quiet_in_an_unchosen_branch(compile_source):
    """
    An unchosen branch is never resolved, so the '@error' inside it is
    never reached: this is what makes the '@else' arm work.
    """
    source = """
    @if (TARGET_OS == OS_DARWIN) {
        fn platform() -> i32 { return 1; }
    } @else @if (TARGET_OS == OS_LINUX) {
        fn platform() -> i32 { return 1; }
    } @else {
        @error("Unsupported OS")
    }

    fn main() -> i32 { return platform() - 1; }
    """
    compile_source(source, target="arm64-apple-darwin")
    compile_source(source, target="x86_64-unknown-linux-gnu")


def test_error_directive_reaches_through_nesting(compile_source):
    """
    A nested '@if' inside a chosen branch reaches its own '@error'.
    """
    with pytest.raises(TypeError, match="nested reach"):
        compile_source("""
        @const DEPTH = 2;

        @if (DEPTH > 1) {
            @if (DEPTH == 2) {
                @error("nested reach")
            }
        }

        fn main() -> i32 { return 0; }
        """)


def test_error_directive_at_the_top_level_always_fires(compile_source):
    """
    Outside any '@if' there is nothing to gate it: the file cannot build.
    """
    with pytest.raises(TypeError, match="this file is not ready"):
        compile_source("""
        @error("this file is not ready");

        fn main() -> i32 { return 0; }
        """)


def test_static_assert_holds_or_stops(compile_source):
    """
    '@static_assert(cond, "...")' requires the condition: it passes
    silently when it holds, and stops the compilation with its message
    when it does not.
    """
    source = """
    @const WIDTH = 8;

    @static_assert(WIDTH == 8, "WIDTH must be eight");
    @static_assert(@sizeof(u64) == WIDTH, "u64 must be WIDTH bytes")

    fn main() -> i32 { return 0; }
    """
    compile_source(source)

    with pytest.raises(TypeError, match="static assertion failed: "
                                        "WIDTH must be eight"):
        compile_source("""
        @const WIDTH = 4;
        @static_assert(WIDTH == 8, "WIDTH must be eight");

        fn main() -> i32 { return 0; }
        """)


def test_static_assert_weighs_the_whole_program(compile_source):
    """
    An assert gates no declaration, so it is checked once everything is
    registered: a struct's layout and an enum's members are in reach,
    whatever order they were declared in.
    """
    source = """
    @static_assert(@sizeof(Header) == 16, "Header must stay two words");
    @static_assert(Mode::Both == 3, "Both must follow Read and Write");

    struct Header { a: u64; b: u64; }
    enum Mode { Read, Write, Both }

    fn main() -> i32 { return 0; }
    """
    compile_source(source)

    with pytest.raises(TypeError, match="Header must stay one word"):
        compile_source("""
        struct Header { a: u64; b: u64; }
        @static_assert(@sizeof(Header) == 8, "Header must stay one word");

        fn main() -> i32 { return 0; }
        """)


def test_static_assert_follows_the_chosen_branch(compile_source):
    """
    An assert inside an '@if' is checked only when its branch is chosen,
    like every other declaration in it.
    """
    source = """
    @if (TARGET_OS == OS_DARWIN) {
        @static_assert(true, "the darwin arm checks its own");
    } @else {
        @static_assert(false, "not on darwin");
    }

    fn main() -> i32 { return 0; }
    """
    compile_source(source, target="arm64-apple-darwin")

    with pytest.raises(TypeError, match="static assertion failed: not on darwin"):
        compile_source(source, target="x86_64-unknown-linux-gnu")
