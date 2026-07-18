"""Feature tests for the 'const T' contract modifier."""

import pytest


def test_mutable_passes_as_const(run):
    """
    A mutable T passes freely where a const T is expected.
    """
    source = """
    fn first(s: const char*) -> char {
        return s[0];
    }

    fn main() -> i32 {
        let msg: char* = "*ok";
        if (first(msg) == "*"[0]) {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_const_scalar_copies_out(run):
    """
    A copy of a non-aliasing const value is an independent, mutable value.
    """
    source = """
    fn scale(n: const i32) -> i32 {
        let m = n;    // the copy is mutable
        m += n;
        return m;
    }

    fn main() -> i32 {
        return scale(21);
    }
    """
    assert run(source).returncode == 42


def test_const_pointer_cannot_pass_as_mutable(compile_source):
    """
    An aliasing const value never passes where a mutable one is expected.
    """
    with pytest.raises(TypeError, match="cannot use a 'const char\\*'"):
        compile_source("""
        fn take(s: char*) {}
        fn f(s: const char*) { take(s); }
        """)


def test_const_variable_cannot_be_assigned(compile_source):
    """
    A const parameter or variable cannot be reassigned.
    """
    with pytest.raises(TypeError, match="cannot assign to const variable 'n'"):
        compile_source("fn f(n: const i32) { n = 5; }")

    with pytest.raises(TypeError, match="cannot assign to const variable 'x'"):
        compile_source("fn main() -> i32 { let x: const i32 = 1; x = 2; return x; }")


def test_cannot_write_through_a_const_pointer(compile_source):
    """
    Index assignment through a const pointer is a mutation and is rejected.
    """
    with pytest.raises(TypeError, match="cannot mutate a 'const char\\*'"):
        compile_source("""fn f(s: const char*) { s[0] = "x"[0]; }""")


def test_cannot_mutate_a_const_struct(compile_source):
    """
    Member assignment on a const struct is rejected.
    """
    with pytest.raises(TypeError, match="cannot mutate a 'const P'"):
        compile_source("""
        struct P { x: i32; }
        fn f(p: const P) { p.x = 1; }
        """)


def test_aliasing_fields_keep_the_contract(compile_source):
    """
    A pointer field read from a const struct is itself const.
    """
    with pytest.raises(TypeError, match="cannot mutate a 'const char\\*'"):
        compile_source("""
        struct B { data: char*; }
        fn f(b: const B) { b.data[0] = "x"[0]; }
        """)


def test_inference_keeps_the_contract(compile_source):
    """
    'let t = s;' on a const pointer stays const: no laundering through a copy.
    """
    with pytest.raises(TypeError, match="cannot use a 'const char\\*'"):
        compile_source("""
        fn take(s: char*) {}
        fn f(s: const char*) { let t = s; take(t); }
        """)


def test_const_field_cannot_be_assigned(compile_source):
    """
    A field declared const cannot be assigned, even on a mutable struct.
    """
    with pytest.raises(TypeError, match="cannot assign to const field 'dot'"):
        compile_source("""
        struct S { dot: const char*; }
        fn f(s: S) { s.dot = "x"; }
        """)


def test_explicit_cast_sheds_the_contract(run):
    """
    's as char*' is the escape hatch back to a mutable pointer.
    """
    source = """
    fn head(s: char*) -> char {
        return s[0];
    }

    fn f(s: const char*) -> char {
        return head(s as char*);
    }

    fn main() -> i32 {
        if (f("*") == "*"[0]) {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42


def test_main_args_may_be_const(run):
    """
    'fn main(args: const char*[])' keeps the argc/argv entry form.
    """
    source = """
    fn main(args: const char*[]) -> i32 {
        return args.length as i32;
    }
    """
    assert run(source, "a", "b").returncode == 3


def test_const_main_args_cannot_be_mutated(compile_source):
    """
    The const marking on args is enforced like any other.
    """
    with pytest.raises(TypeError, match="cannot mutate a 'const char\\*\\[\\]'"):
        compile_source("""
        fn main(args: const char*[]) -> i32 {
            args[0] = "x";
            return 0;
        }
        """)


def test_const_return_type(run):
    """
    A function may return a const pointer, which reads fine but stays const.
    """
    source = """
    fn name() -> const char* {
        return "sie";
    }

    fn main() -> i32 {
        let n = name();
        if (n[0] == "s"[0]) {
            return 42;
        }
        return 0;
    }
    """
    assert run(source).returncode == 42
