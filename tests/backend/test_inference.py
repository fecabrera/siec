"""Feature tests for 'let' type inference."""

import pytest


def test_infers_from_a_call(run):
    """
    'let a = f();' adopts f's return type.
    """
    source = """
    fn answer() -> i32 {
        return 42;
    }

    fn main() -> i32 {
        let a = answer();
        return a;
    }
    """
    assert run(source).returncode == 42


def test_infers_a_struct_from_a_call(run):
    """
    A call returning a struct infers the struct type, fields and all.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn make() -> Point {
        return {40, 2};
    }

    fn main() -> i32 {
        let pt = make();
        return pt.x + pt.y;
    }
    """
    assert run(source).returncode == 42


def test_infers_from_a_variable(run):
    """
    'let b = a;' copies a's declared type.
    """
    source = """
    fn main() -> i32 {
        let a: u8 = 42;
        let b = a;
        return b as i32;
    }
    """
    assert run(source).returncode == 42


def test_literals_take_their_defaults(run):
    """
    Unannotated literals default like any untyped context: i32 and f64.
    """
    source = """
    fn main() -> i32 {
        let x = 40;
        let f = 2.5;
        return x + (f - 0.5) as i32;
    }
    """
    assert run(source).returncode == 42


def test_infers_from_a_cast(run):
    """
    'let z = y as u8;' adopts the cast's target type.
    """
    source = """
    fn main() -> i32 {
        let z = 300 as u8; // wraps to 44
        return z as i32 - 2;
    }
    """
    assert run(source).returncode == 42


def test_infers_members_and_elements(run):
    """
    'arr.length' infers u64 and 'arr[i]' the element type.
    """
    source = """
    fn main() -> i32 {
        let arr: i32[] = [39, 2, 3];
        let n = arr.length;
        let first = arr[0];
        return first + n as i32;
    }
    """
    assert run(source).returncode == 42


def test_infers_a_pointer_from_address_of(run):
    """
    'let p = &x;' infers x's type plus a '*'.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 1;
        let p = &x;
        p[0] = 42;
        return x;
    }
    """
    assert run(source).returncode == 42


def test_infers_a_bool_from_a_comparison(run):
    """
    Comparisons infer bool, usable anywhere a bool is.
    """
    source = """
    fn main() -> i32 {
        let b = 1 < 2;
        if (b) {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_a_literal_adapts_to_a_declared_operand(run):
    """
    In 'x + 1' the literal adapts to x's type, which the let adopts.
    """
    source = """
    fn main() -> i32 {
        let x: u64 = 41;
        let y = x + 1;    // u64, not the literal's default
        return y as i32;
    }
    """
    assert run(source).returncode == 42


def test_inference_in_a_for_init(run):
    """
    A for loop's init can leave its counter's type to inference.
    """
    source = """
    fn main() -> i32 {
        let total: i32 = 0;
        for (let i = 0; i < 4; i += 1) {
            total += i;
        }
        return total; // 0 + 1 + 2 + 3
    }
    """
    assert run(source).returncode == 6


def test_array_literal_initializer_infers_its_array_type(run):
    """
    'let a = [1, 2, 3];' declares the 'i32[]' its elements infer.
    """
    source = """
    fn main() -> i32 {
        let a = [40, 2];
        return (a[0] + a[1]) * (a.length as i32) / 2;
    }
    """
    assert run(source).returncode == 42


def test_unfixed_initializer_is_an_error(compile_source):
    """
    An initializer with no fixed type (an empty array literal) demands an
    annotation.
    """
    with pytest.raises(TypeError, match="cannot infer a type for 'a'"):
        compile_source("fn main() -> i32 { let a = []; return 0; }")


def test_unknown_call_initializer_names_the_function(compile_source):
    """
    'let x = f();' with no such f reports the function, not the inference.
    """
    with pytest.raises(NameError, match="undefined function 'ghost'"):
        compile_source("fn main() -> i32 { let x = ghost(); return 0; }")


def test_valueless_call_initializer_says_so(compile_source):
    """
    'let x = f();' on a void function blames the missing value.
    """
    with pytest.raises(TypeError, match="function 'f' returns no value"):
        compile_source("""
        fn f() { }
        fn main() -> i32 { let x = f(); return 0; }
        """)


def test_unknown_variable_initializer_names_the_variable(compile_source):
    """
    'let x = v;' with no such v reports the variable, not the inference.
    """
    with pytest.raises(NameError, match="undefined variable 'ghost'"):
        compile_source("fn main() -> i32 { let x = ghost; return 0; }")
