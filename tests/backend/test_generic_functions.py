"""Feature tests for generic functions: 'fn f<T>' and its calls."""

import pytest


def test_type_arguments_infer_from_the_value_arguments(run):
    """
    'f(x)' binds each type parameter by unifying parameter patterns with
    argument types: bare T, pointer patterns, and literal defaults alike.
    """
    source = """
    fn identity<T>(t: T) -> T { return t; }

    fn first<T>(items: T*, count: u64) -> T { return items[0]; }

    fn pick<T>(a: T, b: T, take_first: bool) -> T {
        if (take_first) { return a; }
        return b;
    }

    fn main() -> i32 {
        let n: i32 = 30;
        let raw: @raw<i32>[2];
        raw[0] = 9;

        return identity(n) + first(&raw[0], 2) + pick(3, 7, true); // 30+9+3
    }
    """
    assert run(source).returncode == 42


def test_explicit_type_arguments_and_generic_returns(run):
    """
    'f<i64>(x)' spells the arguments, and a generic return type may name
    a generic struct instantiation.
    """
    source = """
    struct Box<T> { value: T; }

    fn make_box<T>(t: T) -> Box<T> {
        let b: Box<T> = { t };
        return b;
    }

    fn identity<T>(t: T) -> T { return t; }

    fn main() -> i32 {
        let boxed = make_box(40 as i64);
        return boxed.value as i32 + identity<i32>(2);
    }
    """
    assert run(source).returncode == 42


def test_type_like_bounds_accept_their_canonical_types(run):
    """
    Intrinsics, aliases, and structs are concrete bounds: aliases
    canonicalize, and each accepts exactly that resulting type.
    """
    source = """
    struct Key { value: i32; }
    @type Word = u64;
    @type KeyAlias = Key;

    fn word<T: Word>(value: T) -> T { return value; }
    fn key<T: KeyAlias>(value: T) -> T { return value; }

    fn main() -> i32 {
        let k: Key = { 2 };
        return word<u64>(40) as i32 + key(k).value;
    }
    """
    assert run(source).returncode == 42


def test_interface_bound_accepts_an_implementer(run):
    """
    An interface bound accepts a concrete type carrying its nominal claim.
    """
    source = """
    interface Hashable;

    struct Key: Hashable { value: i32; }

    fn hash<K: Hashable>(key: K) -> i32 {
        return key.value;
    }

    fn main() -> i32 {
        let key: Key = { 42 };
        return hash(key);
    }
    """
    assert run(source).returncode == 42


def test_intersection_bounds_support_direct_and_template_forms(
        run, compile_source):
    """Functions and receiver families require every explicit `&` bound."""
    source = """
    interface I1;
    interface I2;

    struct Both: I1, I2 {}
    struct One: I1 {}

    fn direct<T: I1 & I2>(value: T) -> i32 { return 10; }

    @where<T: I1 & I2>
    fn decorated<T>(value: T) -> i32 { return 10; }

    @where<T: I1 & I2>
    fn T::separate(const &self) -> i32 { return 6; }

    @where<T: I1 & I2> {
        fn grouped<T>(value: T) -> i32 { return 10; }
        fn T::grouped_method(const &self) -> i32 { return 6; }
    }

    fn main() -> i32 {
        let value: Both = {};
        return direct(value) + decorated(value) + grouped(value)
            + value.separate() + value.grouped_method();
    }
    """
    assert run(source).returncode == 42

    with pytest.raises(TypeError, match="does not implement interface 'I2'"):
        compile_source(source.replace(
            "let value: Both = {};",
            "let value: One = {};",
        ))


def test_intersection_is_more_specific_than_a_single_bound(run):
    """Each member of an intersection contributes overload specificity."""
    source = """
    interface I1;
    interface I2;
    struct Both: I1, I2 {}
    struct One: I1 {}

    fn pick<T>(value: T) -> i32 { return 1; }
    fn pick<T: I1>(value: T) -> i32 { return 2; }
    fn pick<T: I1 & I2>(value: T) -> i32 { return 42; }

    fn main() -> i32 {
        let both: Both = {};
        let one: One = {};
        return pick(both) + pick(one) - 2;
    }
    """
    assert run(source).returncode == 42


def test_interface_bound_infers_its_free_type_arguments(run):
    """
    A concrete claim supplies generic arguments named only inside another
    parameter's bound.
    """
    source = """
    interface Holds<T>;
    struct Key: Holds<i32> { value: i32; }

    fn size<T, U: Holds<T>>(value: U) -> u64 {
        return @sizeof(T);
    }

    fn main() -> i32 {
        let key: Key;
        return size(key) as i32 + 38;
    }
    """
    assert run(source).returncode == 42


def test_bounds_reject_nonmatching_function_arguments(compile_source):
    """
    Inferred type arguments are checked against concrete and interface
    bounds before a function instance is declared.
    """
    with pytest.raises(TypeError, match="type 'i32' does not satisfy bound 'u64'"):
        compile_source("""
        fn only_words<T: u64>(value: T) {}
        fn main() -> i32 { only_words(1); return 0; }
        """)

    with pytest.raises(TypeError, match="type 'Key' does not implement "
                                        "interface 'Hashable'"):
        compile_source("""
        interface Hashable;
        struct Key { value: i32; }
        fn hash<K: Hashable>(key: K) {}
        fn main() -> i32 { let key: Key; hash(key); return 0; }
        """)


def test_a_bounded_overload_beats_an_unbounded_one(run):
    """
    Otherwise identical templates may differ by bounds, and the bounded
    candidate is more specific when both accept a call.
    """
    source = """
    interface Hashable;
    struct Key: Hashable { value: i32; }

    fn pick<T>(value: T) -> i32 { return 1; }
    fn pick<T: Hashable>(value: T) -> i32 { return 42; }

    fn main() -> i32 {
        let key: Key = { 0 };
        return pick(key);
    }
    """
    assert run(source).returncode == 42


def test_bounded_receiver_methods_overload_by_constraints(run):
    """
    Bare 'T::m' families may repeat a signature under different receiver
    bounds, like free-function templates distinguished by constraints.
    """
    source = """
    interface Left;
    interface Right;
    struct L: Left {}
    struct R: Right {}

    @where<T: Left>
    fn T::tag(const &self) -> i32 { return 40; }

    @where<T: Right>
    fn T::tag(const &self) -> i32 { return 2; }

    fn main() -> i32 {
        let left: L = {};
        let right: R = {};
        return left.tag() + right.tag();
    }
    """
    assert run(source).returncode == 42


def test_generic_functions_recurse_and_call_each_other(run):
    """
    An instance may call itself, and one generic may instantiate another.
    """
    source = """
    fn fact<T>(n: T) -> T {
        if (n <= 1) { return 1; }
        return n * fact(n - 1 as T);
    }

    fn identity<T>(t: T) -> T { return t; }

    fn twice<T>(t: T) -> T {
        return identity(t) + identity(t);
    }

    fn main() -> i32 {
        return (fact(5 as i64) - 80 as i64) as i32 + twice(1 as i32); // 40 + 2
    }
    """
    assert run(source).returncode == 42


def test_comparisons_still_parse_around_generic_calls(run):
    """
    A '<' that isn't a generic call's argument list stays a comparison,
    including glued '>>' shifts.
    """
    source = """
    fn f(a: i32, b: i32) -> i32 {
        if (a < b and b > a) {
            return b >> 1;
        }
        return 0;
    }

    fn main() -> i32 { return f(1, 84); }
    """
    assert run(source).returncode == 42


def test_explicit_arguments_on_a_plain_function_are_rejected(compile_source):
    """
    Type arguments belong to generic functions alone.
    """
    with pytest.raises(TypeError, match="function 'plain' is not generic"):
        compile_source("""
        fn plain(a: i32) -> i32 { return a; }
        fn main() -> i32 { return plain<i32>(1); }
        """)


def test_conflicting_inference_is_reported(compile_source):
    """
    Two arguments demanding different bindings for one parameter conflict.
    """
    with pytest.raises(TypeError, match="conflicting type arguments for 'T': "
                                        "'i32' and 'i64'"):
        compile_source("""
        fn same<T>(a: T, b: T) -> T { return a; }
        fn main() -> i32 {
            let x: i32 = 1;
            let y: i64 = 2;
            return same(x, y) as i32;
        }
        """)


def test_uninferable_arguments_ask_for_explicit_spelling(compile_source):
    """
    A type parameter no argument pins down points at the explicit form.
    """
    with pytest.raises(TypeError, match="cannot infer type argument 'T' for "
                                        "generic function 'empty'"):
        compile_source("""
        fn empty<T>() -> T* { return null; }
        fn main() -> i32 { let p = empty(); return 0; }
        """)


def test_invalid_argument_precedes_generic_inference_failure(compile_source):
    """An unresolved argument is not blamed on the type parameter it leaves."""
    with pytest.raises(NameError, match="undefined variable 'missing'"):
        compile_source("""
        fn consume<T>(value: T) -> i32 { return 0; }
        fn main() -> i32 { return consume(missing); }
        """)


def test_uninstantiated_template_costs_nothing(run):
    """
    A template no one calls declares nothing and emits nothing.
    """
    source = """
    fn unused<T>(t: T) -> T { return t; }

    fn main() -> i32 { return 42; }
    """
    assert run(source).returncode == 42


def test_references_to_generic_instances(run):
    """
    'identity<i32>' outside a call is a function value, and a bare
    generic name adopts a function-typed context by unifying signatures.
    """
    source = """
    @type unary<T> = fn(T) -> T;

    fn identity<T>(t: T) -> T { return t; }
    fn negate<T>(t: T) -> T { return 0 as T - t; }

    fn apply(f: unary<i32>, n: i32) -> i32 { return f(n); }

    fn main() -> i32 {
        let g = identity<i32>;
        let table: fn(i64) -> i64 = negate;

        return apply(identity, 40)     // bare, unified from unary<i32>
            + apply(identity<i32>, 2)  // explicit, in argument position
            + g(21) - 21
            + (table(5) + 5 as i64) as i32;
    }
    """
    assert run(source).returncode == 42


def test_reference_signature_mismatch_is_reported(compile_source):
    """
    A target whose shape cannot fit the template names the problem.
    """
    with pytest.raises(TypeError, match="cannot bind generic function 'pair' "
                                        "to 'fn\\(i32\\)->i32': it takes 2 "
                                        "parameters"):
        compile_source("""
        fn pair<T>(a: T, b: T) -> T { return a; }
        fn main() -> i32 {
            let f: fn(i32) -> i32 = pair;
            return 0;
        }
        """)


def test_bare_generic_name_without_context_is_reported(compile_source):
    """
    A bare template bound to nothing cannot pick its arguments.
    """
    with pytest.raises(TypeError, match="cannot infer type arguments for "
                                        "generic function 'identity'"):
        compile_source("""
        fn identity<T>(t: T) -> T { return t; }
        fn main() -> i32 { let f = identity; return 0; }
        """)
