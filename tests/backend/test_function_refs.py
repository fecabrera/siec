"""Feature tests for function references: fn types, assignment, and indirect calls."""

import pytest


def test_reference_stored_and_called(run):
    """
    A function's name stores into a fn-typed variable and calls through it.
    """
    source = """
    fn double(x: i32) -> i32 {
        return x * 2;
    }

    fn main() -> i32 {
        let fp: fn(i32) -> i32 = double;
        return fp(21);
    }
    """
    assert run(source).returncode == 42


def test_reference_reassigned(run):
    """
    A fn-typed variable can be repointed at another function of the same signature.
    """
    source = """
    fn double(x: i32) -> i32 {
        return x * 2;
    }

    fn triple(x: i32) -> i32 {
        return x * 3;
    }

    fn main() -> i32 {
        let fp: fn(i32) -> i32 = double;
        let a: i32 = fp(10); // 20
        fp = triple;
        return a + fp(10);   // 20 + 30
    }
    """
    assert run(source).returncode == 50


def test_reference_as_a_parameter(run):
    """
    A function receives a fn-typed parameter and calls through it.
    """
    source = """
    fn double(x: i32) -> i32 {
        return x * 2;
    }

    fn apply(f: fn(i32) -> i32, x: i32) -> i32 {
        return f(x);
    }

    fn main() -> i32 {
        return apply(double, 21);
    }
    """
    assert run(source).returncode == 42


def test_void_reference(run):
    """
    A 'fn()' reference calls a void function for its effects.
    """
    source = """
    @extern fn putchar(ch: i32) -> i32;

    fn shout() {
        putchar(33); // '!'
    }

    fn main() -> i32 {
        let fp: fn() = shout;
        fp();
        return 0;
    }
    """
    result = run(source)
    assert result.returncode == 0
    assert result.stdout == "!"


def test_indirect_call_return_feeds_signedness(run):
    """
    The reference's return type participates in inference, so a u32 result
    combines with unsigned values.
    """
    source = """
    fn big() -> u32 {
        return 200;
    }

    fn main() -> i32 {
        let fp: fn() -> u32 = big;
        let u: u32 = 100;
        if (fp() + u == 300) {
            return 1;
        }
        return 0;
    }
    """
    assert run(source).returncode == 1


def test_wrong_signature_is_rejected(compile_source):
    """
    Assigning a function of a different signature to a fn-typed variable is an error.
    """
    source = """
    fn f() -> i32 { return 1; }

    fn main() -> i32 {
        let fp: fn(i32) -> i32 = f;
        return 0;
    }
    """
    with pytest.raises(TypeError, match="cannot implicitly convert"):
        compile_source(source)


def test_wrong_arity_through_a_reference_is_rejected(compile_source):
    """
    Calling a reference with the wrong number of arguments is an error.
    """
    source = """
    fn f(x: i32) -> i32 { return x; }

    fn main() -> i32 {
        let fp: fn(i32) -> i32 = f;
        return fp(1, 2);
    }
    """
    with pytest.raises(TypeError, match="takes 1 arguments"):
        compile_source(source)


def test_calling_a_non_function_variable_is_rejected(compile_source):
    """
    Calling a variable that doesn't hold a function reference is an error.
    """
    source = """
    fn main() -> i32 {
        let n: i32 = 5;
        return n();
    }
    """
    with pytest.raises(TypeError, match="cannot call non-function variable"):
        compile_source(source)
