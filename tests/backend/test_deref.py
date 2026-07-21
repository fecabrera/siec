"""Feature tests for the '*' dereference operator."""

import pytest


def test_dereference_reads_through_a_pointer(run):
    """
    '*p' reads the value the pointer points at: 'p[0]' by another spelling.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 42;
        let p: i32* = &x;
        return *p;
    }
    """
    assert run(source).returncode == 42


def test_dereference_assignment_writes_through(run):
    """
    '*p = v' writes the storage the pointer points at.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 1;
        let p: i32* = &x;
        *p = 42;
        return x;
    }
    """
    assert run(source).returncode == 42


def test_compound_dereference_assignment(run):
    """
    '*p += v' reads and writes back through the pointer.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 40;
        let p: i32* = &x;
        *p += 2;
        return x;
    }
    """
    assert run(source).returncode == 42


def test_dereference_in_a_callee_mutates_the_caller(run):
    """
    A callee writing through '*p' mutates the caller's variable.
    """
    source = """
    fn bump(p: i32*) {
        *p += 1;
    }

    fn main() -> i32 {
        let x: i32 = 41;
        bump(&x);
        return x;
    }
    """
    assert run(source).returncode == 42


def test_dereference_of_a_double_pointer(run):
    """
    '**pp' peels both pointer levels, despite lexing as one token.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 42;
        let p: i32* = &x;
        let pp: i32** = &p;
        return **pp;
    }
    """
    assert run(source).returncode == 42


def test_prefix_dereference_beside_multiplication(run):
    """
    'x * *p' keeps the infix '*' a multiplication over p's element.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 6;
        let p: i32* = &x;
        return x * *p + 6; // 36 + 6
    }
    """
    assert run(source).returncode == 42


def test_dereferenced_struct_pointer_reads_fields(run):
    """
    '(*p).field' reaches into the struct the pointer points at.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let pt: Point = {40, 2};
        let p: Point* = &pt;
        return (*p).x + (*p).y;
    }
    """
    assert run(source).returncode == 42


def test_dereferenced_struct_pointer_writes_fields(run):
    """
    '(*p).field = v' writes the field inside the pointed-at struct.
    """
    source = """
    struct Point {
        x: i32;
        y: i32;
    }

    fn main() -> i32 {
        let pt: Point = {1, 2};
        let p: Point* = &pt;
        (*p).x = 40;
        return pt.x + pt.y;
    }
    """
    assert run(source).returncode == 42


def test_inferred_let_adopts_the_element_type(run):
    """
    'let v = *p' infers the pointee's type, not the pointer's.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 42;
        let p = &x;
        let v = *p;
        return v;
    }
    """
    assert run(source).returncode == 42


def test_dereference_keeps_the_element_signedness(run):
    """
    Dividing an unsigned element read through '*p' stays unsigned.
    """
    source = """
    fn main() -> i32 {
        let x: u8 = 130;
        let p: u8* = &x;
        return (*p / 2) as i32; // udiv: 65, where sdiv would see -126
    }
    """
    assert run(source).returncode == 65


def test_address_of_a_dereference_is_the_pointer(run):
    """
    '&*p' addresses the storage p points at: p's own value.
    """
    source = """
    fn main() -> i32 {
        let x: i32 = 1;
        let p: i32* = &x;
        let q: i32* = &*p;
        *q = 42;
        return x;
    }
    """
    assert run(source).returncode == 42


def test_volatile_stores_reach_through_a_dereference(compile_source):
    """
    A write into a '@volatile' struct through '(*p).field' stays volatile.
    """
    module = str(compile_source("""
    @volatile struct Reg {
        data: u32;
    }

    fn main() -> i32 {
        let r: Reg = { 1 };
        let p: Reg* = &r;
        (*p).data = 2;
        return 0;
    }
    """))
    assert module.count("store volatile") >= 2


def test_dereference_of_a_non_pointer_is_an_error(compile_source):
    """
    '*' demands a pointer operand, sharing indexing's contract.
    """
    with pytest.raises(TypeError, match="cannot index"):
        compile_source("fn main() -> i32 { let x: i32 = 1; return *x; }")


def test_write_through_a_const_pointer_is_an_error(compile_source):
    """
    '*p = v' through a 'const T*' breaks the const contract.
    """
    with pytest.raises(TypeError, match="cannot mutate"):
        compile_source("""
        fn zero(p: const i32*) {
            *p = 0;
        }

        fn main() -> i32 { return 0; }
        """)


def test_field_write_through_a_const_pointer_is_an_error(compile_source):
    """
    '(*p).field = v' keeps the const contract of the pointer it reads through.
    """
    with pytest.raises(TypeError, match="cannot mutate"):
        compile_source("""
        struct Point {
            x: i32;
        }

        fn zero(p: const Point*) {
            (*p).x = 0;
        }

        fn main() -> i32 { return 0; }
        """)
