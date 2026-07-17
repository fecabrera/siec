"""Feature tests for compile-time errors raised while lowering source to a module."""

import pytest


def test_unknown_type(compile_source):
    """
    Naming a type that doesn't exist is an error.
    """
    with pytest.raises(TypeError, match="unknown type 'wat'"):
        compile_source("fn main() -> i32 { let x: wat = 0; return 0; }")


def test_undefined_variable(compile_source):
    """
    Reading a variable that was never declared is an error.
    """
    with pytest.raises(NameError, match="undefined variable 'x'"):
        compile_source("fn main() -> i32 { return x; }")


def test_undefined_function(compile_source):
    """
    Calling a function that was never declared is an error.
    """
    with pytest.raises(NameError, match="undefined function 'g'"):
        compile_source("fn main() -> i32 { return g(); }")


def test_too_few_arguments(compile_source):
    """
    Calling with fewer arguments than parameters is an error.
    """
    source = "fn f(a: i32) { } fn main() -> i32 { f(); return 0; }"
    with pytest.raises(TypeError, match="too few arguments"):
        compile_source(source)


def test_too_many_arguments(compile_source):
    """
    Calling a non-vararg function with extra arguments is an error.
    """
    source = "fn f() { } fn main() -> i32 { f(1); return 0; }"
    with pytest.raises(TypeError, match="too many arguments"):
        compile_source(source)


def test_mixed_signedness(compile_source):
    """
    Combining a signed and an unsigned operand is an error.
    """
    source = """
    fn main() -> i32 {
        let s: i32 = 1;
        let u: u32 = 2;
        if (s < u) {
            return 1;
        }
        return 0;
    }
    """
    with pytest.raises(TypeError, match="signed and unsigned"):
        compile_source(source)


def test_unknown_struct_field(compile_source):
    """
    Accessing a field the struct does not declare is an error.
    """
    source = """
    struct Point { x: i32; }
    fn main() -> i32 {
        let p: Point;
        return p.z;
    }
    """
    with pytest.raises(TypeError, match="has no field 'z'"):
        compile_source(source)


def test_member_on_non_struct(compile_source):
    """
    Selecting a field from a non-struct value is an error.
    """
    source = """
    fn main() -> i32 {
        let n: i32 = 0;
        return n.x;
    }
    """
    with pytest.raises(TypeError, match="non-struct type"):
        compile_source(source)


def test_aggregate_element_count_mismatch(compile_source):
    """
    An aggregate literal with the wrong number of elements is an error.
    """
    source = """
    struct Pair { a: i32; b: i32; }
    fn main() -> i32 {
        let p: Pair = {1};
        return p.a;
    }
    """
    with pytest.raises(TypeError, match="expected 2"):
        compile_source(source)


def test_aggregate_without_an_aggregate_type(compile_source):
    """
    An aggregate literal used where a scalar is expected is an error.
    """
    with pytest.raises(TypeError, match="needs a struct or array type"):
        compile_source("fn main() -> i32 { let x: i32 = {1}; return x; }")


def test_duplicate_struct(compile_source):
    """
    Declaring two structs with the same name is an error.
    """
    source = "struct S { x: i32; } struct S { y: i32; } fn main() -> i32 { return 0; }"
    with pytest.raises(TypeError, match="declared more than once"):
        compile_source(source)


def test_function_defined_twice(compile_source):
    """
    Defining the same function twice is an error.
    """
    source = "fn f() { } fn f() { } fn main() -> i32 { return 0; }"
    with pytest.raises(TypeError, match="defined more than once"):
        compile_source(source)


def test_conflicting_declarations(compile_source):
    """
    Redeclaring a function with a different signature is an error.
    """
    source = "fn f() -> i32; fn f() -> i8; fn main() -> i32 { return 0; }"
    with pytest.raises(TypeError, match="conflicting declarations"):
        compile_source(source)


def test_missing_return_value(compile_source):
    """
    A non-void function that falls off its end is an error.
    """
    with pytest.raises(TypeError, match="must return a value"):
        compile_source("fn main() -> i32 { }")


def test_extern_with_a_body(compile_source):
    """
    An extern function cannot carry a body.
    """
    with pytest.raises(SyntaxError, match="cannot have a body"):
        compile_source("@extern fn f() { } fn main() -> i32 { return 0; }")


def test_invalid_assignment_target(compile_source):
    """
    Assigning to something that isn't a variable or field is a parse error.
    """
    source = "fn f() { } fn main() -> i32 { f() = 5; return 0; }"
    with pytest.raises(SyntaxError, match="invalid assignment target"):
        compile_source(source)


def test_unterminated_string_literal(compile_source):
    """
    A string literal with no closing quote is a lexer error.
    """
    with pytest.raises(SyntaxError, match="unterminated string"):
        compile_source('fn main() -> i32 { puts("oops); return 0; }')
