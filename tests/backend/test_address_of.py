"""Feature tests for the '&' address-of operator."""

import pytest


def test_address_of_a_variable(run):
    """
    '&x' yields a pointer through which the variable can be written.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 1;
        let p: i32* = &x;
        p[0] = 42;
        return x;
    }
    """
    assert run(source).returncode == 42


def test_address_passes_to_a_function(run):
    """
    A callee writing through '&x' mutates the caller's variable.
    """
    source = """
    fn bump(p: i32*) {
        p[0] += 1;
    }

    fn main() -> i32 {
        let x: i32 = 41;
        bump(&x);
        return x;
    }
    """
    assert run(source).returncode == 42


def test_address_of_a_struct_field(run):
    """
    '&s.field' points at the field inside the struct's own storage.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let pt: Point = {10, 20};
        let p: i32* = &pt.y;
        p[0] = 32;
        return pt.x + pt.y;
    }
    """
    assert run(source).returncode == 42


def test_address_of_an_array_element(run):
    """
    '&arr[i]' points at the element inside the array's backing data.
    """
    source = """
    fn main() -> i32 {
        let arr: i32[] = [1, 2, 3];
        let p: i32* = &arr[1];
        p[0] = 40;
        return arr[0] + arr[1] + arr[2] as i32;
    }
    """
    assert run(source).returncode == 44


def test_prefix_address_of_beside_bitwise_and(run):
    """
    Infix '&' stays the bitwise mask when its right side takes an address.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 6;
        let p: i32* = &x;
        return x & p[0] + 36; // 6 & 42
    }
    """
    assert run(source).returncode == 2


def test_address_of_a_literal_is_an_error(compile_source):
    """
    '&' demands an assignable operand; a literal has no address.
    """
    with pytest.raises(TypeError, match="not assignable"):
        compile_source("fn main() -> i32 { let p: i32* = &5; return 0; }")
