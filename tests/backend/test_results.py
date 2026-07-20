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


def test_ok_and_error_construct_results(run):
    """
    The builtin 'Ok'/'Error' constructors build Results, their type
    arguments driven by the expected type - a return, an annotated let,
    an argument - or spelled explicitly; the count picks the overload.
    """
    source = """
    fn divide(a: i32, b: i32) -> Result<i32, u8> {
        if (b == 0) { return Error(1); }
        return Ok(a / b);
    }

    fn validate(n: i32) -> Result<u8> {
        if (n < 0) { return Error(7); }
        return Ok();
    }

    fn take(r: Result<i32, u8>) -> i32 {
        return r.ok ? r.value : -1;
    }

    fn main() -> i32 {
        let good = divide(84, 2);
        if (not good.ok or good.value != 42) { return 1; }
        if (divide(1, 0).error != 1) { return 2; }

        if (not validate(3).ok or validate(-1).error != 7) { return 3; }

        let e = Error<i32, u8>(9);              // explicit spelling
        if (e.ok or e.error != 9) { return 4; }

        let o: Result<i64, u8> = Ok(41);        // target-driven let
        if (not o.ok or o.value != 41) { return 5; }

        if (take(Ok(5)) != 5) { return 6; }     // argument position
        if (take(Error(3)) != -1) { return 7; }
        return 0;
    }
    """
    assert run(source).returncode == 0


def test_a_contextless_ok_cannot_infer(compile_source):
    """
    Without an expected type or explicit arguments, E has no source.
    """
    with pytest.raises(TypeError, match="cannot infer type argument 'E' "
                                        "for generic function 'Ok'"):
        compile_source("""
        fn main() -> i32 { let x = Ok(5); return 0; }
        """)


def test_generic_functions_overload_by_arity(run):
    """
    Same-named generic functions with different type-parameter counts
    coexist; the call's shape picks the template.
    """
    source = """
    fn pair<A>(a: A) -> A { return a; }
    fn pair<A, B>(a: A, b: B) -> A { return a + (b as A); }

    fn main() -> i32 {
        return pair(40) + pair(1, 1 as i64) - 42;
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

    with pytest.raises(TypeError, match="function 'Ok' is declared more "
                                        "than once"):
        compile_source("""
        fn Ok<V, E>(v: V) -> Result<V, E> { return Ok(v); }
        fn main() -> i32 { return 0; }
        """)
