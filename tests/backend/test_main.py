"""Entry-point source and native ABI validation."""

import pytest


@pytest.mark.parametrize("source", [
    "fn main() {}",
    "fn main() -> i32 { return 0; }",
    "fn main(argc: i32, argv: char**) {}",
    "fn main(argc: const i32, argv: const char**) -> const i32 { return 0; }",
    "fn main(args: char*[]) {}",
    "fn main(args: const char*[]) -> i32 { return args.length as i32; }",
    "@type Exit = i32; @type Args = char*[]; "
    "fn main(args: Args) -> Exit { return args.length as Exit; }",
])
def test_documented_main_signatures_compile(compile_source, source):
    """Every documented form, including const and aliases, has a known ABI."""
    compile_source(source)


@pytest.mark.parametrize("source", [
    "fn main(value: f64) -> i32 { return 0; }",
    "fn main(value: i32) -> i32 { return value; }",
    "fn main(argc: i32, argv: u8**) -> i32 { return argc; }",
    "fn main(a: i32, b: char**, c: i32) -> i32 { return a + c; }",
    "fn main() -> f64 { return 0.0; }",
    "fn main(...) -> i32 { return 0; }",
    "fn main(args...) -> i32 { return 0; }",
    "fn main(argc: i32 = 0, argv: char** = null) -> i32 { return argc; }",
])
def test_invalid_main_signatures_are_rejected(compile_source, source):
    """Arity, types, varargs, and defaults cannot change the runtime ABI."""
    with pytest.raises(TypeError, match="'main' must have one of these signatures"):
        compile_source(source)


@pytest.mark.parametrize(("source", "message"), [
    ("@extern fn main() -> i32;", "cannot be '@extern'"),
    ("@inline fn main() -> i32 { return 0; }", "cannot be '@inline'"),
    ("@private fn main() -> i32 { return 0; }", "cannot be '@private'"),
    ("@override fn main() -> i32 { return 0; }", "cannot be '@override'"),
    ("@remove(\"use app\") fn main() -> i32;", "cannot be '@remove'"),
])
def test_main_rejects_incompatible_decorators(
        compile_source, source, message):
    """Decorators cannot hide, replace, discard, or alter entry linkage."""
    with pytest.raises(TypeError, match=message):
        compile_source(source)


def test_main_forward_declaration_may_precede_definition(run):
    """A valid declaration and later definition still share the entry symbol."""
    result = run("""
        fn main(argc: i32, argv: char**) -> i32;
        fn main(argc: i32, argv: char**) -> i32 { return argc; }
    """, "argument")
    assert result.returncode == 2


def test_method_named_main_is_not_an_entry_point(compile_source):
    """Only the free C symbol is constrained; an ordinary method keeps its API."""
    compile_source("""
        struct Number { value: i32; }
        fn Number::main(value: f64) -> f64 { return value; }
        fn main() -> i32 { return 0; }
    """)
