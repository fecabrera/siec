"""Feature tests for block expressions: blocks producing values through 'emit'."""

import pytest


def test_block_initializes_a_variable(run):
    """
    A block's emitted value initializes the let, its locals staying inside.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = {
            let x: i32 = 40;
            emit x + 2;
        };
        return a;
    }
    """
    assert run(source).returncode == 42


def test_emit_ends_the_block_early(run):
    """
    An 'emit' inside an arm ends the block there, like a return ends a function.
    """
    source = """
    fn main() -> i32 {
        let b: i32 = {
            if (1) emit 1;
            emit 2;
        };
        return b;
    }
    """
    assert run(source).returncode == 1


def test_emitted_value_widens_to_the_target(run):
    """
    The emitted value coerces to the block's context type.
    """
    source = """
    fn main() -> i32 {
        let wide: i64 = { emit 7; };
        return wide as i32;
    }
    """
    assert run(source).returncode == 7


def test_block_expressions_nest(run):
    """
    A block expression may initialize a variable inside another one.
    """
    source = """
    fn main() -> i32 {
        let n: i32 = {
            let inner: i32 = { emit 10; };
            emit inner * 2;
        };
        return n;
    }
    """
    assert run(source).returncode == 20


def test_block_without_an_emit_is_an_error(compile_source):
    """
    A block used as a value that never emits is rejected.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = { let x: i32 = 1; };
        return a;
    }
    """
    with pytest.raises(TypeError, match="must produce its value with 'emit'"):
        compile_source(source)


def test_emit_outside_a_block_expression_is_an_error(compile_source):
    """
    An 'emit' with no enclosing block expression is rejected.
    """
    source = """
    fn main() -> i32 {
        emit 5;
        return 0;
    }
    """
    with pytest.raises(TypeError, match="'emit' outside a block expression"):
        compile_source(source)
