"""Feature tests for function overloading: one name, distinct signatures."""

import pytest


def test_overloads_pick_by_argument_type(run):
    """
    Each call resolves to the overload its argument's declared type names.
    """
    source = """
    fn describe(n: i64) -> i32 { return 1; }
    fn describe(f: f64) -> i32 { return 2; }
    fn describe(s: char*) -> i32 { return 3; }

    fn main() -> i32 {
        let n: i64 = 0;
        let f: f64 = 0.0;
        let s: char* = "x";
        return describe(n) * 100 + describe(f) * 10 + describe(s); // 123
    }
    """
    assert run(source).returncode == 123


def test_exact_match_beats_widening(run):
    """
    An 'i32' argument picks the i32 overload even though it also widens to i64.
    """
    source = """
    fn pick(n: i32) -> i32 { return 1; }
    fn pick(n: i64) -> i32 { return 2; }

    fn main() -> i32 {
        let narrow: i32 = 0;
        let wide: i64 = 0;
        return pick(narrow) * 10 + pick(wide); // 12
    }
    """
    assert run(source).returncode == 12


def test_untyped_literal_ranks_at_its_default(run):
    """
    An integer literal ranks as i32 - exact into an i32 overload, widening
    into i64 - and a literal too big for i32 ranks as i64 directly.
    """
    source = """
    fn pick(n: i32) -> i32 { return 1; }
    fn pick(n: i64) -> i32 { return 2; }

    fn main() -> i32 {
        return pick(5) * 10 + pick(5000000000); // 12
    }
    """
    assert run(source).returncode == 12


def test_literal_widens_when_no_exact_overload_exists(run):
    """
    'dec.add(5)': with no i32 candidate, the literal's i32 widens to i64,
    never crossing into the unsigned or float candidates.
    """
    source = """
    struct Decimal { value: i64; }

    fn Decimal::add(&self, d: const &Decimal) -> i32 { return 1; }
    fn Decimal::add(&self, n: i64) -> i32 { return 2; }
    fn Decimal::add(&self, f: f64) -> i32 { return 3; }

    fn main() -> i32 {
        let dec: Decimal = {0};
        return dec.add(5);
    }
    """
    assert run(source).returncode == 2


def test_methods_overload_on_the_receiver_type(run):
    """
    Method overloads resolve like free functions, the receiver joining
    the arguments; a struct argument picks the reference candidate.
    """
    source = """
    struct Decimal { value: i64; }

    fn Decimal::add(&self, d: const &Decimal) -> i64 { return self.value + d.value; }
    fn Decimal::add(&self, n: i64) -> i64 { return self.value + n; }

    fn main() -> i32 {
        let a: Decimal = {40};
        let b: Decimal = {2};
        return (a.add(b) + a.add(0)) as i32 - 40; // 42 + 40 - 40
    }
    """
    assert run(source).returncode == 42


def test_unsigned_arguments_stay_in_their_prefix(run):
    """
    A u8 argument widens into the u64 overload, never the i64 one.
    """
    source = """
    fn pick(n: i64) -> i32 { return 1; }
    fn pick(n: u64) -> i32 { return 2; }

    fn main() -> i32 {
        let n: u8 = 5;
        return pick(n);
    }
    """
    assert run(source).returncode == 2


def test_overloads_pick_by_arity(run):
    """
    Overloads may differ in parameter count alone.
    """
    source = """
    fn pick() -> i32 { return 1; }
    fn pick(n: i32) -> i32 { return 2; }

    fn main() -> i32 {
        return pick() * 10 + pick(0); // 12
    }
    """
    assert run(source).returncode == 12


def test_return_types_may_differ_across_overloads(run):
    """
    Inference follows the picked overload's return type.
    """
    source = """
    fn half(n: i64) -> i64 { return n / 2; }
    fn half(f: f64) -> f64 { return f / 2.0; }

    fn main() -> i32 {
        let n = half(84 as i64);   // i64
        let f = half(85.0);        // f64
        return (n + f as i64) as i32 - 42; // 42 + 42 - 42
    }
    """
    assert run(source).returncode == 42


def test_ambiguous_conversions_are_an_error(compile_source):
    """
    An argument widening into two candidates alike has no winner.
    """
    with pytest.raises(TypeError, match="ambiguous"):
        compile_source("""
        fn pick(n: i16) -> i32 { return 1; }
        fn pick(n: i64) -> i32 { return 2; }

        fn main() -> i32 {
            let n: i8 = 0;
            return pick(n);
        }
        """)


def test_no_matching_overload_is_an_error(compile_source):
    """
    An argument no candidate takes names the types it offered.
    """
    with pytest.raises(TypeError, match="no overload of 'pick' takes"):
        compile_source("""
        fn pick(n: i64) -> i32 { return 1; }
        fn pick(f: f64) -> i32 { return 2; }

        fn main() -> i32 {
            return pick(true);
        }
        """)


def test_same_signature_twice_is_still_a_conflict(compile_source):
    """
    Overloading needs distinct parameter lists; repeating one is the same
    redefinition error as ever.
    """
    with pytest.raises(TypeError, match="defined more than once"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        fn f(n: i32) -> i32 { return 2; }

        fn main() -> i32 { return 0; }
        """)


def test_return_type_alone_cannot_overload(compile_source):
    """
    Two signatures differing only in return type give calls nothing to
    pick by.
    """
    with pytest.raises(TypeError, match="conflicting declarations"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        fn f(n: i32) -> i64 { return 2; }

        fn main() -> i32 { return 0; }
        """)


def test_extern_functions_cannot_overload(compile_source):
    """
    An '@extern' function names one foreign symbol.
    """
    with pytest.raises(TypeError, match="cannot overload '@extern'"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        @extern fn f(s: char*) -> i32;

        fn main() -> i32 { return 0; }
        """)


def test_bare_reference_to_an_overloaded_name_is_ambiguous(compile_source):
    """
    A bare reference has no arguments to pick a candidate by.
    """
    with pytest.raises(TypeError, match="ambiguous reference"):
        compile_source("""
        fn f(n: i32) -> i32 { return 1; }
        fn f(n: i64) -> i32 { return 2; }

        fn main() -> i32 {
            let g = f;
            return 0;
        }
        """)


def test_forward_declared_overloads_define_later(run):
    """
    Each forward declaration pairs with the definition sharing its
    signature, whichever order they appear in.
    """
    source = """
    fn pick(n: i64) -> i32;
    fn pick(f: f64) -> i32;

    fn main() -> i32 {
        let n: i64 = 0;
        return pick(n) * 10 + pick(0.0); // 12
    }

    fn pick(n: i64) -> i32 { return 1; }
    fn pick(f: f64) -> i32 { return 2; }
    """
    assert run(source).returncode == 12
