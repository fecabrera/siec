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


def test_conditional_include_is_an_error(compile_source):
    """
    '@include' joins the program before conditions evaluate, so it cannot
    sit inside an '@if'.
    """
    with pytest.raises(SyntaxError, match="'@include' cannot be conditional"):
        compile_source("""
            @if (true) {
                @include("libc/stdio")
            }
        """)
