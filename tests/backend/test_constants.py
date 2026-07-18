"""Feature tests for '@const' compile-time constants."""

import pytest


def test_constants_substitute_at_their_uses(run):
    """
    Annotated and inferred constants substitute their values in place.
    """
    source = """
    @const SIZE: u64 = 40;
    @const EXTRA = 2;

    fn main() -> i32 {
        return (SIZE as i32) + EXTRA; // 42
    }
    """
    assert run(source).returncode == 42


def test_constants_reference_constants(run):
    """
    A constant's value may fold other constants, regardless of order.
    """
    source = """
    @const TAU: f64 = PI * 2.0;
    @const PI = 3.14159;

    fn main() -> i32 {
        return TAU as i32; // 6
    }
    """
    assert run(source).returncode == 6


def test_inferred_constant_adapts_to_its_context(run):
    """
    An unannotated constant adapts to each use like a literal written in place.
    """
    source = """
    @const N = 5;

    fn main() -> i32 {
        let small: i32 = N;
        let big: u64 = N;
        return small + big as i32; // 10
    }
    """
    assert run(source).returncode == 10


def test_local_shadows_a_constant(run):
    """
    A let with the same name shadows the constant inside its scope.
    """
    source = """
    @const X = 40;

    fn main() -> i32 {
        let X: i32 = 2;
        return X + 40; // the local X
    }
    """
    assert run(source).returncode == 42


def test_constant_cannot_be_reassigned(compile_source):
    """
    Assigning to a constant is an error.
    """
    source = """
    @const X: i32 = 5;
    fn main() -> i32 { X = 6; return 0; }
    """
    with pytest.raises(TypeError, match="cannot reassign constant 'X'"):
        compile_source(source)


def test_constant_value_must_be_constant(compile_source):
    """
    A call in a constant's value is rejected.
    """
    source = """
    fn f() -> i32 { return 1; }
    @const X: i32 = f();
    fn main() -> i32 { return X; }
    """
    with pytest.raises(TypeError, match="must be a constant expression"):
        compile_source(source)


def test_constant_cycle_is_an_error(compile_source):
    """
    Constants referencing each other in a cycle are rejected.
    """
    source = """
    @const A = B;
    @const B = A;
    fn main() -> i32 { return 0; }
    """
    with pytest.raises(TypeError, match="constant cycle: A -> B -> A"):
        compile_source(source)


def test_duplicate_constant_is_an_error(compile_source):
    """
    Declaring the same constant twice is rejected.
    """
    source = """
    @const X = 1;
    @const X = 2;
    fn main() -> i32 { return 0; }
    """
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source(source)
