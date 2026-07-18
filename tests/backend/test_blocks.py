"""Feature tests for block statements: brace-enclosed scopes."""

import pytest


def test_block_scopes_its_declarations(run):
    """
    A block's declarations shadow and end with it; outer writes persist.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 1;

        {
            let b: i32 = 41;
            a = a + b;        // writes through to the outer a

            let a: i32 = 100; // shadows the outer a until the block ends
            b = a - a;
        }

        return a; // 42: the outer a, with the write kept
    }
    """
    assert run(source).returncode == 42


def test_block_declaration_is_gone_after_the_block(compile_source):
    """
    Using a block-local variable after the block is an error.
    """
    source = """
    fn main() -> i32 {
        {
            let b: i32 = 1;
        }
        return b;
    }
    """
    with pytest.raises(NameError, match="undefined variable 'b'"):
        compile_source(source)


def test_if_arms_scope_their_declarations(run):
    """
    Each if/else arm is its own scope: declarations end with the arm,
    while writes to outer variables persist.
    """
    source = """
    fn main() -> i32 {
        let a: i32 = 1;

        if (a == 1) {
            let b: i32 = 41;
            a = a + b;
            let a: i32 = 100; // shadows only inside this arm
        } else {
            let c: i32 = 7;
            a = c;
        }

        return a; // 42
    }
    """
    assert run(source).returncode == 42


def test_if_arm_declaration_is_gone_after_the_if(compile_source):
    """
    Using an arm-local variable after the if is an error.
    """
    source = """
    fn main() -> i32 {
        if (1) {
            let b: i32 = 1;
        }
        return b;
    }
    """
    with pytest.raises(NameError, match="undefined variable 'b'"):
        compile_source(source)


def test_return_inside_a_block_leaves_the_function(run):
    """
    A return inside a block terminates the function, skipping what follows.
    """
    source = """
    fn main() -> i32 {
        {
            return 5;
        }
        return 9;
    }
    """
    assert run(source).returncode == 5
