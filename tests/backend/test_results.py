"""Feature tests for the builtin 'Result<V, E>' and 'Result<E>' structs."""

import pytest


def test_result_holds_a_value_or_an_error(run):
    """
    'Result<V, E>' is visible everywhere unimported: 'ok' tags which of
    the overlapping 'value' and 'error' members holds.
    """
    source = """
    fn divide(a: i32, b: i32) -> Result<i32, u8> {
        let r: Result<i32, u8>;
        if (b == 0) {
            r.ok = false;
            r.error = 1;
            return r;
        }
        r.ok = true;
        r.value = a / b;
        return r;
    }

    fn main() -> i32 {
        let good = divide(84, 2);
        if (not good.ok or good.value != 42) { return 1; }

        let bad = divide(1, 0);
        if (bad.ok or bad.error != 1) { return 2; }

        // value and error share storage: bool + padding + union
        if (sizeof(Result<i64, u8>) != 16) { return 3; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_result_with_only_an_error(run):
    """
    'Result<E>' carries just the tag and an error: the arity picks the
    template.
    """
    source = """
    fn check(n: i32) -> Result<u8> {
        let r: Result<u8>;
        r.ok = n >= 0;
        if (n < 0) { r.error = 7; }
        return r;
    }

    fn main() -> i32 {
        if (check(3).ok and check(-5).error == 7) { return 0; }
        return 1;
    }
    """
    assert run(source).returncode == 0


def test_result_flows_through_generics_and_methods(run):
    """
    'Result<T, E>' substitutes through generic functions and comes back
    from methods like any struct value.
    """
    source = """
    fn wrap<T>(v: T) -> Result<T, u8> {
        let r: Result<T, u8>;
        r.ok = true;
        r.value = v;
        return r;
    }

    struct Parser { at: i32; }
    fn Parser::init(self: &Parser) { self.at = 0; }
    fn Parser::next(self: &Parser) -> Result<i32, u8> {
        self.at += 1;
        return wrap(self.at);
    }

    fn main() -> i32 {
        let r = wrap(41 as i64);
        if (not r.ok or r.value != 41) { return 1; }

        let p = Parser();
        if (p.next().value != 1 or p.next().value != 2) { return 2; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_result_cannot_be_redeclared(compile_source):
    """
    'Result' is builtin: a user template under the name collides.
    """
    with pytest.raises(TypeError, match="struct 'Result' is declared more "
                                        "than once"):
        compile_source("""
        struct Result<A, B> { a: A; b: B; }
        fn main() -> i32 { return 0; }
        """)
