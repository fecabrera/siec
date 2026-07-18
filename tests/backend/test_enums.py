"""Feature tests for enum declarations and 'A::member' access."""

import pytest


def test_automatic_values_start_at_one(run):
    """
    Members count from 1; an explicit value resets the counter.
    """
    source = """
    enum E {
        A,      // = 1
        B = 5,
        C,      // = 6
    }

    fn main() -> i32 {
        return (E::A + E::B + E::C) as i32; // 12
    }
    """
    assert run(source).returncode == 12


def test_member_values_may_reference_members(run):
    """
    A value expression can combine earlier members, C-style.
    """
    source = """
    enum Flags: u8 {
        INF = 2,
        NAN = 4,
        SNAN = 8,
        SPECIAL = Flags::INF | Flags::NAN | Flags::SNAN,
    }

    fn main() -> i32 {
        return Flags::SPECIAL as i32; // 14
    }
    """
    assert run(source).returncode == 14


def test_enum_as_a_type(run):
    """
    An enum types variables and parameters, compared by value.
    """
    source = """
    enum Color { RED, GREEN, BLUE }

    fn is_green(c: Color) -> i32 {
        if (c == Color::GREEN) {
            return 42;
        }
        return 0;
    }

    fn main() -> i32 {
        let c: Color = Color::GREEN;
        return is_green(c);
    }
    """
    assert run(source).returncode == 42


def test_enum_struct_field(run):
    """
    A struct field may be enum-typed; reads and writes go by value.
    """
    source = """
    enum Color { RED, GREEN, BLUE }

    struct Pixel {
        color: Color;
    }

    fn main() -> i32 {
        let p: Pixel = {Color::BLUE};
        p.color = Color::GREEN;
        return p.color as i32; // 2
    }
    """
    assert run(source).returncode == 2


def test_constants_hold_enum_members(run):
    """
    An '@const' may name an enum member, and an enum value an '@const'.
    """
    source = """
    @const BASE = 40;

    enum E {
        A = BASE + 2,
    }

    @const DEFAULT = E::A;

    fn main() -> i32 {
        return DEFAULT as i32;
    }
    """
    assert run(source).returncode == 42


def test_unsigned_backing_compares_unsigned(run):
    """
    A u32-backed enum's comparisons use its backing signedness.
    """
    source = """
    enum Status: u32 {
        OK = 1,
        OVERFLOW = 0x8000,
    }

    fn main() -> i32 {
        if (Status::OVERFLOW > Status::OK) {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_unknown_member_is_an_error(compile_source):
    """
    Naming a member the enum does not declare is rejected.
    """
    with pytest.raises(TypeError, match="enum 'E' has no member 'B'"):
        compile_source("""
        enum E { A }
        fn main() -> i32 { return E::B as i32; }
        """)


def test_unknown_enum_is_an_error(compile_source):
    """
    '::' through an undeclared enum name is rejected.
    """
    with pytest.raises(NameError, match="undefined enum 'Nope'"):
        compile_source("fn main() -> i32 { return Nope::A as i32; }")


def test_duplicate_member_is_an_error(compile_source):
    """
    A member declared twice in one enum is rejected.
    """
    with pytest.raises(TypeError, match="declares member 'A' more than once"):
        compile_source("enum E { A, A }")


def test_non_integer_backing_is_an_error(compile_source):
    """
    The backing type must be an integer type.
    """
    with pytest.raises(TypeError, match="integer backing type"):
        compile_source("enum E: f32 { A }")


def test_non_constant_value_is_an_error(compile_source):
    """
    Member values must be compile-time constant integer expressions.
    """
    with pytest.raises(TypeError, match="constant integer expressions"):
        compile_source("""
        fn f() -> i32 { return 1; }
        enum E { A = f() }
        """)
