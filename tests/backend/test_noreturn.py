"""Feature tests for the '@noreturn' decorator."""

import pytest

EXIT = """
@extern @noreturn fn exit(code: i32);
"""


def test_noreturn_call_satisfies_a_required_return(run):
    """
    A path ending in an '@noreturn' call needs no return of its own.
    """
    source = EXIT + """
    fn quit(code: i32) -> i32 {
        exit(code);
    }

    fn main() -> i32 {
        return quit(42);
    }
    """
    assert run(source).returncode == 42


def test_without_noreturn_the_return_is_still_required(compile_source):
    """
    Only an '@noreturn' callee ends a path; a plain call leaves the
    missing-return error in place.
    """
    with pytest.raises(TypeError, match="must return a value"):
        compile_source("""
        @extern fn exit(code: i32);

        fn quit(code: i32) -> i32 {
            exit(code);
        }

        fn main() -> i32 {
            return quit(1);
        }
        """)


def test_noreturn_body_ends_through_another_noreturn_call(run):
    """
    A '@noreturn' function's own body leaves through a noreturn call.
    """
    source = EXIT + """
    @noreturn fn die(code: i32) {
        exit(code);
    }

    fn main() -> i32 {
        die(42);
    }
    """
    assert run(source).returncode == 42


def test_noreturn_call_ends_one_branch(run):
    """
    A branch ending in an '@noreturn' call needs no return; the others
    still run normally.
    """
    source = EXIT + """
    @noreturn fn die(code: i32) {
        exit(code);
    }

    fn check(x: i32) -> i32 {
        if (x == 0) {
            die(7);
        }
        return x;
    }

    fn main() -> i32 {
        return check(42);
    }
    """
    assert run(source).returncode == 42


def test_statements_after_a_noreturn_call_are_dead(run):
    """
    Nothing after an '@noreturn' call on the same path runs.
    """
    source = EXIT + """
    fn main() -> i32 {
        exit(42);
        return 1;
    }
    """
    assert run(source).returncode == 42


def test_noreturn_body_may_loop_forever(run):
    """
    A '@noreturn' body that loops forever falls off no end and needs no
    closing call.
    """
    source = """
    @noreturn fn spin() {
        while (true) {}
    }

    fn main() -> i32 {
        return 42;
    }
    """
    assert run(source).returncode == 42


def test_noreturn_declaration_carries_the_llvm_attribute(compile_source):
    """
    The declaration is marked 'noreturn' and calls end in 'unreachable',
    so LLVM may optimize on the promise.
    """
    module = str(compile_source(EXIT + """
    fn main() -> i32 {
        exit(3);
    }
    """))
    assert "noreturn" in module
    assert "unreachable" in module


def test_noreturn_cannot_declare_a_return_type(compile_source):
    """
    '@noreturn' hands nothing back: a return type is a contradiction.
    """
    with pytest.raises(SyntaxError, match="cannot declare a return type"):
        compile_source("@noreturn fn f() -> i32;")


def test_noreturn_function_cannot_return(compile_source):
    """
    A 'return' inside an '@noreturn' body breaks its promise.
    """
    with pytest.raises(TypeError, match="'@noreturn' function 'die' cannot return"):
        compile_source(EXIT + """
        @noreturn fn die() {
            return;
        }

        fn main() -> i32 { return 0; }
        """)


def test_extern_still_rejects_body_decorators(compile_source):
    """
    '@extern' combines with '@noreturn' alone: the others need a body.
    """
    with pytest.raises(SyntaxError, match="'@extern' only combines"):
        compile_source("@extern @inline fn f();")
